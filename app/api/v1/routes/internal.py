"""
Endpoints internos chamados por scheduler externo (pg_cron + pg_net no Supabase).

Autenticação por secret compartilhado. Aceita ambos os formatos para compatibilidade:
  - Authorization: Bearer <CRON_SECRET>   (preferencial — passa por qualquer proxy)
  - X-Cron-Secret: <CRON_SECRET>          (legado, pode ser strippeado por WAFs)
"""
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])


def _extract_secret(authorization: str | None, x_cron_secret: str | None) -> str | None:
    """Retorna o secret recebido em qualquer um dos dois headers aceitos."""
    if authorization:
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            return token[7:].strip()
        return token
    return x_cron_secret


def _validate_cron_secret(received: str | None, caller_ip: str | None) -> None:
    if not settings.CRON_SECRET:
        logger.error("CRON_SECRET não configurado — rejeitando chamada interna (caller=%s)", caller_ip)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cron endpoint disabled (CRON_SECRET not configured).",
        )
    if not received or not hmac.compare_digest(received, settings.CRON_SECRET):
        logger.warning("Tentativa inválida no /internal/cron (caller=%s)", caller_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid cron secret.",
        )


@router.post("/cron/shopee-sync", status_code=status.HTTP_202_ACCEPTED)
async def cron_shopee_sync(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """
    Disparado pelo pg_cron via pg_net com tipo: full (90d madrugada) ou incremental (7d horário).

    Query:
      - type=full|incremental (padrão: incremental)
      - user_id=<int> opcional — sincroniza só esse usuário (ops / destravar conta)

    Preferência: fan-out Celery (worker sobrevive a restart da API). Se o broker
    falhar, cai no BackgroundTask inline com ordenação stale-first + lock global.
    """
    caller_ip = request.client.host if request.client else "unknown"
    _validate_cron_secret(_extract_secret(authorization, x_cron_secret), caller_ip)

    from app.services.shopee_integration_service import run_shopee_sync_all

    sync_type = request.query_params.get("type", "incremental")
    days_back = 90 if sync_type == "full" else 7
    user_id_raw = request.query_params.get("user_id")
    user_id: int | None = None
    if user_id_raw:
        try:
            user_id = int(user_id_raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="user_id deve ser inteiro.",
            ) from exc

    trigger = "ops_unstick" if user_id is not None else ("cron_full" if sync_type == "full" else "cron_incremental")

    mode = "celery-fanout"
    if user_id is not None:
        from app.tasks.shopee_tasks import sync_shopee_user_task

        sync_shopee_user_task.delay(user_id, days_back=days_back, empty_attempt=0, trigger=trigger)
        mode = "celery-user"
    else:
        try:
            from app.tasks.shopee_tasks import sync_all_shopee_users_task

            sync_all_shopee_users_task.delay(days_back, trigger=trigger)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "cron.shopee-sync: Celery indisponível (%s) — fallback inline",
                exc,
            )
            background_tasks.add_task(run_shopee_sync_all, days_back, trigger)
            mode = "background-inline-fallback"

    logger.info(
        "cron.shopee-sync mode=%s type=%s days_back=%d user_id=%s caller_ip=%s source=%s",
        mode,
        sync_type,
        days_back,
        user_id,
        caller_ip,
        request.headers.get("X-Cron-Source", "unknown"),
    )
    return {
        "status": "accepted",
        "mode": mode,
        "sync_type": sync_type,
        "days_back": days_back,
        "user_id": user_id,
    }


@router.post("/cron/facebook-sync", status_code=status.HTTP_202_ACCEPTED)
async def cron_facebook_sync(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """
    Disparado pelo pg_cron via pg_net (de hora em hora).
    Sync manual continua disponível em POST /facebook/sync.

    Roda o sync de TODOS os usuários Facebook INLINE, num BackgroundTask do FastAPI —
    SEM Celery/worker. Retorna 202 na hora (satisfaz o timeout do pg_net) e o sync
    continua no próprio processo da API.
    """
    caller_ip = request.client.host if request.client else "unknown"
    _validate_cron_secret(_extract_secret(authorization, x_cron_secret), caller_ip)

    from app.services.facebook_integration_service import run_facebook_sync_all

    background_tasks.add_task(run_facebook_sync_all, trigger="cron")
    logger.info(
        "cron.facebook-sync (inline/background, sem worker) caller_ip=%s source=%s",
        caller_ip, request.headers.get("X-Cron-Source", "unknown"),
    )
    return {"status": "accepted", "mode": "background-inline"}


@router.post("/cron/instagram-token-refresh", status_code=status.HTTP_202_ACCEPTED)
async def cron_instagram_token_refresh(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """Renova os tokens do Instagram que vencem em menos de 10 dias.

    O token longo do Business Login for Instagram dura 60 dias e só pode ser
    renovado enquanto ainda está válido — se vencer, a aluna PRECISA refazer o
    login. Por isso o cron roda todo dia, com folga de 10 dias: mesmo que o
    backend fique fora do ar alguns dias, ainda sobra janela pra renovar.

    Roda INLINE num BackgroundTask (mesmo desenho do cron do Facebook).
    """
    caller_ip = request.client.host if request.client else "unknown"
    _validate_cron_secret(_extract_secret(authorization, x_cron_secret), caller_ip)

    from app.services.instagram_connection_service import run_instagram_token_refresh_all

    background_tasks.add_task(run_instagram_token_refresh_all)
    logger.info(
        "cron.instagram-token-refresh (inline/background) caller_ip=%s source=%s",
        caller_ip, request.headers.get("X-Cron-Source", "unknown"),
    )
    return {"status": "accepted", "mode": "background-inline"}


@router.post("/cron/roteiros", status_code=status.HTTP_202_ACCEPTED)
def cron_roteiros(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """
    Tick do motor (pg_cron */5min): flip atômico agendada→enviando das
    execuções due e enfileira UMA task Celery p9 por execução. Dois ticks
    simultâneos não duplicam — o UPDATE...RETURNING garante. O mesmo tick
    RESGATA execuções estagnadas em `enviando` (worker que morreu depois do
    flip, broker fora do ar) — sem isso elas ficariam presas para sempre.
    """
    caller_ip = request.client.host if request.client else None
    _validate_cron_secret(_extract_secret(authorization, x_cron_secret), caller_ip)
    from datetime import datetime, timezone

    from app.db.session import SessionLocal
    from app.repositories.roteiro_repository import RoteiroRepository
    from app.tasks.roteiro_tasks import processar_execucao

    agora = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        repo = RoteiroRepository(db)
        ids = repo.flip_agendadas_para_enviando(agora)
        resgatadas = repo.enviando_estagnadas(agora)
    finally:
        db.close()
    for execucao_id in ids + resgatadas:
        processar_execucao.apply_async(args=[execucao_id], priority=9)
    if resgatadas:
        logger.warning("Tick resgatou %s execução(ões) estagnada(s): %s",
                       len(resgatadas), resgatadas)
    logger.info("Tick de roteiros: %s execução(ões) enfileirada(s)", len(ids))
    return {"enfileiradas": ids, "resgatadas": resgatadas}


@router.post("/cron/grupos-snapshot", status_code=status.HTTP_202_ACCEPTED)
def cron_grupos_snapshot(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """
    Retrato diário dos grupos (F6) + reconciliação de sessões órfãs no WAHA
    + realinhamento dos eventos assinados por sessão (F8).
    1×/dia: sync pesado de hora em hora foi o que derrubou o banco em 20/07.
    """
    caller_ip = request.client.host if request.client else None
    _validate_cron_secret(_extract_secret(authorization, x_cron_secret), caller_ip)
    background_tasks.add_task(_rodar_snapshot_de_grupos)
    return {"status": "agendado"}


def _rodar_snapshot_de_grupos() -> None:
    from app.db.session import SessionLocal
    from app.models.whatsapp_grupos import INSTANCIA_CONECTADA, WhatsappInstancia
    from app.services.grupo_snapshot_service import (
        reconciliar_eventos_de_sessao, reconciliar_orfas, snapshot_do_usuario,
    )

    db = SessionLocal()
    try:
        user_ids = [
            uid for (uid,) in db.query(WhatsappInstancia.user_id)
            .filter(WhatsappInstancia.status == INSTANCIA_CONECTADA)
            .distinct().all()
        ]
        total = {"usuarias": 0, "grupos": 0, "erros": 0}
        for user_id in user_ids:
            try:
                r = snapshot_do_usuario(db, user_id)
                total["usuarias"] += 1
                total["grupos"] += r["grupos"]
                total["erros"] += r["erros"]
            except Exception:
                db.rollback()
                total["erros"] += 1
                logger.exception("Snapshot falhou para user %s", user_id)
        orfas = reconciliar_orfas(db)
        # Rede de segurança do monitoramento (F8): o alinhamento normal é no
        # toggle, mas ele pode falhar com a sessão fora do ar. Sessão que segue
        # assinando `message` sem monitoramento ativo entrega conteúdo de grupo
        # que ninguém pediu.
        eventos = reconciliar_eventos_de_sessao(db)
        # Captura presa em `replicando` (worker morto entre o claim e o fim)
        # ficaria invisível para sempre — nem replica, nem aparece como erro.
        from app.services.monitoramento_service import MonitoramentoService

        servico_mon = MonitoramentoService(db)
        destravadas = servico_mon.destravar_replicando()
        # Retenção: texto escrito por terceiros não fica aqui para sempre.
        expurgadas = servico_mon.expurgar_antigas(
            settings.MONITORAMENTO_RETENCAO_DIAS
        )
        # §6.5: número criado que NUNCA pareou (status `criada` há mais de
        # 24h) é lixo de fluxo abandonado — some da lista e devolve a vaga do
        # plano. SEMPRE via service.remover (WAHA + proxy + soft-delete):
        # UPDATE direto deixaria sessão zumbi no WAHA e proxy preso.
        removidas = _limpar_instancias_nunca_pareadas(db)
        logger.info("Snapshot diário: %s | órfãs removidas: %s | sessões "
                    "realinhadas: %s | capturas destravadas: %s | "
                    "expurgadas: %s | nunca pareadas removidas: %s",
                    total, orfas, eventos, destravadas, expurgadas, removidas)
    except Exception:
        logger.exception("Snapshot diário de grupos falhou por inteiro")
    finally:
        db.close()


def _limpar_instancias_nunca_pareadas(db) -> int:
    """Remove instâncias `criada` com mais de 24h (spec §6.5). Devolve quantas."""
    from datetime import datetime, timedelta, timezone

    from app.repositories.whatsapp_instancia_repository import (
        WhatsappInstanciaRepository,
    )
    from app.services.whatsapp_instancia_service import WhatsappInstanciaService

    repo = WhatsappInstanciaRepository(db)
    limite = datetime.now(timezone.utc) - timedelta(hours=24)
    velhas = repo.criadas_antes_de(limite)
    if not velhas:
        return 0
    servico = WhatsappInstanciaService(
        repo=repo, plan_limit_numeros=0,
        webhook_url=settings.WAHA_WEBHOOK_URL, db=db,
    )
    removidas = 0
    for instancia in velhas:
        try:
            servico.remover(instancia)
            removidas += 1
        except Exception:
            db.rollback()
            # Uma instância problemática não pode travar a limpeza das demais.
            logger.exception("Falha ao remover instância nunca pareada %s",
                             getattr(instancia, "nome_instancia", "?"))
    if removidas:
        logger.info("Limpeza §6.5: %s instância(s) nunca pareada(s) removida(s)",
                    removidas)
    return removidas


@router.post("/cron/proxy-health", status_code=status.HTTP_202_ACCEPTED)
def cron_proxy_health(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """
    Sonda de saúde do pool de proxies (plano §2.7) — pg_cron de hora em hora.

    De hora em hora, e não 1×/dia como o snapshot de grupos, porque aqui a
    consulta é externa (um GET por proxy contra um eco de IP) e não toca o
    banco compartilhado — não é o padrão que derrubou o Postgres em 20/07.

    Query:
      - proxy_id=<int> opcional — verifica só esse proxy (botão "Verificar"
        do admin passa por aqui em vez de duplicar a lógica).

    Preferência por Celery (o worker sobrevive a restart da API); se o broker
    estiver fora — e ele já esteve, em crashloop de AOF — cai no BackgroundTask
    inline, senão a sonda simplesmente para de existir em silêncio.
    """
    caller_ip = request.client.host if request.client else "unknown"
    _validate_cron_secret(_extract_secret(authorization, x_cron_secret), caller_ip)

    from app.tasks.proxy_tasks import rodar_verificacao

    bruto = request.query_params.get("proxy_id")
    try:
        apenas = int(bruto) if bruto else None
    except ValueError:
        raise HTTPException(status_code=400, detail="proxy_id inválido")

    modo = "celery"
    try:
        from app.tasks.proxy_tasks import verificar_proxies

        verificar_proxies.apply_async(kwargs={"apenas_proxy_id": apenas}, priority=9)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cron.proxy-health: Celery indisponível (%s) — inline", exc)
        background_tasks.add_task(rodar_verificacao, apenas)
        modo = "background-inline-fallback"
    logger.info("cron.proxy-health aceito caller_ip=%s proxy_id=%s modo=%s",
                caller_ip, apenas, modo)
    return {"status": "accepted", "mode": modo, "proxy_id": apenas}


@router.get("/cron/health", status_code=status.HTTP_200_OK)
def cron_health(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """Sanity check para o pg_cron validar conectividade sem enfileirar work."""
    caller_ip = request.client.host if request.client else "unknown"
    _validate_cron_secret(_extract_secret(authorization, x_cron_secret), caller_ip)
    return {"status": "ok"}
