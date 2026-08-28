"""
Alocação de proxy por sessão WAHA — o pool, a afinidade e o cooldown.

A decisão de produto que este módulo implementa (plano 27/08):

**Proxy por sessão é STICKY, não rotativo.** O que derruba número no WhatsApp
não é repetir o mesmo IP — é TROCAR de IP. Uma sessão que aparece em São Paulo
e dez minutos depois em Frankfurt é o padrão mais óbvio de conta automatizada
que existe. Logo: cada chip fica com um IP fixo enquanto estiver saudável, e
"dinâmico" aqui é a ALOCAÇÃO (pool no banco, realoca em falha real, admin troca
sem redeploy), nunca o IP por mensagem.

**Afinidade por usuária.** Os chips da mesma afiliada compartilham IP — é o
retrato coerente de uma pessoa com três aparelhos na mesma casa, e derruba o
custo de 3 IPs por afiliada para 1. Chips de usuárias DIFERENTES nunca dividem
IP: um banimento contaminaria a vizinhança.

**A credencial nunca sai em claro** — nem em log, nem em resposta de API, nem
em mensagem de erro. `credenciais()` é o único ponto que decifra, e o resultado
vai direto para o corpo da chamada ao WAHA.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.encryption import decrypt_value
from app.models.whatsapp_proxies import (
    PRIORIDADE_TIPO, PROXY_DEGRADADO, PROXY_OK, PROXY_QUARENTENA, WhatsappProxy,
)
from app.repositories.whatsapp_proxy_repository import WhatsappProxyRepository
from app.services.waha_client import ErroWhatsapp

logger = logging.getLogger(__name__)

# Frase que a tela mostra quando o pool está cheio e o proxy é obrigatório.
# A afiliada não sabe (nem precisa saber) o que é proxy: para ela é capacidade.
MENSAGEM_SEM_PROXY = (
    "Estamos sem capacidade de conexão no momento. Fale com o suporte."
)


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def habilitado() -> bool:
    """O módulo inteiro atrás de uma chave (feature flag + env)."""
    from app.core.feature_flags import whatsapp_proxy_ligado

    return whatsapp_proxy_ligado()


def alocar(db: Session, instancia) -> Optional[WhatsappProxy]:
    """
    Escolhe o proxy desta sessão. NÃO grava — quem chama persiste `proxy_id` na
    MESMA transação da instância (proxy alocado com sessão não criada = vaga
    fantasma no pool).

    Ordem de escolha, parando no primeiro que servir:

      1. proxy que já atende outro chip da MESMA `user_id` e tem vaga;
      2. proxy ativo, `ok`, sem nenhuma outra usuária, com vaga — preferindo
         móvel/residencial (datacenter é reconhecido e queimado) e o de menor
         ocupação;
      3. nenhum → `WHATSAPP_PROXY_OBRIGATORIO` decide: em produção levanta
         `ErroWhatsapp("sem_proxy")` e a sessão NÃO é criada; em local/hml
         cria sem proxy e loga em WARNING.
    """
    if not habilitado():
        return None

    repo = WhatsappProxyRepository(db)
    proxies = [p for p in repo.listar(ativos_apenas=True) if p.status == PROXY_OK]
    ocupacao = repo.contagem_de_sessoes()
    usuarias = repo.usuarias_por_proxy()
    user_id = instancia.user_id

    def tem_vaga(p: WhatsappProxy) -> bool:
        return ocupacao.get(p.id, 0) < (p.max_sessoes or 0)

    # (1) afinidade: o IP que já é o "endereço" desta afiliada.
    afins = [p for p in proxies
             if user_id in usuarias.get(p.id, set()) and tem_vaga(p)]
    if afins:
        escolhido = min(afins, key=lambda p: ocupacao.get(p.id, 0))
        logger.info("Proxy %s reusado por afinidade (user %s)", escolhido.id, user_id)
        return escolhido

    # (2) proxy virgem de outras usuárias. Compartilhar com usuária diferente
    # faria um banimento contaminar a vizinhança — por isso `not usuarias`.
    livres = [p for p in proxies if not usuarias.get(p.id) and tem_vaga(p)]
    if livres:
        escolhido = min(
            livres,
            key=lambda p: (PRIORIDADE_TIPO.get(p.tipo, 9), ocupacao.get(p.id, 0), p.id),
        )
        logger.info("Proxy %s alocado (user %s, tipo %s)",
                    escolhido.id, user_id, escolhido.tipo)
        return escolhido

    # (3) pool esgotado.
    if settings.WHATSAPP_PROXY_OBRIGATORIO:
        logger.error("Pool de proxies esgotado — sessão do user %s NÃO criada", user_id)
        raise ErroWhatsapp("sem_proxy", MENSAGEM_SEM_PROXY)
    logger.warning(
        "Pool de proxies sem vaga: sessão do user %s vai SEM proxy "
        "(WHATSAPP_PROXY_OBRIGATORIO=false)", user_id,
    )
    return None


def fixar(instancia, proxy: Optional[WhatsappProxy]) -> None:
    """Carimba o vínculo na instância — sem commit (quem chama decide a
    transação, para que proxy e sessão nasçam juntos ou não nasçam)."""
    if proxy is None:
        return
    instancia.proxy_id = proxy.id
    instancia.proxy_fixado_em = _agora()


def liberar(db: Session, instancia) -> None:
    """Devolve a vaga ao pool (remoção do número). Sem commit: acompanha a
    transação de quem remove."""
    if getattr(instancia, "proxy_id", None) is None:
        return
    logger.info("Proxy %s liberado pela sessão %s",
                instancia.proxy_id, instancia.nome_instancia)
    instancia.proxy_id = None
    instancia.proxy_fixado_em = None


def em_cooldown(instancia) -> bool:
    """Trocar de IP é o sinal que queremos evitar: só depois do cooldown.

    Instância sem troca anterior (`proxy_fixado_em` nulo) nunca está em
    cooldown — o carimbo é da FIXAÇÃO, e sessão recém-criada que já falha por
    proxy precisa poder sair do IP ruim.
    """
    fixado = getattr(instancia, "proxy_fixado_em", None)
    if fixado is None or not (instancia.proxy_trocas or 0):
        return False
    if fixado.tzinfo is None:
        fixado = fixado.replace(tzinfo=timezone.utc)
    return _agora() - fixado < timedelta(hours=settings.WHATSAPP_PROXY_COOLDOWN_H)


class TrocaEmCooldown(Exception):
    """Trocar de novo agora é justamente o padrão que queremos evitar."""


def realocar(db: Session, instancia, motivo: str,
             ignorar_cooldown: bool = False) -> Optional[WhatsappProxy]:
    """
    Troca o proxy de um chip. Evento RARO e registrado — nunca rotina.

    Só deve ser chamado por falha de rede/proxy CONFIRMADA (sonda de saúde,
    quarentena) ou por decisão explícita do admin. **Nunca** por número banido:
    trocar de IP porque o WhatsApp derrubou o número queima o IP seguinte também.

    Não roda no meio de uma fatia de envio — o motor apenas marca o proxy como
    degradado e pausa a execução (ver `roteiro_envio_service._tratar_erro`).
    """
    if not ignorar_cooldown and em_cooldown(instancia):
        raise TrocaEmCooldown(
            f"Este número trocou de IP há menos de "
            f"{settings.WHATSAPP_PROXY_COOLDOWN_H}h. Trocar de novo agora é "
            "exatamente o padrão que aumenta o risco de banimento."
        )
    anterior = instancia.proxy_id
    # Solta a vaga ANTES de escolher: senão o proxy atual continua contando
    # ocupação e um pool apertado devolveria "sem vaga" na própria troca.
    instancia.proxy_id = None
    db.flush()
    novo = alocar(db, instancia)
    if novo is not None and novo.id == anterior:
        # O pool devolveu o mesmo IP (é o único com vaga): não é troca.
        novo = None
    if novo is None:
        instancia.proxy_id = anterior
        logger.warning("Realocação da sessão %s sem destino (motivo=%s)",
                       instancia.nome_instancia, motivo)
        return None
    fixar(instancia, novo)
    instancia.proxy_trocas = (instancia.proxy_trocas or 0) + 1
    logger.warning("Sessão %s trocou de proxy %s → %s (motivo=%s, trocas=%s)",
                   instancia.nome_instancia, anterior, novo.id, motivo,
                   instancia.proxy_trocas)
    return novo


def credenciais(proxy: Optional[WhatsappProxy]) -> Optional[Dict[str, Any]]:
    """
    `config.proxy` do WAHA, decifrado, montado SÓ na hora de chamar.

    `server` vai **sem** esquema (`http://`) — exigência do WAHA. O retorno
    nunca deve ser logado: use `mascarar_proxy` antes de qualquer log.
    """
    if proxy is None:
        return None
    corpo: Dict[str, Any] = {"server": proxy.servidor}
    try:
        if proxy.usuario_cifrado:
            corpo["username"] = decrypt_value(proxy.usuario_cifrado)
        if proxy.senha_cifrada:
            corpo["password"] = decrypt_value(proxy.senha_cifrada)
    except Exception as e:  # noqa: BLE001 — nunca deixar a credencial no texto
        logger.error("Falha ao decifrar credencial do proxy %s (%s)",
                     proxy.id, type(e).__name__)
        raise ErroWhatsapp("proxy_credencial",
                           "Credencial do proxy não pôde ser lida.") from e
    return corpo


def credenciais_da_instancia(db: Session, instancia) -> Optional[Dict[str, Any]]:
    """Proxy vigente da sessão, pronto para o corpo do WAHA. `None` quando a
    sessão não tem proxy fixado (ou o módulo está desligado)."""
    proxy_id = getattr(instancia, "proxy_id", None)
    if proxy_id is None or not habilitado():
        return None
    return credenciais(WhatsappProxyRepository(db).por_id(proxy_id))


# --- saúde ------------------------------------------------------------------


def registrar_falha(db: Session, proxy: WhatsappProxy, erro: str) -> str:
    """Escada de saúde: 2 falhas seguidas → degradado; 4 → quarentena.

    Devolve o status resultante. Quem coloca em quarentena é responsável por
    realocar os chips (o pool não escolhe proxy em quarentena)."""
    proxy.falhas_seguidas = (proxy.falhas_seguidas or 0) + 1
    proxy.ultimo_erro = (erro or "")[:2000]
    proxy.verificado_em = _agora()
    if proxy.falhas_seguidas >= settings.WHATSAPP_PROXY_FALHAS_QUARENTENA:
        proxy.status = PROXY_QUARENTENA
    elif proxy.falhas_seguidas >= settings.WHATSAPP_PROXY_FALHAS_DEGRADADO:
        proxy.status = PROXY_DEGRADADO
    WhatsappProxyRepository(db).salvar(proxy)
    logger.warning("Proxy %s falhou (%sª seguida) → %s",
                   proxy.id, proxy.falhas_seguidas, proxy.status)
    return proxy.status


def registrar_sucesso(db: Session, proxy: WhatsappProxy,
                      ip: Optional[str] = None,
                      pais: Optional[str] = None) -> None:
    proxy.falhas_seguidas = 0
    proxy.status = PROXY_OK
    proxy.ultimo_erro = None
    proxy.verificado_em = _agora()
    if ip:
        proxy.ultimo_ip = ip[:64]
    if pais:
        proxy.ultimo_pais = pais[:8]
    WhatsappProxyRepository(db).salvar(proxy)


def marcar_degradado(db: Session, proxy_id: int, motivo: str) -> None:
    """Chamado pelo motor de envio quando TODOS os chips de um proxy falham
    por rede na mesma fatia. Não troca proxy: só marca e deixa a sonda decidir
    — realocar no meio de um envio é pior que pausar."""
    repo = WhatsappProxyRepository(db)
    proxy = repo.por_id(proxy_id)
    if proxy is None or proxy.status == PROXY_QUARENTENA:
        return
    proxy.status = PROXY_DEGRADADO
    proxy.ultimo_erro = (motivo or "")[:2000]
    repo.salvar(proxy)
    logger.error("Proxy %s marcado degradado pelo motor de envio: %s",
                 proxy_id, motivo)


def ocupacao(db: Session) -> Dict[int, int]:
    return WhatsappProxyRepository(db).contagem_de_sessoes()
