#!/usr/bin/env python3
"""
Import histórico de assinaturas/cobranças Kiwify (dados reais fornecidos pelo
Luiz, 09/08/2026) — cobre abril a agosto/2026, 43 assinaturas + 49 cobranças.

Uso:
  python scripts/import_kiwify_historico.py --dry-run   # não commita, só mostra o que faria
  python scripts/import_kiwify_historico.py              # roda de verdade
  python scripts/import_kiwify_historico.py --validate   # só imprime os números de aceite (sem importar)

Idempotente: dedupe_key único por linha (`import:assinatura:<cpf>:<inicio_iso>`
e `import:cobranca:<order_ref>`) — rodar 2x não duplica.

Rodar UMA VEZ e arquivar — não fica agendado, não roda de novo depois de
validado. Requer a migration 048 já aplicada (canceled_at/cancel_reason em
subscription_events) e os fixes de app/services/admin_metrics_service.py
(my_commission, arredondamento do MRR) já aplicados — os números de aceite
pressupõem os dois.

Nota de design importante: TODO evento gerado (tanto de assinaturas.csv quanto
de cobrancas.csv) carrega o mesmo "estado atual" (has_access/access_until/
subscription_status/next_payment) da pessoa — não só o evento de estado da
assinatura. Isso espelha o formato real de webhook da Kiwify (todo payload
carrega o estado da assinatura, não só eventos de cancelamento) e evita um bug
sutil: `_latest_by_subscriber()` prioriza eventos tipo order_approved/
subscription_canceled sobre um tipo novo como "import_subscription_active" —
se só o evento de assinatura carregasse o estado, uma cobrança mais recente
poderia "vencer" como latest e mascarar o has_access/access_until corretos.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.subscription_event import SubscriptionEvent
from app.repositories.user_repository import UserRepository

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("import_kiwify_historico")

BRT = ZoneInfo("America/Sao_Paulo")
ASSINATURAS_CSV = Path(__file__).resolve().parent / "data" / "import_assinaturas.csv"
COBRANCAS_CSV = Path(__file__).resolve().parent / "data" / "import_cobrancas.csv"

# Ajustes/testes do produtor (Luiz) — não representam saída real de cliente,
# não contam como "nova assinatura" (nenhuma cobrança real associada de qualquer
# forma, mas documentado explicitamente pra não confundir no futuro).
PRODUTOR_ADJUSTMENT_REASONS = {"cancelado pelo produtor"}


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _digits(value: str | None) -> str:
    return "".join(c for c in (value or "") if c.isdigit())


def _parse_brt(value: str | None) -> datetime | None:
    """'YYYY-MM-DD HH:MM' ou 'YYYY-MM-DD HH:MM:SS', assumido BRT → UTC aware."""
    v = (value or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(v, fmt)
            return dt.replace(tzinfo=BRT).astimezone(timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Não consegui parsear data BRT: {value!r}")


def carregar_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def carregar_assinaturas() -> list[dict]:
    linhas = []
    for r in carregar_csv(ASSINATURAS_CSV):
        linhas.append(
            {
                "nome": r["nome"].strip(),
                "email": r["email"].strip().lower(),
                "cpf": _digits(r["cpf"]),
                "telefone": r["telefone"].strip() or None,
                "plano": r["plano"].strip(),
                "periodicidade": r["periodicidade"].strip(),
                "liquido_cents": int(r["liquido_cents"]),
                "status": r["status"].strip().lower(),
                "inicio_brt": _parse_brt(r["inicio_brt"]),
                "ultima_cobranca_brt": _parse_brt(r["ultima_cobranca_brt"]),
                "acesso_ate_brt": _parse_brt(r["acesso_ate_brt"]),
                "cancelada_em_brt": _parse_brt(r["cancelada_em_brt"]),
                "motivo_cancelamento": r["motivo_cancelamento"].strip() or None,
                "ja_no_painel": r["ja_no_painel"].strip().lower() == "sim",
            }
        )
    return linhas


def carregar_cobrancas() -> list[dict]:
    linhas = []
    for r in carregar_csv(COBRANCAS_CSV):
        linhas.append(
            {
                "order_ref": r["order_ref"].strip(),
                "nome": r["nome"].strip(),
                "email": r["email"].strip().lower(),
                "cpf": _digits(r["cpf"]),
                "data_brt": _parse_brt(r["data_brt"]),
                "plano": r["plano"].strip(),
                "periodicidade": r["periodicidade"].strip(),
                "liquido_cents": int(r["liquido_cents"]),
                "bruto_cents": int(r["bruto_cents"]),
                "afiliado": r["afiliado"].strip() or None,
                "comissao_afiliado_cents": int(r["comissao_afiliado_cents"] or 0),
            }
        )
    return linhas


# --------------------------------------------------------------------------- #
# Pares de continuação/upgrade (Parte C.4 do plano) — puro, sem banco
# --------------------------------------------------------------------------- #


def calcular_pares_continuacao(
    linhas_assinaturas: list[dict], linhas_cobrancas: list[dict]
) -> tuple[dict[tuple, str], set[str]]:
    """Retorna (pares, cobrancas_de_novo_ciclo):

    - pares: {(cpf, inicio_brt_iso): 'continuacao'|'upgrade'} — pras linhas de
      ASSINATURA que devem ganhar is_plan_change=True (a cancelada e a nova).
    - cobrancas_de_novo_ciclo: {order_ref} — a cobrança que representa o
      primeiro pagamento do lado "novo" de cada par, também ganha
      is_plan_change=True (senão new_subscriptions() ainda contaria essa
      renovação como aquisição nova)."""
    por_cpf: dict[str, list[dict]] = defaultdict(list)
    for linha in linhas_assinaturas:
        if linha["cpf"]:
            por_cpf[linha["cpf"]].append(linha)

    marcadas: dict[tuple, str] = {}
    cobrancas_de_novo_ciclo: set[str] = set()
    for cpf, linhas in por_cpf.items():
        linhas_ordenadas = sorted(linhas, key=lambda l: l["inicio_brt"])
        for anterior, atual in zip(linhas_ordenadas, linhas_ordenadas[1:]):
            if not anterior.get("cancelada_em_brt"):
                continue
            gap = atual["inicio_brt"] - anterior["cancelada_em_brt"]
            if gap.total_seconds() < 0:
                logger.warning(
                    "GAP NEGATIVO cpf=%s: %s começa antes de %s cancelar — revisar manualmente",
                    cpf, atual["inicio_brt"], anterior["cancelada_em_brt"],
                )
                continue
            mesmo_plano = (
                anterior["plano"] == atual["plano"]
                and anterior["periodicidade"] == atual["periodicidade"]
            )
            if mesmo_plano and gap <= timedelta(days=1):
                tipo = "continuacao"
            elif not mesmo_plano and gap <= timedelta(days=30):
                tipo = "upgrade"
            else:
                continue
            marcadas[(cpf, anterior["inicio_brt"].isoformat())] = tipo
            marcadas[(cpf, atual["inicio_brt"].isoformat())] = tipo

            cobranca = _achar_cobranca_do_novo_ciclo(cpf, atual["inicio_brt"], linhas_cobrancas)
            if cobranca:
                cobrancas_de_novo_ciclo.add(cobranca["order_ref"])
            else:
                logger.info(
                    "Par %s (cpf=%s, inicio=%s) sem cobrança correspondente em "
                    "cobrancas.csv — ok se for ajuste do produtor sem pagamento real.",
                    tipo, cpf, atual["inicio_brt"].isoformat(),
                )
    return marcadas, cobrancas_de_novo_ciclo


def _achar_cobranca_do_novo_ciclo(
    cpf: str, inicio_brt: datetime, linhas_cobrancas: list[dict]
) -> dict | None:
    """Cobrança do mesmo CPF com data_brt mínima >= inicio_brt."""
    candidatas = [
        c for c in linhas_cobrancas if c["cpf"] == cpf and c["data_brt"] >= inicio_brt
    ]
    if not candidatas:
        return None
    return min(candidatas, key=lambda c: c["data_brt"])


# --------------------------------------------------------------------------- #
# Estado "atual" por CPF (Nota de design do docstring do módulo)
# --------------------------------------------------------------------------- #


def calcular_estado_por_cpf(linhas_assinaturas: list[dict]) -> dict[str, dict]:
    """{cpf: {has_access, access_until, subscription_status, next_payment,
    canceled_at, cancel_reason}} — a assinatura mais recente por CPF define o
    estado "atual" daquele CPF; esse estado é carimbado em TODO evento gerado
    pra aquele CPF (assinatura E cobrança), não só no evento de estado."""
    por_cpf: dict[str, list[dict]] = defaultdict(list)
    for linha in linhas_assinaturas:
        if linha["cpf"]:
            por_cpf[linha["cpf"]].append(linha)

    estado: dict[str, dict] = {}
    for cpf, linhas in por_cpf.items():
        mais_recente = max(linhas, key=lambda l: l["inicio_brt"])
        ativa = mais_recente["status"] == "active"
        acesso_ate = mais_recente["acesso_ate_brt"]
        estado[cpf] = {
            "has_access": True if acesso_ate else False,
            "access_until": acesso_ate,
            "next_payment": acesso_ate,
            "subscription_status": "active" if ativa else "canceled",
            "canceled_at": None if ativa else mais_recente["cancelada_em_brt"],
            "cancel_reason": None if ativa else mais_recente["motivo_cancelamento"],
        }
    return estado


# --------------------------------------------------------------------------- #
# Resolvers sem efeito colateral (Parte C.1)
# --------------------------------------------------------------------------- #


def resolver_user_id(user_repo: UserRepository, email: str, cpf: str) -> int | None:
    user = user_repo.get_by_email(email)
    if not user and cpf:
        user = user_repo.get_by_cpf(cpf)
    return user.id if user else None


def resolver_subscription_id_existente(db: Session) -> tuple[dict, dict]:
    rows = db.execute(
        text(
            "SELECT DISTINCT customer_cpf, customer_email, subscription_id "
            "FROM subscription_events WHERE subscription_id IS NOT NULL"
        )
    ).all()
    por_cpf = {r.customer_cpf: r.subscription_id for r in rows if r.customer_cpf}
    por_email = {r.customer_email: r.subscription_id for r in rows if r.customer_email}
    return por_cpf, por_email


# --------------------------------------------------------------------------- #
# Construção dos eventos
# --------------------------------------------------------------------------- #


class ImportContext:
    def __init__(self, db: Session):
        self.db = db
        self.user_repo = UserRepository(db)
        self.sub_id_por_cpf, self.sub_id_por_email = resolver_subscription_id_existente(db)
        self.existentes = {
            r[0]
            for r in db.query(SubscriptionEvent.dedupe_key)
            .filter(SubscriptionEvent.dedupe_key.like("import:%"))
            .all()
        }
        self.criados = 0
        self.pulados = 0
        self.avisos: list[str] = []

    def resolver_subscription_id(self, cpf: str, email: str) -> str | None:
        return self.sub_id_por_cpf.get(cpf) or self.sub_id_por_email.get(email)

    def inserir(self, dedupe_key: str, **campos) -> None:
        if dedupe_key in self.existentes:
            self.pulados += 1
            return
        row = SubscriptionEvent(dedupe_key=dedupe_key, **campos)
        self.db.add(row)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            logger.info("dedupe_key já existe (corrida) — pulando: %s", dedupe_key)
            self.pulados += 1
            return
        self.existentes.add(dedupe_key)
        self.criados += 1


def construir_evento_assinatura(
    ctx: ImportContext, linha: dict, estado_por_cpf: dict, pares: dict
) -> None:
    cpf, email = linha["cpf"], linha["email"]
    estado = estado_por_cpf.get(cpf, {})
    if not estado:
        ctx.avisos.append(f"Sem estado calculado pro CPF {cpf} ({linha['nome']}) — pulando linha.")
        return

    chave_par = (cpf, linha["inicio_brt"].isoformat())
    is_plan_change = chave_par in pares
    ativa = linha["status"] == "active"
    # Pra CANCELADA, received_at precisa ser QUANDO CANCELOU, não quando
    # começou — churn_for_month() filtra por received_at dentro do range do
    # mês; com inicio_brt, o cancelamento seria atribuído ao mês de início da
    # assinatura em vez do mês real do cancelamento (bug real, achado rodando
    # o dry-run: julho devia contar 6 cancelamentos e contava só os que por
    # coincidência começaram em julho).
    received_at = linha["inicio_brt"] if ativa else linha["cancelada_em_brt"]

    campos = dict(
        event_type="import_subscription_active" if ativa else "subscription_canceled",
        customer_email=email,
        customer_name=linha["nome"],
        customer_cpf=cpf,
        customer_phone=linha["telefone"],
        subscription_id=ctx.resolver_subscription_id(cpf, email),
        user_id=resolver_user_id(ctx.user_repo, email, cpf),
        plan_name=linha["plano"],
        plan_frequency=linha["periodicidade"],
        amount_net_cents=linha["liquido_cents"],
        subscription_start=linha["inicio_brt"],
        has_access=estado["has_access"],
        access_until=estado["access_until"],
        next_payment=estado["next_payment"],
        subscription_status=estado["subscription_status"],
        canceled_at=estado["canceled_at"],
        cancel_reason=estado["cancel_reason"],
        is_plan_change=is_plan_change,
        received_at=received_at,
        raw_payload={"_import_source": "import_assinaturas.csv", "_row": _linha_serializavel(linha)},
    )
    ctx.inserir(f"import:assinatura:{cpf}:{linha['inicio_brt'].isoformat()}", **campos)


def construir_evento_cobranca(
    ctx: ImportContext, linha: dict, estado_por_cpf: dict, cobrancas_de_novo_ciclo: set
) -> None:
    cpf, email = linha["cpf"], linha["email"]
    estado = estado_por_cpf.get(cpf, {})
    if not estado:
        ctx.avisos.append(
            f"Cobrança {linha['order_ref']} — sem estado calculado pro CPF {cpf} "
            f"({linha['nome']}), nenhuma linha correspondente em assinaturas.csv. Pulando."
        )
        return

    fee_cents = linha["bruto_cents"] - linha["liquido_cents"]
    data_iso = linha["data_brt"].isoformat()
    charges_completed = [
        {
            "order_id": linha["order_ref"],
            "status": "paid",
            "approved_date": data_iso,
            "Commissions": {
                "my_commission": linha["liquido_cents"],
                "charge_amount": linha["bruto_cents"],
                "kiwify_fee": fee_cents,
            },
        }
    ]
    campos = dict(
        event_type="order_approved",
        order_id=linha["order_ref"],
        order_ref=linha["order_ref"],
        customer_email=email,
        customer_name=linha["nome"],
        customer_cpf=cpf,
        subscription_id=ctx.resolver_subscription_id(cpf, email),
        user_id=resolver_user_id(ctx.user_repo, email, cpf),
        plan_name=linha["plano"],
        plan_frequency=linha["periodicidade"],
        amount_gross_cents=linha["bruto_cents"],
        fee_cents=fee_cents,
        amount_net_cents=linha["liquido_cents"],
        approved_date=linha["data_brt"],
        has_access=estado["has_access"],
        access_until=estado["access_until"],
        next_payment=estado["next_payment"],
        subscription_status=estado["subscription_status"],
        charges_completed=charges_completed,
        is_plan_change=linha["order_ref"] in cobrancas_de_novo_ciclo,
        received_at=linha["data_brt"],
        raw_payload={"_import_source": "import_cobrancas.csv", "_row": _linha_serializavel(linha)},
    )
    ctx.inserir(f"import:cobranca:{linha['order_ref']}", **campos)


def _linha_serializavel(linha: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in linha.items()}


# --------------------------------------------------------------------------- #
# Validação (Parte C.6)
# --------------------------------------------------------------------------- #


def validar(db: Session) -> None:
    from app.services.admin_metrics_service import AdminMetricsService

    svc = AdminMetricsService(db)
    dash = svc.dashboard(2026, 8)
    print(f"Ativos: {dash['active_count']} (esperado 28) — por plano: {dash['active_by_plan']} "
          f"(esperado essencial=5 pro=23 max=0)")
    print(f"MRR líquido: {dash['mrr_net_cents']/100:.2f} (esperado 1309.79)")
    for ano, mes, esperado in [
        (2026, 4, 377.70), (2026, 5, 1308.83), (2026, 6, 1133.55),
        (2026, 7, 896.49), (2026, 8, 621.30),
    ]:
        rev = svc.revenue_for_month(ano, mes)
        print(f"{mes:02d}/{ano}: {rev['net']/100:.2f} (esperado {esperado})")
    churn_jul = svc.churn_for_month(2026, 7)
    print(f"Churn julho: count={churn_jul['count']} rate={churn_jul['rate']}")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Faz tudo, mas dá rollback no final.")
    parser.add_argument("--validate", action="store_true", help="Só imprime os números de aceite, não importa nada.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.validate:
            validar(db)
            return 0

        linhas_assinaturas = carregar_assinaturas()
        linhas_cobrancas = carregar_cobrancas()
        logger.info("Carregado: %d assinaturas, %d cobranças", len(linhas_assinaturas), len(linhas_cobrancas))

        estado_por_cpf = calcular_estado_por_cpf(linhas_assinaturas)
        pares, cobrancas_de_novo_ciclo = calcular_pares_continuacao(linhas_assinaturas, linhas_cobrancas)
        logger.info("Pares de continuação/upgrade detectados: %s", pares or "nenhum")
        logger.info("Cobranças marcadas como novo ciclo (is_plan_change): %s", cobrancas_de_novo_ciclo or "nenhuma")

        ctx = ImportContext(db)

        for linha in linhas_assinaturas:
            construir_evento_assinatura(ctx, linha, estado_por_cpf, pares)
        for linha in linhas_cobrancas:
            construir_evento_cobranca(ctx, linha, estado_por_cpf, cobrancas_de_novo_ciclo)

        for aviso in ctx.avisos:
            logger.warning(aviso)

        logger.info("Eventos criados: %d — pulados (já existiam): %d", ctx.criados, ctx.pulados)

        if args.dry_run:
            logger.info("--dry-run: rollback (nada foi de fato salvo).")
            db.rollback()
        else:
            db.commit()
            logger.info("Commit realizado.")
            validar(db)
        return 0
    except Exception as e:
        db.rollback()
        print(f"ERRO no import: {e}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
