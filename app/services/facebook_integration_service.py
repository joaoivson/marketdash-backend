import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.encryption import decrypt_value, encrypt_value
from app.models.campaign import CampaignDailyInsight, CampaignPlatformDailyInsight
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.facebook_integration_repository import FacebookIntegrationRepository
from app.schemas.facebook_integration import (
    FacebookAdAccount,
    FacebookIntegrationResponse,
)
from app.services import facebook_marketing_client as fb

logger = logging.getLogger(__name__)

# Janela de insights na 1ª carga (backfill): cobre os filtros 7d/14d/mês da tela.
SYNC_WINDOW_DAYS = 90
# Ciclos após o backfill: revisa só os últimos dias quentes (o gasto do Meta ainda
# muda ~3 dias por ajustes/reembolsos, depois estabiliza). O upsert preserva o resto.
INCREMENTAL_WINDOW_DAYS = 3
BRT = timezone(timedelta(hours=-3))


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _to_int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def derive_ad_review_issue(effective_status: Optional[str], ads: list[dict]) -> Optional[str]:
    """Campanha ACTIVE sem nenhum anúncio ACTIVE = não está entregando de verdade.

    Reprovação de anúncio (moderação da Meta) não rebaixa o `effective_status`
    da CAMPANHA — ela continua ACTIVE pra sempre, mesmo com o anúncio reprovado
    e zero entrega. `issues_info` no nível da campanha também vem vazio nesse
    caso (testado contra a API real) — o status real só existe no ANÚNCIO
    (`DISAPPROVED`, `PENDING_REVIEW`, etc., ver Ad.effective_status).

    Campanha sem nenhum anúncio criado ainda (lista vazia) não conta como
    problema — pode só estar em configuração, não necessariamente reprovada.
    """
    if (effective_status or "").upper() != "ACTIVE":
        return None
    if not ads:
        return None
    tem_ad_ativo = any((ad.get("effective_status") or "").upper() == "ACTIVE" for ad in ads)
    if tem_ad_ativo:
        return None
    status_dos_ads = sorted({(ad.get("effective_status") or "DESCONHECIDO").upper() for ad in ads})
    return ", ".join(status_dos_ads)[:255]


def _budget_to_brl(raw) -> Optional[float]:
    """daily_budget/lifetime_budget vêm em centavos (string) na Graph API."""
    if raw is None or raw == "":
        return None
    return _to_float(raw) / 100.0


def agrupar_insights_por_campanha(insights: list[dict]) -> dict[str, list[dict]]:
    """Agrupa a resposta de nível-CONTA por `campaign_id`.

    `get_account_campaign_insights` traz uma linha por (campanha, dia) numa
    chamada só; o sync precisa dela indexada por campanha. Linha sem
    `campaign_id` é descartada: sem ela não há a qual campanha local ligar.
    """
    agrupado: dict[str, list[dict]] = {}
    for ins in insights:
        fb_campaign_id = str(ins.get("campaign_id") or "")
        if fb_campaign_id:
            agrupado.setdefault(fb_campaign_id, []).append(ins)
    return agrupado


def insight_vazio_com_entrega(insights_upserted: int, placement_upserted: int) -> bool:
    """True quando a conta entregou no período mas nenhum insight foi gravado.

    É a assinatura do incidente de 26/08/2026: a chamada de insights voltou
    vazia (HTTP 200, `data: []`) enquanto a de placement seguia trazendo dados,
    e o gasto sumiu da tela de 3 alunas por dois dias com o sync marcado
    "success". Conta que parou de anunciar NÃO cai aqui — nesse caso as duas
    chamadas vêm vazias, e acusar isso esvaziaria o valor do sinal.
    """
    return insights_upserted == 0 and placement_upserted > 0


class FacebookIntegrationService:
    def __init__(self, repo: FacebookIntegrationRepository):
        self.repo = repo
        self.db: Session = repo.db

    def resolve_connection_state(self, user_id: int) -> str:
        """conectado | nunca | desconectado."""
        integration = self.repo.get_by_user_id(user_id)
        if not integration:
            return "nunca"
        if not integration.is_active:
            return "desconectado"
        if integration.token_expires_at:
            now = datetime.now(timezone.utc)
            exp = integration.token_expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now > exp:
                integration.is_active = False
                self.db.commit()
                return "desconectado"
        accounts = integration.account_ids_list()
        if not accounts:
            # Token ok mas sem conta → ainda não está "conectado" operacionalmente
            return "desconectado"
        return "conectado"

    def require_connected(self, user_id: int) -> None:
        if self.resolve_connection_state(user_id) != "conectado":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Nenhuma conta de anúncio conectada",
            )

    def mark_disconnected(self, user_id: int) -> None:
        integration = self.repo.get_by_user_id(user_id)
        if integration and integration.is_active:
            integration.is_active = False
            self.db.commit()
            logger.info("Facebook marcado desconectado (token inválido) user_id=%s", user_id)

    @staticmethod
    def _to_response(integration) -> FacebookIntegrationResponse:
        """Monta a resposta incluindo a lista de contas (ad_account_ids)."""
        resp = FacebookIntegrationResponse.model_validate(integration)
        resp.ad_account_ids = integration.account_ids_list()
        # connection_state preenchido pelo caller que tem o service
        return resp

    def get_status(self, user_id: int) -> Optional[FacebookIntegrationResponse]:
        integration = self.repo.get_by_user_id(user_id)
        if not integration:
            return None
        # Refresh expiry → is_active
        state = self.resolve_connection_state(user_id)
        integration = self.repo.get_by_user_id(user_id)
        if not integration:
            return None
        resp = self._to_response(integration)
        resp.connection_state = state
        resp.is_active = state == "conectado"
        return resp

    # ------------------------------------------------------------------ #
    #  OAuth                                                              #
    # ------------------------------------------------------------------ #

    def _resolve_redirect_uri(self, supplied: Optional[str]) -> str:
        redirect = supplied or settings.FACEBOOK_OAUTH_REDIRECT_URI
        if not redirect:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="redirect_uri não informado e FACEBOOK_OAUTH_REDIRECT_URI não configurado.",
            )
        return redirect

    def build_oauth_url(self, redirect_uri: Optional[str]) -> str:
        state = secrets.token_urlsafe(16)
        return fb.build_oauth_url(self._resolve_redirect_uri(redirect_uri), state)

    async def handle_oauth_callback(self, user_id: int, code: str, redirect_uri: Optional[str]) -> FacebookIntegrationResponse:
        redirect = self._resolve_redirect_uri(redirect_uri)

        short = await fb.exchange_code_for_token(code, redirect)
        short_token = short.get("access_token")
        if not short_token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Falha ao obter token do Facebook.")

        long = await fb.exchange_for_long_lived_token(short_token)
        access_token = long.get("access_token") or short_token
        expires_in = _to_int(long.get("expires_in"))
        token_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None
        )

        me = await fb.get_me(access_token)

        integration = self.repo.upsert_token(
            user_id=user_id,
            encrypted_access_token=encrypt_value(access_token),
            fb_user_id=str(me.get("id")) if me.get("id") else None,
            fb_user_name=me.get("name"),
            scopes=",".join(fb.DEFAULT_SCOPES),
            token_expires_at=token_expires_at,
        )
        self.db.commit()
        self.db.refresh(integration)
        return self._to_response(integration)

    # ------------------------------------------------------------------ #
    #  Conta de anúncios                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _e_token_invalido(exc: HTTPException) -> bool:
        """True quando o erro é o token do usuário no Facebook, não uma falha nossa."""
        detail = exc.detail
        return isinstance(detail, dict) and detail.get("code") == fb.FACEBOOK_TOKEN_INVALIDO

    def _marcar_desconectado(self, user_id: int) -> None:
        integration = self.repo.get_by_user_id(user_id)
        if integration and integration.is_active:
            integration.is_active = False
            self.db.commit()
            logger.info("Integração Facebook marcada como inativa (token inválido) user_id=%s", user_id)

    def _access_token(self, user_id: int) -> str:
        integration = self.repo.get_by_user_id(user_id)
        if not integration:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Integração Facebook não configurada.",
            )
        if not integration.is_active:
            # Integração existe mas o token venceu (resolve_connection_state já derrubou
            # is_active). Mesmo código do 409 da Graph API: a tela só precisa saber que o
            # caminho de volta é refazer o OAuth.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": fb.FACEBOOK_TOKEN_INVALIDO,
                    "message": "Sua conexão com o Facebook expirou. Clique em Conectar com Facebook para reconectar.",
                },
            )
        return decrypt_value(integration.encrypted_access_token)

    async def list_ad_accounts(self, user_id: int) -> list[FacebookAdAccount]:
        # Conta demo: token placeholder — não chama Graph API.
        if self._is_demo_user(user_id):
            return [
                FacebookAdAccount(
                    account_id="demo",
                    name="Conta Demo",
                    currency="BRL",
                    account_status=1,
                    id="act_demo",
                )
            ]
        token = self._access_token(user_id)
        try:
            raw = await fb.list_ad_accounts(token)
        except HTTPException as exc:
            # Token morto no lado do Facebook (senha trocada, app removido, expirado):
            # marca a integração como inativa para o status virar "desconectado" e a tela
            # oferecer o botão de reconectar, em vez de exibir "Conectado" com lista vazia.
            if self._e_token_invalido(exc):
                self._marcar_desconectado(user_id)
            raise
        accounts: list[FacebookAdAccount] = []
        for acc in raw:
            account_id = acc.get("account_id") or (acc.get("id") or "").replace("act_", "")
            accounts.append(
                FacebookAdAccount(
                    account_id=account_id,
                    name=acc.get("name"),
                    currency=acc.get("currency"),
                    account_status=acc.get("account_status"),
                    id=acc.get("id") or (f"act_{account_id}" if account_id else None),
                )
            )
        return accounts

    def select_ad_account(self, user_id: int, ad_account_id: str, ad_account_name: Optional[str]) -> FacebookIntegrationResponse:
        # Normaliza para o formato "act_<id>" usado nas chamadas da Graph API.
        normalized = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        integration = self.repo.set_ad_account(user_id, normalized, ad_account_name)
        if not integration:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integração Facebook não encontrada.")
        self.db.commit()
        self.db.refresh(integration)
        return self._to_response(integration)

    def select_ad_accounts(self, user_id: int, account_ids: list[str]) -> FacebookIntegrationResponse:
        """Salva uma ou mais contas de anúncio selecionadas (checkboxes)."""
        normalized = [a if a.startswith("act_") else f"act_{a}" for a in account_ids if a]
        integration = self.repo.set_ad_accounts(user_id, normalized)
        if not integration:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integração Facebook não encontrada.")
        self.db.commit()
        self.db.refresh(integration)
        resp = self._to_response(integration)
        resp.connection_state = self.resolve_connection_state(user_id)
        return resp

    def is_token_valid(self, user_id: int) -> bool:
        return self.resolve_connection_state(user_id) == "conectado"

    def disconnect(self, user_id: int) -> None:
        """Desconecta Facebook do usuário. Idempotente."""
        deleted = self.repo.delete_by_user_id(user_id)
        self.db.commit()
        if deleted:
            logger.info("Facebook desconectado user_id=%s", user_id)
        else:
            logger.info("Facebook desconectar ignorado user_id=%s (nenhuma integração)", user_id)

    # ------------------------------------------------------------------ #
    #  Escrita (pausar/ativar, orçamento)                                 #
    # ------------------------------------------------------------------ #

    def _is_demo_user(self, user_id: int) -> bool:
        from app.models.user import User

        user = self.db.query(User).filter(User.id == user_id).first()
        return bool(user and getattr(user, "is_demo", False))

    async def set_campaign_status(self, user_id: int, fb_campaign_id: str, active: bool) -> str:
        new_status = "ACTIVE" if active else "PAUSED"
        if self._is_demo_user(user_id):
            return new_status
        self.require_connected(user_id)
        try:
            token = self._access_token(user_id)
            await fb.update_campaign_status(token, fb_campaign_id, new_status)
        except HTTPException as exc:
            self._maybe_mark_token_invalid(user_id, exc)
            raise
        return new_status

    async def set_campaign_budget(self, user_id: int, fb_campaign_id: str, daily_budget_brl: float) -> float:
        if self._is_demo_user(user_id):
            return daily_budget_brl
        self.require_connected(user_id)
        try:
            token = self._access_token(user_id)
            await fb.update_campaign_daily_budget(token, fb_campaign_id, daily_budget_brl)
        except HTTPException as exc:
            self._maybe_mark_token_invalid(user_id, exc)
            raise
        return daily_budget_brl

    def _maybe_mark_token_invalid(self, user_id: int, exc: HTTPException) -> None:
        detail = str(exc.detail or "")
        if "190" in detail or "OAuthException" in detail or "Session has expired" in detail:
            self.mark_disconnected(user_id)

    def clear_ads_data(self, user_id: int) -> dict:
        """Apaga campanhas, insights, ad spends e cliques. Não toca Shopee/comissões."""
        from app.models.campaign import Campaign, CampaignDailyInsight
        from app.models.ad_spend import AdSpend
        from app.models.click_row import ClickRow

        # Placement primeiro: tem FK pra campaigns, precisa sair antes.
        platform_insights_deleted = (
            self.db.query(CampaignPlatformDailyInsight)
            .filter(CampaignPlatformDailyInsight.user_id == user_id)
            .delete(synchronize_session=False)
        )
        insights_deleted = (
            self.db.query(CampaignDailyInsight)
            .filter(CampaignDailyInsight.user_id == user_id)
            .delete(synchronize_session=False)
        )
        campaigns_deleted = (
            self.db.query(Campaign)
            .filter(Campaign.user_id == user_id)
            .delete(synchronize_session=False)
        )
        spends_deleted = (
            self.db.query(AdSpend)
            .filter(AdSpend.user_id == user_id)
            .delete(synchronize_session=False)
        )
        clicks_deleted = (
            self.db.query(ClickRow)
            .filter(ClickRow.user_id == user_id)
            .delete(synchronize_session=False)
        )
        self.db.commit()
        return {
            "campaigns": campaigns_deleted,
            "insights": insights_deleted,
            "platform_insights": platform_insights_deleted,
            "ad_spends": spends_deleted,
            "clicks": clicks_deleted,
        }

    # ------------------------------------------------------------------ #
    #  Sincronização (campanhas + insights diários)                       #
    # ------------------------------------------------------------------ #

    async def sync_user(self, user_id: int, db: Session, trigger: str = "manual") -> int:
        """Sincroniza campanhas e insights diários da conta selecionada.

        Retorna o número de campanhas processadas. Atualiza last_sync_at.

        trigger: origem da chamada (manual/cron/account_select) — só alimenta o log de
        execução (sync_runs), não muda o comportamento do sync.
        """
        from app.repositories.sync_run_repository import SyncRunRepository

        run_repo = SyncRunRepository(db)
        try:
            run_id: Optional[int] = run_repo.create(source="facebook", trigger=trigger, user_id=user_id)
        except Exception as exc:
            logger.warning(
                "sync_runs: falha ao criar linha de tracking (facebook) user_id=%s (%s) — seguindo sem tracking",
                user_id, exc,
            )
            run_id = None

        integration = self.repo.get_by_user_id(user_id)
        account_ids = integration.account_ids_list() if integration else []
        if not integration or not integration.is_active or not account_ids:
            logger.info("Facebook sync ignorado user_id=%s (sem integração/conta ativa)", user_id)
            if run_id is not None:
                try:
                    run_repo.mark_success(run_id, records_fetched=0, records_upserted=0)
                except Exception:
                    pass
            return 0

        token = decrypt_value(integration.encrypted_access_token)
        camp_repo = CampaignRepository(db)

        # Serializa syncs concorrentes do MESMO usuário (cron inline + botão manual/worker) —
        # evita corrida no rebuild do AdSpend (delete+insert). Lock por-transação, liberado no commit.
        # NOTA: pg_advisory_xact_lock BLOQUEIA até liberar (não é try-lock como o do Shopee) — não
        # existe aqui um caso de "no-op por colisão de lock", por isso sem status skipped_lock.
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": int(user_id)})

        try:
            now = datetime.now(BRT)
            # Backfill de 90 dias quando NÃO há histórico suficiente no banco: 1ª carga
            # (last_sync_at None) OU usuário que migrou pro modelo novo e só tem os últimos dias
            # (nunca fez o backfill). Sem isso, o usuário existente fica preso em 3d e o Dashboard
            # não mostra gasto/cliques histórico. Depois do backfill, passa a incremental (3d).
            earliest = camp_repo.earliest_insight_date(user_id)
            needs_backfill = (
                integration.last_sync_at is None
                or earliest is None
                or earliest > (now.date() - timedelta(days=7))
            )
            window_days = SYNC_WINDOW_DAYS if needs_backfill else INCREMENTAL_WINDOW_DAYS
            since = (now - timedelta(days=window_days)).date()
            until = now.date()

            # Janela do breakdown por placement (Instagram vs Facebook) é decidida
            # SEPARADAMENTE: a tabela é nova, então quem já usava o MarketDash tem 90
            # dias de insights agregados e ZERO linha por plataforma. Reaproveitar
            # `needs_backfill` deixaria esses usuários sem histórico de Instagram pra
            # sempre, porque `last_sync_at` já está preenchido.
            #
            # SAVEPOINT: se a migration 051 ainda não foi aplicada, a tabela não
            # existe e o SELECT aborta a transação inteira no Postgres — derrubando
            # um sync de Facebook que funcionava. O savepoint isola a falha: o
            # placement fica de fora desta rodada e o gasto continua entrando.
            placement_disponivel = True
            platform_since = since
            try:
                with db.begin_nested():
                    earliest_platform = camp_repo.earliest_platform_insight_date(user_id)
                platform_needs_backfill = (
                    earliest_platform is None
                    or earliest_platform > (now.date() - timedelta(days=7))
                )
                platform_window_days = (
                    SYNC_WINDOW_DAYS if platform_needs_backfill else INCREMENTAL_WINDOW_DAYS
                )
                platform_since = (now - timedelta(days=platform_window_days)).date()
            except SQLAlchemyError as exc:
                placement_disponivel = False
                logger.warning(
                    "Placement por plataforma indisponível (migration 051 aplicada?) "
                    "user_id=%s: %s — o sync segue sem essa etapa",
                    user_id, exc,
                )

            processed = 0
            # Contados separadamente porque o run "success" com 0 insights foi
            # exatamente o que escondeu o incidente de 26/08 por dois dias.
            insights_upserted = 0
            placement_upserted = 0
            for ad_account_id in account_ids:
                try:
                    campaigns = await fb.list_campaigns(token, ad_account_id)
                except HTTPException as exc:
                    # Não aborta o sync inteiro se uma conta falhar — segue para a próxima.
                    logger.warning(
                        "Facebook list_campaigns falhou user_id=%s conta=%s: %s",
                        user_id, ad_account_id, exc.detail,
                    )
                    continue

                # Uma chamada por CONTA (não por campanha) — evita N+1. O status real de
                # moderação só existe no anúncio (ver derive_ad_review_issue).
                ads_by_campaign: dict[str, list[dict]] = {}
                ads_fetch_ok = True
                try:
                    ads = await fb.list_ads(token, ad_account_id)
                    for ad in ads:
                        cid = str(ad.get("campaign_id") or "")
                        if cid:
                            ads_by_campaign.setdefault(cid, []).append(ad)
                except HTTPException as exc:
                    ads_fetch_ok = False
                    logger.warning(
                        "Facebook list_ads falhou user_id=%s conta=%s: %s — "
                        "ad_review_issue não será atualizado nesta rodada",
                        user_id, ad_account_id, exc.detail,
                    )

                # Insights diários de TODAS as campanhas da conta numa chamada só
                # (ver get_account_campaign_insights). Falha aqui não derruba o sync:
                # as campanhas continuam sendo atualizadas e o gasto entra no próximo
                # ciclo — mas a guarda no fim marca o run como parcial.
                try:
                    insights_da_conta = await fb.get_account_campaign_insights(
                        token, ad_account_id, since.isoformat(), until.isoformat()
                    )
                except HTTPException as exc:
                    logger.warning(
                        "Facebook insights falhou user_id=%s conta=%s: %s",
                        user_id, ad_account_id, exc.detail,
                    )
                    insights_da_conta = []

                insights_por_campanha = agrupar_insights_por_campanha(insights_da_conta)

                # fb_campaign_id -> id local. Necessário para casar as linhas do
                # breakdown por placement (que vêm no nível da CONTA, numa chamada só)
                # com as campanhas que acabamos de fazer upsert.
                campaign_id_by_fb_id: dict[str, int] = {}

                for c in campaigns:
                    fb_campaign_id = str(c.get("id") or "")
                    if not fb_campaign_id:
                        continue

                    campaign_fields = {
                        "ad_account_id": ad_account_id,
                        "name": c.get("name") or "(sem nome)",
                        "objective": c.get("objective"),
                        "status": c.get("status"),
                        "effective_status": c.get("effective_status"),
                        "daily_budget": _budget_to_brl(c.get("daily_budget")),
                        "lifetime_budget": _budget_to_brl(c.get("lifetime_budget")),
                    }
                    # Só atualiza ad_review_issue quando a busca de anúncios funcionou —
                    # senão uma falha transiente nessa chamada apagaria um problema real
                    # já detectado num sync anterior (upsert sobrescreve incondicionalmente).
                    if ads_fetch_ok:
                        campaign_fields["ad_review_issue"] = derive_ad_review_issue(
                            c.get("effective_status"),
                            ads_by_campaign.get(fb_campaign_id, []),
                        )

                    campaign = camp_repo.upsert_campaign(
                        user_id=user_id,
                        fb_campaign_id=fb_campaign_id,
                        fields=campaign_fields,
                    )
                    campaign_id_by_fb_id[fb_campaign_id] = campaign.id

                    # Insights diários: UPSERT da janela (preserva histórico, atualiza valores).
                    rows: list[CampaignDailyInsight] = []
                    for ins in insights_por_campanha.get(fb_campaign_id, []):
                        day_str = ins.get("date_start")
                        try:
                            day = date.fromisoformat(day_str)
                        except (TypeError, ValueError):
                            continue
                        rows.append(
                            CampaignDailyInsight(
                                user_id=user_id,
                                campaign_id=campaign.id,
                                fb_campaign_id=fb_campaign_id,
                                date=day,
                                spend=_to_float(ins.get("spend")),
                                # Cliques no link (alinha com o Gerenciador do Meta), fallback p/ clicks.
                                clicks=_to_int(ins.get("inline_link_clicks") or ins.get("clicks")),
                                impressions=_to_int(ins.get("impressions")),
                                cpc=_to_float(ins.get("cost_per_inline_link_click") or ins.get("cpc")) or None,
                                ctr=_to_float(ins.get("inline_link_click_ctr") or ins.get("ctr")) or None,
                                reach=_to_int(ins.get("reach")) or None,
                            )
                        )
                    insights_upserted += camp_repo.upsert_insights(rows)
                    processed += 1

                # Breakdown por placement: UMA chamada por CONTA (level=campaign),
                # não por campanha — o custo em rate limit não cresce com o número de
                # campanhas. Falha aqui NÃO derruba o sync: o gasto total já foi salvo
                # acima e a tela de placement simplesmente não atualiza nesta rodada.
                if not placement_disponivel:
                    continue

                try:
                    platform_rows_raw = await fb.get_account_campaign_platform_insights(
                        token, ad_account_id, platform_since.isoformat(), until.isoformat()
                    )
                except HTTPException as exc:
                    logger.warning(
                        "Facebook insights por placement falhou user_id=%s conta=%s: %s",
                        user_id, ad_account_id, exc.detail,
                    )
                    platform_rows_raw = []

                platform_rows: list[CampaignPlatformDailyInsight] = []
                for ins in platform_rows_raw:
                    fb_cid = str(ins.get("campaign_id") or "")
                    local_id = campaign_id_by_fb_id.get(fb_cid)
                    if not local_id:
                        # Campanha fora da lista sincronizada (ex.: DELETED). Ignorar é
                        # correto: sem linha em `campaigns` não há FK pra apontar.
                        continue
                    try:
                        day = date.fromisoformat(ins.get("date_start"))
                    except (TypeError, ValueError):
                        continue
                    platform_rows.append(
                        CampaignPlatformDailyInsight(
                            user_id=user_id,
                            campaign_id=local_id,
                            fb_campaign_id=fb_cid,
                            date=day,
                            publisher_platform=fb.normalize_publisher_platform(
                                ins.get("publisher_platform")
                            ),
                            spend=_to_float(ins.get("spend")),
                            clicks=_to_int(ins.get("inline_link_clicks") or ins.get("clicks")),
                            impressions=_to_int(ins.get("impressions")),
                            cpc=_to_float(ins.get("cost_per_inline_link_click") or ins.get("cpc")) or None,
                            ctr=_to_float(ins.get("inline_link_click_ctr") or ins.get("ctr")) or None,
                            reach=_to_int(ins.get("reach")) or None,
                        )
                    )
                if platform_rows:
                    try:
                        with db.begin_nested():
                            saved = camp_repo.upsert_platform_insights(platform_rows)
                        placement_upserted += saved
                        logger.info(
                            "Placement insights user_id=%s conta=%s: %d linhas",
                            user_id, ad_account_id, saved,
                        )
                    except SQLAlchemyError as exc:
                        placement_disponivel = False
                        logger.warning(
                            "Placement por plataforma não gravado user_id=%s conta=%s: %s",
                            user_id, ad_account_id, exc,
                        )

            # Espelha o gasto do Meta na tabela AdSpend (fonte do gasto do Dashboard).
            # AdSpend = projeção pura dos insights; o lançamento manual foi descontinuado.
            mirrored = camp_repo.rebuild_ad_spend_from_meta(user_id)
            self.repo.update_last_sync(user_id)
            db.commit()
            logger.info("AdSpend espelhado do Meta user_id=%s: %d linhas", user_id, mirrored)
            logger.info(
                "Facebook sync concluído user_id=%s: %d campanhas, %d insights, "
                "%d linhas de placement (%d contas)",
                user_id, processed, insights_upserted, placement_upserted, len(account_ids),
            )

            suspeita_parcial = insight_vazio_com_entrega(insights_upserted, placement_upserted)
            detalhes = None
            if suspeita_parcial:
                detalhes = {
                    "insights_vazios_com_placement": {
                        "campanhas": processed,
                        "linhas_placement": placement_upserted,
                        "janela": f"{since.isoformat()}..{until.isoformat()}",
                    }
                }
                logger.warning(
                    "Facebook sync user_id=%s: nenhum insight gravado, mas o placement "
                    "trouxe %d linhas — o gasto NÃO está entrando na tela",
                    user_id, placement_upserted,
                )

            if run_id is not None:
                try:
                    # records_fetched = campanhas listadas; records_upserted = linhas de
                    # insight de fato gravadas. Antes os dois eram "campanhas", e o painel
                    # mostrava 72/72 com zero gasto entrando.
                    run_repo.mark_success(
                        run_id,
                        records_fetched=processed,
                        records_upserted=insights_upserted,
                        is_suspected_partial=suspeita_parcial,
                        details=detalhes,
                    )
                except Exception:
                    pass
            return processed
        except Exception as exc:
            db.rollback()
            logger.error("Facebook sync falhou user_id=%s: %s", user_id, exc)
            if run_id is not None:
                try:
                    run_repo.mark_failed(run_id, str(exc))
                except Exception:
                    pass
            raise


async def run_facebook_sync_all(trigger: str = "cron") -> dict:
    """Sincroniza TODOS os usuários Facebook ativos INLINE — sem Celery/worker.

    Disparado pelo cron do Supabase (pg_cron → pg_net → POST /internal/cron/facebook-sync),
    que agenda esta função num BackgroundTask do FastAPI: roda no próprio processo da API.
    Falha de um usuário não derruba os demais (cada sync_user é atômico e dá commit por user).
    """
    from app.core.ambiente import is_homologacao
    from app.core.sync_gate import sync_liberado_para
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.repositories.facebook_integration_repository import FacebookIntegrationRepository

    # Lista os usuários ativos numa sessão curta, depois sincroniza cada um na SUA PRÓPRIA
    # sessão (isola falha e evita estado cruzado entre usuários após commit/rollback).
    db0 = SessionLocal()
    skipped_gate = 0
    try:
        user_ids = [i.user_id for i in FacebookIntegrationRepository(db0).get_all_active()]
        # Em homologação o cron sincroniza só quem está liberado (ver
        # app/core/sync_gate.py). Os e-mails vêm em UMA query — fora de
        # homologação nem essa query roda, o custo em produção é zero.
        if is_homologacao() and user_ids:
            emails = dict(db0.query(User.id, User.email).filter(User.id.in_(user_ids)).all())
            liberados = [uid for uid in user_ids if sync_liberado_para(emails.get(uid))]
            skipped_gate = len(user_ids) - len(liberados)
            user_ids = liberados
    finally:
        db0.close()

    synced = 0
    for uid in user_ids:
        db = SessionLocal()
        try:
            svc = FacebookIntegrationService(FacebookIntegrationRepository(db))
            await svc.sync_user(uid, db, trigger=trigger)
            synced += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("FB sync inline falhou user_id=%s: %s", uid, exc)
            try:
                from app.models.sync_error_log import SyncErrorLog

                db.add(SyncErrorLog(user_id=uid, source="facebook", error_message=str(exc)[:2000]))
                db.commit()
            except Exception:
                db.rollback()
        finally:
            db.close()
    logger.info(
        "FB sync inline (pg_cron, sem worker): %d/%d usuários skipped_gate=%d",
        synced, len(user_ids), skipped_gate,
    )
    return {"synced": synced, "total": len(user_ids), "skipped_gate": skipped_gate}
