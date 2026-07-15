"""Diagnóstico end-to-end do sync de campanhas do Facebook.

Roda FORA do Celery, dentro do mesmo ambiente do worker, e imprime o erro real
de cada etapa que normalmente falha em silêncio:

  1. Integrações Facebook no banco (user_id, is_active, contas, last_sync_at)
  2. RLS: a query enxerga as linhas sem app.current_user_id setado?
  3. Descriptografia do token (chave Fernet / SHOPEE_ENCRYPTION_KEY)
  4. Graph API: me / ad-accounts / campaigns (permissão, App Review, token)
  5. sync_user na mão (traceback completo se estourar)
  6. Contagem de campaigns / insights gravados no banco

Uso (rode DENTRO do container worker, que é o ambiente que falha):

    docker-compose exec worker python scripts/diagnose_facebook.py
    # ou para um usuário específico:
    docker-compose exec worker python scripts/diagnose_facebook.py --user-id 123
"""

import argparse
import asyncio
import sys
import traceback

from sqlalchemy import text

from app.core.encryption import decrypt_value
from app.db.session import SessionLocal
from app.models.campaign import Campaign, CampaignDailyInsight
from app.models.facebook_integration import FacebookIntegration
from app.repositories.facebook_integration_repository import FacebookIntegrationRepository
from app.services import facebook_marketing_client as fb
from app.services.facebook_integration_service import FacebookIntegrationService


def hr(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def diagnose_user(db, integ: FacebookIntegration, read_only: bool = False) -> None:
    uid = integ.user_id
    hr(f"USER {uid} — is_active={integ.is_active}  last_sync_at={integ.last_sync_at}")
    account_ids = integ.account_ids_list()
    print(f"  ad_accounts_json = {integ.ad_accounts_json}")
    print(f"  ad_account_id    = {integ.ad_account_id}")
    print(f"  account_ids_list = {account_ids}")
    print(f"  token_expires_at = {integ.token_expires_at}")

    if not account_ids:
        print("  >>> PROBLEMA: nenhuma conta selecionada -> sync_user retorna 0 e nem grava last_sync.")
        return

    # 3. Descriptografia do token --------------------------------------------
    hr(f"[{uid}] 3. Descriptografar token (Fernet / SHOPEE_ENCRYPTION_KEY)")
    try:
        token = decrypt_value(integ.encrypted_access_token)
        print(f"  OK — token descriptografado ({len(token)} chars).")
    except Exception:
        print("  >>> FALHA AO DESCRIPTOGRAFAR. Causa provável: SHOPEE_ENCRYPTION_KEY do worker")
        print("      e diferente da que o container `app` usou ao salvar o token.")
        traceback.print_exc()
        return

    # 4. Graph API ------------------------------------------------------------
    hr(f"[{uid}] 4. Graph API direta (me / ad-accounts / campaigns)")
    try:
        me = asyncio.run(fb.get_me(token))
        print(f"  me: {me}")
    except Exception:
        print("  >>> /me falhou (token invalido/expirado?):")
        traceback.print_exc()
    try:
        accs = asyncio.run(fb.list_ad_accounts(token))
        print(f"  ad-accounts acessiveis pelo token: {len(accs)}")
        for a in accs:
            print(f"    - act_{a.get('account_id')}  {a.get('name')}  status={a.get('account_status')}")
    except Exception:
        print("  >>> list_ad_accounts falhou:")
        traceback.print_exc()
    for acc in account_ids:
        try:
            camps = asyncio.run(fb.list_campaigns(token, acc))
            print(f"  campaigns em {acc}: {len(camps)}")
            for c in camps[:3]:
                print(f"    - {c.get('id')}  {c.get('name')}  {c.get('effective_status')}")
        except Exception:
            print(f"  >>> list_campaigns({acc}) falhou — provavel permissao/App Review:")
            traceback.print_exc()

    # 5. sync_user na mao -----------------------------------------------------
    if read_only:
        hr(f"[{uid}] 5. sync_user — PULADO (modo --read-only, nao escreve no banco)")
    else:
        hr(f"[{uid}] 5. Rodar sync_user (mesmo codigo do Celery, sincrono)")
        try:
            svc = FacebookIntegrationService(FacebookIntegrationRepository(db))
            processed = asyncio.run(svc.sync_user(uid, db))
            print(f"  sync_user retornou: {processed} campanhas processadas")
        except Exception:
            print("  >>> sync_user ESTOUROU (e isto que faz a Celery task morrer e last_sync ficar nulo):")
            traceback.print_exc()

    # 6. Contagem no banco ----------------------------------------------------
    hr(f"[{uid}] 6. O que ficou gravado no banco")
    n_camp = db.query(Campaign).filter(Campaign.user_id == uid).count()
    n_ins = db.query(CampaignDailyInsight).filter(CampaignDailyInsight.user_id == uid).count()
    print(f"  campaigns: {n_camp}")
    print(f"  campaign_daily_insights: {n_ins}")
    if n_camp > 0:
        print("  >>> Campanhas EXISTEM no banco. Se a tela continua vazia, o problema esta")
        print("      no GET /campaigns (RLS sem current_user_id, user_id divergente ou filtro de data).")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--read-only", action="store_true", help="Nao roda sync_user (nao escreve no banco).")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # 2. RLS: o worker enxerga as linhas sem setar app.current_user_id?
        hr("2. RLS / visibilidade (SessionLocal cru, igual ao worker)")
        cur = db.execute(text("SELECT current_setting('app.current_user_id', true)")).scalar()
        print(f"  app.current_user_id atual = {cur!r} (None/'' => policy nega tudo SE o role respeitar RLS)")
        raw_count = db.execute(text("SELECT count(*) FROM facebook_integrations")).scalar()
        print(f"  SELECT count(*) FROM facebook_integrations = {raw_count}")
        repo = FacebookIntegrationRepository(db)
        orm_count = len(repo.get_all_active())
        print(f"  repo.get_all_active() (ORM) = {orm_count}")
        if raw_count and orm_count == 0:
            print("  >>> RLS esta bloqueando o worker! Linhas existem mas o ORM nao ve nenhuma.")
            print("      O worker precisa SET LOCAL app.current_user_id antes de ler/gravar.")

        if args.user_id:
            integ = repo.get_by_user_id(args.user_id)
            integs = [integ] if integ else []
        else:
            integs = repo.get_all_active()

        if not integs:
            print("\n>>> Nenhuma integracao ativa encontrada pelo ORM. Veja o aviso de RLS acima.")
            sys.exit(0)

        for integ in integs:
            diagnose_user(db, integ, read_only=args.read_only)
    finally:
        db.close()


if __name__ == "__main__":
    main()
