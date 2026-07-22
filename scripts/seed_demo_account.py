#!/usr/bin/env python3
"""
Seed re-executável da conta demo (produção).

Uso:
  cd marketdash-backend
  python scripts/seed_demo_account.py

Pré-requisito: usuário auth `demo@marketdash.com.br` já existir no Supabase Auth
e uma linha correspondente em `users` (ou o script cria o registro local se
SUPABASE_SERVICE_ROLE_KEY estiver disponível — caso contrário exige user local).

O CSV em scripts/data/demo_marketdash_dados.csv tem datas absolutas; este script
desloca todas para que o último dia = ontem (BRT).
"""
from __future__ import annotations

import csv
import hashlib
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Ensure app imports work when run as script
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.ad_spend import AdSpend
from app.models.campaign import Campaign, CampaignDailyInsight
from app.models.click_row import ClickRow
from app.models.dataset import Dataset
from app.models.dataset_row import DatasetRow
from app.models.facebook_integration import FacebookIntegration
from app.models.subscription import Subscription
from app.models.user import User
from app.models.user_settings import UserSettings

DEMO_EMAIL = "demo@marketdash.com.br"
DEMO_NAME = "MarketDash Demo"
CSV_PATH = Path(__file__).resolve().parent / "data" / "demo_marketdash_dados.csv"
BRT = timezone(timedelta(hours=-3))
DIRECT_ATTR = "ORDERED_IN_SAME_SHOP"
INDIRECT_ATTR = "ORDERED_IN_DIFFERENT_SHOP"


def _parse_float(v: str) -> float:
    try:
        return float(str(v).replace(",", ".").strip() or 0)
    except ValueError:
        return 0.0


def _parse_int(v: str) -> int:
    try:
        return int(float(str(v).strip() or 0))
    except ValueError:
        return 0


def _ensure_user(db: Session) -> User:
    user = db.query(User).filter(User.email.ilike(DEMO_EMAIL)).first()
    if not user:
        # Senha dummy — login real é via Supabase Auth; hashed_password é obrigatório.
        user = User(
            email=DEMO_EMAIL,
            name=DEMO_NAME,
            hashed_password="demo-no-local-login",
            is_active=True,
            is_demo=True,
        )
        db.add(user)
        db.flush()
        print(f"Criado user local id={user.id} — confirme que existe no Supabase Auth.")
    else:
        user.name = DEMO_NAME
        user.is_demo = True
        user.is_active = True
    return user


def _ensure_subscription(db: Session, user: User) -> None:
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    far = datetime.now(timezone.utc) + timedelta(days=3650)
    if not sub:
        sub = Subscription(user_id=user.id)
        db.add(sub)
    sub.plan = "pro"
    sub.is_active = True
    sub.plano_periodo = "anual"
    sub.assinatura_status = "ativa"
    sub.assinatura_vence_em = far
    sub.expires_at = far
    sub.provider = "demo"


def _ensure_settings(db: Session, user: User) -> None:
    s = db.query(UserSettings).filter(UserSettings.user_id == user.id).first()
    if not s:
        s = UserSettings(user_id=user.id)
        db.add(s)
    s.ad_tax_rate = 13.83
    s.commission_tax_rate = 6.0


def _ensure_fake_facebook(db: Session, user: User) -> None:
    """Integração placeholder — token dummy; sync é pulado via is_demo."""
    from app.core.encryption import encrypt_value

    integ = db.query(FacebookIntegration).filter(FacebookIntegration.user_id == user.id).first()
    if not integ:
        integ = FacebookIntegration(
            user_id=user.id,
            encrypted_access_token=encrypt_value("demo-placeholder-token"),
        )
        db.add(integ)
    else:
        if not integ.encrypted_access_token:
            integ.encrypted_access_token = encrypt_value("demo-placeholder-token")
    integ.is_active = True
    integ.fb_user_name = "MarketDash Demo"
    integ.ad_account_id = "act_demo"
    integ.ad_account_name = "Conta Demo"
    integ.ad_accounts_json = '["act_demo"]'


def _wipe_demo_data(db: Session, user_id: int) -> None:
    db.query(CampaignDailyInsight).filter(CampaignDailyInsight.user_id == user_id).delete(synchronize_session=False)
    db.query(Campaign).filter(Campaign.user_id == user_id).delete(synchronize_session=False)
    db.query(AdSpend).filter(AdSpend.user_id == user_id).delete(synchronize_session=False)
    db.query(ClickRow).filter(ClickRow.user_id == user_id).delete(synchronize_session=False)
    # Datasets + rows (cascade)
    datasets = db.query(Dataset).filter(Dataset.user_id == user_id).all()
    for ds in datasets:
        db.query(DatasetRow).filter(DatasetRow.dataset_id == ds.id).delete(synchronize_session=False)
        db.delete(ds)
    db.flush()


def _load_csv_shifted() -> list[dict]:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CSV não encontrado: {CSV_PATH}")

    rows = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    dates = sorted({datetime.strptime(r["data"][:10], "%Y-%m-%d").date() for r in rows})
    if not dates:
        raise RuntimeError("CSV vazio")
    csv_last = dates[-1]
    yesterday = datetime.now(BRT).date() - timedelta(days=1)
    delta = yesterday - csv_last
    print(f"Deslocando datas: CSV last={csv_last} → yesterday={yesterday} (delta={delta.days}d)")

    out = []
    for r in rows:
        d = datetime.strptime(r["data"][:10], "%Y-%m-%d").date() + delta
        nr = dict(r)
        nr["_date"] = d
        out.append(nr)
    return out


def _seed_from_rows(db: Session, user: User, rows: list[dict]) -> None:
    # Dataset de comissões
    ds = Dataset(
        user_id=user.id,
        filename="demo_seed_commissions.csv",
        type="transaction",
        status="completed",
        row_count=0,
    )
    db.add(ds)
    db.flush()

    # Campanhas únicas por sub_id
    camp_meta: dict[str, dict] = {}
    for r in rows:
        sid = r["sub_id"].strip()
        if sid not in camp_meta:
            camp_meta[sid] = {
                "name": r["campanha"].strip(),
                "categoria": r["categoria"].strip(),
                "status": r["status"].strip(),
                "orcamento": _parse_float(r["orcamento_dia"]),
            }

    campaigns: dict[str, Campaign] = {}
    for i, (sid, meta) in enumerate(camp_meta.items(), start=1):
        status = "ACTIVE" if meta["status"].lower().startswith("ativ") else "PAUSED"
        c = Campaign(
            user_id=user.id,
            fb_campaign_id=f"demo_{sid}",
            ad_account_id="act_demo",
            name=meta["name"],
            status=status,
            effective_status=status,
            daily_budget=meta["orcamento"],
            sub_id=sid,
            last_synced_at=datetime.now(timezone.utc),
        )
        db.add(c)
        db.flush()
        campaigns[sid] = c

    insight_count = 0
    commission_rows = 0
    spend_rows = 0

    for r in rows:
        d: date = r["_date"]
        sid = r["sub_id"].strip()
        camp = campaigns[sid]
        spend = _parse_float(r["gasto_sem_imposto"])
        clicks = _parse_int(r["cliques"])
        impressions = _parse_int(r["impressoes"])
        cpc = _parse_float(r["cpc"]) if r.get("cpc") else (spend / clicks if clicks else None)

        db.add(
            CampaignDailyInsight(
                user_id=user.id,
                campaign_id=camp.id,
                fb_campaign_id=camp.fb_campaign_id,
                date=d,
                spend=spend,
                clicks=clicks,
                impressions=impressions,
                cpc=cpc,
            )
        )
        insight_count += 1

        # AdSpend (source=meta) — gasto sem imposto
        if spend > 0:
            db.add(
                AdSpend(
                    user_id=user.id,
                    date=d,
                    sub_id=sid,
                    amount=spend,
                    clicks=clicks,
                    source="meta",
                )
            )
            spend_rows += 1

        # Comissões: N pedidos com order_ids distintos; diretos vs cookie
        pedidos = _parse_int(r["pedidos"])
        diretos = min(_parse_int(r["diretos"]), pedidos)
        com_bruta = _parse_float(r["comissao_bruta"])
        # Channel splits (origem)
        channels = [
            ("Instagram", _parse_float(r.get("com_instagram", "0"))),
            ("WhatsApp", _parse_float(r.get("com_whatsapp", "0"))),
            ("Outros", _parse_float(r.get("com_outros", "0"))),
        ]
        # Se splits vazios, tudo em Instagram
        if sum(c[1] for c in channels) <= 0 and com_bruta > 0:
            channels = [("Instagram", com_bruta)]

        if pedidos <= 0 and com_bruta > 0:
            pedidos = 1
            diretos = 0

        per_order_comm = (com_bruta / pedidos) if pedidos else 0.0
        # Distribui canais proporcionalmente pelos pedidos
        channel_pool = []
        for ch, amt in channels:
            if amt <= 0:
                continue
            n = max(1, round(pedidos * (amt / com_bruta))) if com_bruta else 1
            channel_pool.extend([ch] * n)
        while len(channel_pool) < pedidos:
            channel_pool.append(channels[0][0])
        channel_pool = channel_pool[:pedidos]

        for i in range(pedidos):
            order_id = f"demo-{sid}-{d.isoformat()}-{i+1}"
            attr = DIRECT_ATTR if i < diretos else INDIRECT_ATTR
            ch = channel_pool[i]
            # Ajusta comissão do canal neste pedido
            row_comm = per_order_comm
            h = hashlib.md5(f"{user.id}:{order_id}".encode()).hexdigest()
            db.add(
                DatasetRow(
                    dataset_id=ds.id,
                    user_id=user.id,
                    date=d,
                    platform="Shopee",
                    category=r["categoria"].strip(),
                    product=camp.name[:200],
                    status="COMPLETED",
                    channel=ch,
                    attribution_type=attr,
                    sub_id1=sid,
                    order_id=order_id,
                    revenue=0,
                    commission=round(row_comm, 4),
                    quantity=1,
                    row_hash=h,
                )
            )
            commission_rows += 1

    ds.row_count = commission_rows
    print(
        f"Seed OK: campaigns={len(campaigns)} insights={insight_count} "
        f"ad_spends={spend_rows} commission_rows={commission_rows}"
    )


def main() -> int:
    db = SessionLocal()
    try:
        user = _ensure_user(db)
        _ensure_subscription(db, user)
        _ensure_settings(db, user)
        _ensure_fake_facebook(db, user)
        _wipe_demo_data(db, user.id)
        rows = _load_csv_shifted()
        _seed_from_rows(db, user, rows)
        db.commit()
        print(f"Demo account pronta: {DEMO_EMAIL} (user_id={user.id}, is_demo=true, plan=pro)")
        return 0
    except Exception as e:
        db.rollback()
        print(f"ERRO no seed demo: {e}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
