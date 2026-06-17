import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.encryption import decrypt_value, encrypt_value
from app.models.campaign import CampaignDailyInsight
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


def _budget_to_brl(raw) -> Optional[float]:
    """daily_budget/lifetime_budget vêm em centavos (string) na Graph API."""
    if raw is None or raw == "":
        return None
    return _to_float(raw) / 100.0


class FacebookIntegrationService:
    def __init__(self, repo: FacebookIntegrationRepository):
        self.repo = repo
        self.db: Session = repo.db

    @staticmethod
    def _to_response(integration) -> FacebookIntegrationResponse:
        """Monta a resposta incluindo a lista de contas (ad_account_ids)."""
        resp = FacebookIntegrationResponse.model_validate(integration)
        resp.ad_account_ids = integration.account_ids_list()
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

    def _access_token(self, user_id: int) -> str:
        integration = self.repo.get_by_user_id(user_id)
        if not integration or not integration.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Integração Facebook não configurada ou inativa.",
            )
        return decrypt_value(integration.encrypted_access_token)

    async def list_ad_accounts(self, user_id: int) -> list[FacebookAdAccount]:
        token = self._access_token(user_id)
        raw = await fb.list_ad_accounts(token)
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
        return self._to_response(integration)

    def get_status(self, user_id: int) -> Optional[FacebookIntegrationResponse]:
        integration = self.repo.get_by_user_id(user_id)
        if not integration:
            return None
        return self._to_response(integration)

    def disconnect(self, user_id: int) -> None:
        deleted = self.repo.delete_by_user_id(user_id)
        self.db.commit()
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integração Facebook não encontrada.")

    # ------------------------------------------------------------------ #
    #  Escrita (pausar/ativar, orçamento)                                 #
    # ------------------------------------------------------------------ #

    async def set_campaign_status(self, user_id: int, fb_campaign_id: str, active: bool) -> str:
        token = self._access_token(user_id)
        new_status = "ACTIVE" if active else "PAUSED"
        await fb.update_campaign_status(token, fb_campaign_id, new_status)
        return new_status

    async def set_campaign_budget(self, user_id: int, fb_campaign_id: str, daily_budget_brl: float) -> float:
        token = self._access_token(user_id)
        await fb.update_campaign_daily_budget(token, fb_campaign_id, daily_budget_brl)
        return daily_budget_brl

    # ------------------------------------------------------------------ #
    #  Sincronização (campanhas + insights diários)                       #
    # ------------------------------------------------------------------ #

    async def sync_user(self, user_id: int, db: Session) -> int:
        """Sincroniza campanhas e insights diários da conta selecionada.

        Retorna o número de campanhas processadas. Atualiza last_sync_at.
        """
        integration = self.repo.get_by_user_id(user_id)
        account_ids = integration.account_ids_list() if integration else []
        if not integration or not integration.is_active or not account_ids:
            logger.info("Facebook sync ignorado user_id=%s (sem integração/conta ativa)", user_id)
            return 0

        token = decrypt_value(integration.encrypted_access_token)
        camp_repo = CampaignRepository(db)

        # Serializa syncs concorrentes do MESMO usuário (cron inline + botão manual/worker) —
        # evita corrida no rebuild do AdSpend (delete+insert). Lock por-transação, liberado no commit.
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": int(user_id)})

        now = datetime.now(BRT)
        # 1ª carga (sem last_sync_at): backfill de 90 dias. Ciclos seguintes: só os ~3 dias
        # quentes (o upsert preserva o histórico já gravado). Banco = fonte de verdade.
        window_days = SYNC_WINDOW_DAYS if integration.last_sync_at is None else INCREMENTAL_WINDOW_DAYS
        since = (now - timedelta(days=window_days)).date()
        until = now.date()

        processed = 0
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

            for c in campaigns:
                fb_campaign_id = str(c.get("id") or "")
                if not fb_campaign_id:
                    continue

                campaign = camp_repo.upsert_campaign(
                    user_id=user_id,
                    fb_campaign_id=fb_campaign_id,
                    fields={
                        "ad_account_id": ad_account_id,
                        "name": c.get("name") or "(sem nome)",
                        "objective": c.get("objective"),
                        "status": c.get("status"),
                        "effective_status": c.get("effective_status"),
                        "daily_budget": _budget_to_brl(c.get("daily_budget")),
                        "lifetime_budget": _budget_to_brl(c.get("lifetime_budget")),
                    },
                )

                # Insights diários: UPSERT da janela (preserva histórico, atualiza valores).
                try:
                    insights = await fb.get_campaign_insights(
                        token, fb_campaign_id, since.isoformat(), until.isoformat()
                    )
                except HTTPException as exc:
                    logger.warning(
                        "Facebook insights falhou user_id=%s campaign=%s: %s",
                        user_id, fb_campaign_id, exc.detail,
                    )
                    insights = []

                rows: list[CampaignDailyInsight] = []
                for ins in insights:
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
                camp_repo.upsert_insights(rows)
                processed += 1

        # Espelha o gasto do Meta na tabela AdSpend (fonte do gasto do Dashboard).
        # AdSpend = projeção pura dos insights; o lançamento manual foi descontinuado.
        mirrored = camp_repo.rebuild_ad_spend_from_meta(user_id)
        self.repo.update_last_sync(user_id)
        db.commit()
        logger.info("AdSpend espelhado do Meta user_id=%s: %d linhas", user_id, mirrored)
        logger.info(
            "Facebook sync concluído user_id=%s: %d campanhas (%d contas)",
            user_id, processed, len(account_ids),
        )
        return processed


async def run_facebook_sync_all() -> dict:
    """Sincroniza TODOS os usuários Facebook ativos INLINE — sem Celery/worker.

    Disparado pelo cron do Supabase (pg_cron → pg_net → POST /internal/cron/facebook-sync),
    que agenda esta função num BackgroundTask do FastAPI: roda no próprio processo da API.
    Falha de um usuário não derruba os demais (cada sync_user é atômico e dá commit por user).
    """
    from app.db.session import SessionLocal
    from app.repositories.facebook_integration_repository import FacebookIntegrationRepository

    # Lista os usuários ativos numa sessão curta, depois sincroniza cada um na SUA PRÓPRIA
    # sessão (isola falha e evita estado cruzado entre usuários após commit/rollback).
    db0 = SessionLocal()
    try:
        user_ids = [i.user_id for i in FacebookIntegrationRepository(db0).get_all_active()]
    finally:
        db0.close()

    synced = 0
    for uid in user_ids:
        db = SessionLocal()
        try:
            svc = FacebookIntegrationService(FacebookIntegrationRepository(db))
            await svc.sync_user(uid, db)
            synced += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("FB sync inline falhou user_id=%s: %s", uid, exc)
            db.rollback()
        finally:
            db.close()
    logger.info("FB sync inline (pg_cron, sem worker): %d/%d usuários", synced, len(user_ids))
    return {"synced": synced, "total": len(user_ids)}
