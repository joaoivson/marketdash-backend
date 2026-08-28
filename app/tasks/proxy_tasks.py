"""
Sonda de saúde do pool de proxies (plano §2.7).

Um proxy morto não avisa: a sessão simplesmente para de mandar e o motor vê
`timeout`. A sonda existe para que "o IP caiu" seja um FATO conhecido antes de
virar lote falhado — e para que a decisão de tirar um IP de circulação seja de
uma escada medida, não de um palpite no meio do envio.

Escada (config): 2 falhas seguidas → `degradado`; 4 → `quarentena`. O pool não
escolhe proxy fora de `ok`, então a quarentena já impede novas alocações; a
realocação dos chips que estão nele é o passo seguinte — e ele mexe numa sessão
pareada, por isso é gated (ver `WHATSAPP_PROXY_APLICAR_AUTOMATICO`).

Prioridade 9 (batch). NUNCA um valor intermediário: só as pontas 0 e 9 são
consumidas neste ambiente — priority=5 é aceita e nunca executa, em silêncio.
"""
import logging
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import httpx

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _url_do_proxy(proxy, credenciais: Dict[str, Any]) -> str:
    """`http://user:senha@host:porta` para o httpx.

    A senha é URL-encoded: senha de proxy com `@` ou `/` (comum em provedor
    residencial) montaria uma URL que aponta para outro host.
    """
    usuario = credenciais.get("username")
    senha = credenciais.get("password")
    if usuario:
        credencial = quote(str(usuario), safe="")
        if senha:
            credencial += ":" + quote(str(senha), safe="")
        return f"http://{credencial}@{proxy.servidor}"
    return f"http://{proxy.servidor}"


def _cliente_http(url_proxy: str, timeout: float, transport=None) -> httpx.Client:
    """httpx 0.28 removeu `proxies=` em favor de `proxy=`. O fallback mantém o
    código vivo se o ambiente subir com uma versão anterior (requirements diz
    apenas `httpx>=0.27`)."""
    if transport is not None:
        return httpx.Client(timeout=timeout, transport=transport)
    try:
        return httpx.Client(timeout=timeout, proxy=url_proxy)
    except TypeError:  # httpx < 0.28
        return httpx.Client(timeout=timeout, proxies=url_proxy)


def verificar_proxy(db, proxy, transport=None) -> Tuple[bool, Optional[str], Optional[str], str]:
    """
    Bate num eco de IP ATRAVÉS do proxy.

    Devolve `(ok, ip, pais, detalhe)`. O IP devolvido ser diferente do host do
    proxy é NORMAL em residencial rotativo — o que interessa é o proxy responder
    e o país continuar sendo o esperado (um chip brasileiro saindo da Alemanha é
    exatamente o retrato que queremos evitar).
    """
    from app.core.config import settings
    from app.services import proxy_pool_service

    try:
        credenciais = proxy_pool_service.credenciais(proxy) or {}
    except Exception as e:  # noqa: BLE001 — credencial nunca vai para o texto
        return False, None, None, f"credencial ilegível ({type(e).__name__})"

    url_proxy = _url_do_proxy(proxy, credenciais)
    timeout = settings.WHATSAPP_PROXY_HEALTH_TIMEOUT_S
    try:
        with _cliente_http(url_proxy, timeout, transport) as cliente:
            r = cliente.get(settings.WHATSAPP_PROXY_HEALTH_URL)
            if r.status_code >= 400:
                return False, None, None, f"eco de IP respondeu {r.status_code}"
            ip = (r.text or "").strip()[:64]
            pais = None
            if settings.WHATSAPP_PROXY_GEO_URL and ip:
                try:
                    g = cliente.get(settings.WHATSAPP_PROXY_GEO_URL.format(ip=ip))
                    if g.status_code < 400:
                        pais = (g.text or "").strip()[:8]
                except httpx.HTTPError:
                    pais = None       # geolocalização é extra, não veredito
            return True, ip, pais, "ok"
    except httpx.HTTPError as e:
        # `str(e)` de erro de proxy NÃO embute a credencial (httpx mascara a
        # URL), mas cortamos assim mesmo — detalhe longo aqui vira log grande
        # e o que importa é o tipo.
        return False, None, None, f"{type(e).__name__}: {str(e)[:120]}"


def _chips_em_quarentena(db, proxy) -> int:
    """Realoca (no banco) os chips de um proxy em quarentena.

    Aplicar o IP novo NA SESSÃO é outra coisa: mexe numa sessão pareada e não
    está confirmado se o WAHA pede novo QR (spike §1). Por isso o padrão é só
    reservar o IP novo e ALERTAR — o admin aplica pela tela, sabendo o risco.
    """
    from app.core.config import settings
    from app.repositories.whatsapp_proxy_repository import WhatsappProxyRepository
    from app.services import proxy_pool_service
    from app.services.whatsapp_instancia_service import (
        EnvioEmAndamento, aplicar_proxy_na_sessao,
    )
    from app.services.waha_client import ErroWhatsapp

    realocados = 0
    for instancia in WhatsappProxyRepository(db).instancias_do_proxy(proxy.id):
        try:
            novo = proxy_pool_service.realocar(
                db, instancia, motivo=f"quarentena do proxy {proxy.id}",
                ignorar_cooldown=True,
            )
        except Exception:
            db.rollback()
            logger.exception("Realocação falhou para a sessão %s",
                             instancia.nome_instancia)
            continue
        if novo is None:
            logger.error("Sessão %s presa em proxy em quarentena: pool sem "
                         "destino", instancia.nome_instancia)
            continue
        db.commit()
        realocados += 1
        if not settings.WHATSAPP_PROXY_APLICAR_AUTOMATICO:
            logger.error(
                "Sessão %s realocada para o proxy %s NO BANCO — a sessão segue "
                "saindo pelo IP antigo até alguém aplicar pela tela do admin",
                instancia.nome_instancia, novo.id,
            )
            continue
        try:
            aplicar_proxy_na_sessao(db, instancia)
        except (EnvioEmAndamento, ErroWhatsapp) as e:
            logger.warning("Proxy novo não aplicado na sessão %s: %s",
                           instancia.nome_instancia, e)
    return realocados


def rodar_verificacao(apenas_proxy_id: Optional[int] = None) -> Dict[str, Any]:
    """Verifica o pool inteiro (ou um proxy só) e registra em `sync_runs`."""
    from app.db.session import SessionLocal
    from app.models.whatsapp_proxies import PROXY_QUARENTENA
    from app.repositories.sync_run_repository import SyncRunRepository
    from app.repositories.whatsapp_proxy_repository import WhatsappProxyRepository
    from app.services import proxy_pool_service

    db = SessionLocal()
    resumo = {"verificados": 0, "ok": 0, "falhas": 0, "quarentena": 0,
              "realocados": 0, "fora_do_pais": []}
    run_id = None
    try:
        repo = WhatsappProxyRepository(db)
        sync_repo = SyncRunRepository(db)
        run_id = sync_repo.create(source="proxy_health", trigger="cron")
        proxies = repo.listar(ativos_apenas=True)
        if apenas_proxy_id is not None:
            proxies = [p for p in proxies if p.id == apenas_proxy_id]
        for proxy in proxies:
            resumo["verificados"] += 1
            ok, ip, pais, detalhe = verificar_proxy(db, proxy)
            if ok:
                proxy_pool_service.registrar_sucesso(db, proxy, ip=ip, pais=pais)
                resumo["ok"] += 1
                if pais and proxy.pais and pais.upper() != proxy.pais.upper():
                    # IP diferente do host é normal em residencial rotativo;
                    # PAÍS diferente não é — é o retrato que queremos evitar.
                    resumo["fora_do_pais"].append(
                        {"proxy_id": proxy.id, "esperado": proxy.pais, "visto": pais}
                    )
                    logger.error("Proxy %s saiu de %s: agora responde de %s",
                                 proxy.id, proxy.pais, pais)
                continue
            resumo["falhas"] += 1
            status = proxy_pool_service.registrar_falha(db, proxy, detalhe)
            if status == PROXY_QUARENTENA:
                resumo["quarentena"] += 1
                resumo["realocados"] += _chips_em_quarentena(db, proxy)
        sync_repo.mark_success(run_id, records_fetched=resumo["verificados"],
                               records_upserted=resumo["ok"], details=resumo)
        logger.info("Sonda de proxies: %s", resumo)
        return resumo
    except Exception as e:  # noqa: BLE001 — task muda não deixa rastro
        db.rollback()
        if run_id is not None:
            try:
                SyncRunRepository(db).mark_failed(run_id, str(e)[:500])
            except Exception:
                logger.exception("Não foi possível marcar a sonda como falha")
        logger.exception("Sonda de proxies falhou por inteiro")
        return {"erro": type(e).__name__, **resumo}
    finally:
        db.close()


@celery_app.task(name="proxies.verificar", bind=True)
def verificar_proxies(self, apenas_proxy_id: Optional[int] = None) -> Dict[str, Any]:
    return rodar_verificacao(apenas_proxy_id)
