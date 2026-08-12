"""Gravação append-only de webhooks Kiwify em subscription_events."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.subscription_event import SubscriptionEvent
from app.models.user import User
from app.services.charges import unknown_array_charges

logger = logging.getLogger(__name__)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # epoch seconds
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # dd/mm/yyyy or ISO
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d/%m/%Y %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(s.replace("Z", "+0000") if fmt.endswith("%z") and s.endswith("Z") else s, fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _as_cents(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def build_dedupe_key(order_id: Optional[str], event_type: str, approved_date: Optional[datetime]) -> str:
    ad = approved_date.isoformat() if approved_date else ""
    return f"{order_id or ''}|{event_type or ''}|{ad}"


def extract_event_fields(payload: Dict[str, Any], event_type: str) -> Dict[str, Any]:
    order = payload.get("order") if isinstance(payload.get("order"), dict) else payload
    customer = order.get("Customer") or order.get("customer") or {}
    if not isinstance(customer, dict):
        customer = {}
    subscription = order.get("Subscription") or order.get("subscription") or {}
    if not isinstance(subscription, dict):
        subscription = {}
    plan = subscription.get("plan") or {}
    if not isinstance(plan, dict):
        plan = {}
    commissions = order.get("Commissions") or order.get("commissions") or {}
    if not isinstance(commissions, dict):
        commissions = {}
    access = subscription.get("customer_access") or {}
    if not isinstance(access, dict):
        access = {}
    charges = subscription.get("charges") or {}
    if not isinstance(charges, dict):
        charges = {}
    # Array cumulativo de cobranças já pagas dessa assinatura — cada entrada tem seu
    # próprio order_id (diferente do order_id do webhook em si). Fonte de verdade
    # pra "cobrança distinta" — ver migration 038.
    charges_completed = charges.get("completed")
    if not isinstance(charges_completed, list):
        charges_completed = None

    approved = _parse_dt(order.get("approved_date") or order.get("created_at"))
    refunded = _parse_dt(order.get("refunded_at"))
    access_until = _parse_dt(access.get("access_until") or subscription.get("access_until"))
    next_payment = _parse_dt(subscription.get("next_payment"))
    start_date = _parse_dt(subscription.get("start_date"))
    deposit = _parse_dt(commissions.get("deposit_date"))

    has_access = access.get("has_access")
    if has_access is None:
        has_access = None
    else:
        has_access = bool(has_access)

    email = (customer.get("email") or "").strip().lower() or None

    return {
        "event_type": (event_type or "").strip().lower() or "unknown",
        "order_id": order.get("order_id"),
        "order_ref": order.get("order_ref"),
        "order_status": order.get("order_status"),
        "subscription_id": order.get("subscription_id") or subscription.get("id"),
        "customer_email": email,
        "customer_name": customer.get("full_name") or customer.get("name"),
        "customer_cpf": customer.get("CPF") or customer.get("cpf"),
        "customer_phone": customer.get("mobile") or customer.get("phone"),
        "plan_id": str(plan.get("id")) if plan.get("id") is not None else None,
        "plan_name": plan.get("name"),
        "plan_frequency": (plan.get("frequency") or plan.get("charge_frequency") or "").lower() or None,
        "amount_gross_cents": _as_cents(commissions.get("charge_amount")),
        "fee_cents": _as_cents(commissions.get("kiwify_fee")),
        "amount_net_cents": _as_cents(commissions.get("my_commission")),
        "payment_method": order.get("payment_method"),
        "subscription_status": subscription.get("status"),
        "has_access": has_access,
        "access_until": access_until,
        "next_payment": next_payment,
        "subscription_start": start_date,
        "approved_date": approved,
        "refunded_at": refunded,
        "funds_status": commissions.get("funds_status"),
        "deposit_date": deposit,
        "charges_completed": charges_completed,
        "card_rejection_reason": (
            order.get("card_rejection_reason") or payload.get("card_rejection_reason")
        ),
        "dedupe_key": build_dedupe_key(order.get("order_id"), (event_type or "").strip().lower(), approved),
    }


PAID_LIKE_EVENTS = ("order_approved", "subscription_renewed", "compra_aprovada")


def encontrar_par_de_plan_change(
    db: Session, fields: Dict[str, Any], reference_time: Optional[datetime] = None
):
    """Par de upgrade/continuação do evento que está chegando — em QUALQUER ordem.

    Rodada 6, item 3. A regra antiga só olhava "cancelou antes, assinou depois".
    Ana Ariel assinou a Pro 8 minutos ANTES da Essencial ser cancelada, e o par
    nunca era detectado: ela contava como nova assinatura E como churn no mesmo
    mês. Agora a janela é de ±30 dias e vale nos dois sentidos.

    - Mesmo plano em ≤1 dia = continuação (trocou forma de pagamento). Direcional:
      só conta quando o CANCELAMENTO aconteceu ANTES (ou no mesmo instante) do
      PAGAMENTO — nunca o contrário. Renovação seguida de cancelamento de verdade
      horas depois (caso Girlene/Dayana) não é "continuação", é churn de verdade;
      só a bidirecionalidade do ramo de plano DIFERENTE (upgrade) foi pedida.
    - Plano diferente em ≤30 dias = upgrade/downgrade (bidirecional, qualquer ordem).

    `reference_time` (default `now()`) permite processar eventos HISTÓRICOS com a
    janela ancorada na data do próprio evento.
    """
    from datetime import timedelta

    cpf = fields.get("customer_cpf")
    if not cpf:
        return None

    evento = (fields.get("event_type") or "").lower()
    if evento in PAID_LIKE_EVENTS:
        procurado = ["subscription_canceled"]
    elif evento == "subscription_canceled":
        procurado = list(PAID_LIKE_EVENTS)
    else:
        return None

    agora = reference_time or datetime.now(timezone.utc)
    janela = timedelta(days=30)
    candidatos = (
        db.query(SubscriptionEvent)
        .filter(
            SubscriptionEvent.customer_cpf == cpf,
            SubscriptionEvent.received_at >= agora - janela,
            SubscriptionEvent.received_at <= agora + janela,
            SubscriptionEvent.event_type.in_(procurado),
        )
        .order_by(SubscriptionEvent.received_at.desc())
        .limit(20)
        .all()
    )

    for ev in candidatos:
        recebido = ev.received_at
        if recebido is not None and recebido.tzinfo is None:
            recebido = recebido.replace(tzinfo=timezone.utc)
        gap = abs(agora - (recebido or agora))
        mesmo_plano = (ev.plan_name or "") == (fields.get("plan_name") or "") and (
            ev.plan_frequency or ""
        ) == (fields.get("plan_frequency") or "")
        if mesmo_plano and gap <= timedelta(days=1):
            # Direcional: cancelamento tem que ter acontecido ANTES (ou junto)
            # do pagamento — nunca depois. `evento` é o tipo do evento que está
            # chegando (`agora`); `ev` é o candidato (`recebido`). Um dos dois é
            # o cancelamento e o outro é o pago — descobre qual é qual pra
            # comparar os timestamps reais, não a ordem de chegada/processamento.
            if evento == "subscription_canceled":
                cancelado_em, pago_em = agora, recebido
            else:
                cancelado_em, pago_em = recebido, agora
            if cancelado_em is not None and pago_em is not None and cancelado_em <= pago_em:
                return ev
            continue
        if not mesmo_plano and gap <= janela:
            return ev
    return None


def alertar_cobrancas_desconhecidas(db: Session, ev) -> list:
    """O array `charges.completed` não insere nada — só denuncia buraco no histórico.

    Rodada 6, item 1. Antes, cada entrada do array virava cobrança, o que
    duplicava tudo que também veio pelo import. Agora, se o array cita uma
    cobrança que não temos como evento, é sinal de webhook perdido — alguém
    precisa olhar, mas nada entra automático.
    """
    subscription_id = getattr(ev, "subscription_id", None)
    email = getattr(ev, "customer_email", None)
    if subscription_id:
        filtro = SubscriptionEvent.subscription_id == subscription_id
    elif email:
        filtro = SubscriptionEvent.customer_email == email
    else:
        return []

    conhecidos = set()
    for order_id, order_ref in (
        db.query(SubscriptionEvent.order_id, SubscriptionEvent.order_ref)
        .filter(filtro)
        .all()
    ):
        if order_id:
            conhecidos.add(str(order_id))
        if order_ref:
            conhecidos.add(str(order_ref))

    desconhecidas = unknown_array_charges([ev], conhecidos)
    for ch in desconhecidas:
        logger.warning(
            "possível webhook perdido: cobrança %s (assinatura %s / %s) aparece no "
            "array charges.completed mas não existe como evento — verificar na Kiwify",
            ch.get("order_id"),
            subscription_id,
            email,
        )
    return [str(ch.get("order_id")) for ch in desconhecidas]


def record_subscription_event(
    db: Session,
    payload: Dict[str, Any],
    event_type: str,
) -> Optional[SubscriptionEvent]:
    """Insere evento. Retorna None se duplicado. Nunca quebra o webhook por dedupe."""
    try:
        fields = extract_event_fields(payload, event_type)
        existing = (
            db.query(SubscriptionEvent)
            .filter(SubscriptionEvent.dedupe_key == fields["dedupe_key"])
            .first()
        )
        if existing:
            logger.info("subscription_event duplicate skipped type=%s", event_type)
            return None

        user_id = None
        if fields.get("customer_email"):
            user = db.query(User).filter(User.email == fields["customer_email"]).first()
            if user:
                user_id = user.id

        par = encontrar_par_de_plan_change(db, fields)
        is_plan_change = par is not None
        if par is not None and not par.is_plan_change:
            par.is_plan_change = True

        row = SubscriptionEvent(
            event_type=fields["event_type"],
            order_id=fields.get("order_id"),
            order_ref=fields.get("order_ref"),
            order_status=fields.get("order_status"),
            subscription_id=fields.get("subscription_id"),
            customer_email=fields.get("customer_email"),
            customer_name=fields.get("customer_name"),
            customer_cpf=fields.get("customer_cpf"),
            customer_phone=fields.get("customer_phone"),
            plan_id=fields.get("plan_id"),
            plan_name=fields.get("plan_name"),
            plan_frequency=fields.get("plan_frequency"),
            amount_gross_cents=fields.get("amount_gross_cents"),
            fee_cents=fields.get("fee_cents"),
            amount_net_cents=fields.get("amount_net_cents"),
            payment_method=fields.get("payment_method"),
            subscription_status=fields.get("subscription_status"),
            has_access=fields.get("has_access"),
            access_until=fields.get("access_until"),
            next_payment=fields.get("next_payment"),
            subscription_start=fields.get("subscription_start"),
            approved_date=fields.get("approved_date"),
            refunded_at=fields.get("refunded_at"),
            funds_status=fields.get("funds_status"),
            deposit_date=fields.get("deposit_date"),
            charges_completed=fields.get("charges_completed"),
            card_rejection_reason=fields.get("card_rejection_reason"),
            user_id=user_id,
            is_plan_change=is_plan_change,
            raw_payload=payload,
            dedupe_key=fields["dedupe_key"],
        )
        db.add(row)
        db.flush()

        # Verificação (não inserção): o array cita cobranças que não conhecemos?
        # Try/except próprio: essa checagem é só um alerta, nunca pode derrubar
        # o registro do evento (que já foi flushado acima). Se falhar aqui, o
        # `except Exception` externo não faz db.rollback() e o webhook ainda
        # commita a linha — mas retornar None faria _link_event_to_user e o
        # backfill de CPF serem pulados, deixando user_id NULL pra sempre.
        try:
            alertar_cobrancas_desconhecidas(db, row)
        except Exception:  # noqa: BLE001
            logger.exception(
                "alertar_cobrancas_desconhecidas falhou (ignorado) type=%s order=%s",
                event_type,
                fields.get("order_id"),
            )

        # Backfill: assim que a Kiwify manda um subscription_id real pra um CPF
        # que já tinha histórico órfão (ex.: importado sem subscription_id, ou
        # de antes desse campo existir), "adota" retroativamente esse histórico
        # pro mesmo grupo — sem isso, a pessoa vira "2 assinantes" a partir da
        # primeira renovação real pós-import (o histórico fica preso na chave
        # por CPF, o evento novo abre uma chave por subscription_id à parte).
        if fields.get("subscription_id") and fields.get("customer_cpf"):
            db.query(SubscriptionEvent).filter(
                SubscriptionEvent.customer_cpf == fields["customer_cpf"],
                SubscriptionEvent.subscription_id.is_(None),
            ).update({"subscription_id": fields["subscription_id"]}, synchronize_session=False)

        logger.info(
            "subscription_event recorded type=%s order=%s",
            fields["event_type"],
            fields.get("order_id"),
        )
        return row
    except IntegrityError:
        logger.info("subscription_event race duplicate type=%s", event_type)
        db.rollback()
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("subscription_event record failed: %s", exc, exc_info=True)
        return None
