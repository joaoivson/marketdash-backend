"""Métricas do painel admin — calculadas na hora a partir de subscription_events."""
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.facebook_integration import FacebookIntegration
from app.models.shopee_integration import ShopeeIntegration
from app.models.subscription_event import SubscriptionEvent
from app.models.user import User
from app.models.user_login import UserLogin
from app.models.ad_spend import AdSpend
from app.models.dataset_row import DatasetRow
from app.services.charges import extract_paid_charges, total_paid_net
from app.core.plans import list_price_cents

PAID_EVENTS = {
    "order_approved",
    "subscription_renewed",
    "compra_aprovada",
}
REFUND_EVENTS = {
    "order_refunded",
    "order_chargedback",
    "chargeback",
    "compra_reembolsada",
}
CANCEL_EVENTS = {"subscription_canceled"}
# Cancelamento feito pelo produtor (ajuste/teste do Luiz no painel Kiwify) não é
# churn real — cliente não saiu, foi um ajuste administrativo. is_plan_change
# não serve pra isso (é semântica de continuação/upgrade); cancel_reason sim.
PRODUTOR_ADJUSTMENT_REASONS = {"cancelado pelo produtor"}
FAILED_PAY_EVENTS = {
    "subscription_late",
    "order_refused",
    "compra_recusada",
}


BRT = ZoneInfo("America/Sao_Paulo")


def _month_bounds(year: int, month: int) -> Tuple[datetime, datetime]:
    """Início/fim do mês em BRT (America/Sao_Paulo), convertidos pra instantes
    UTC — todo dado é gravado em UTC, mas o negócio é brasileiro. Sem isso, uma
    cobrança de véspera de virada de mês perto da meia-noite (ex.: 21h BRT =
    00h UTC do dia seguinte) cai no mês civil errado."""
    start = datetime(year, month, 1, tzinfo=BRT).astimezone(timezone.utc)
    last = monthrange(year, month)[1]
    end = (
        datetime(year, month, last, 23, 59, 59, 999999, tzinfo=BRT)
        .astimezone(timezone.utc)
    )
    return start, end


def _brt_date(dt: datetime) -> date:
    """Data civil (BRT) de um datetime aware — para bucketing por mês."""
    return dt.astimezone(BRT).date()


def _freq_divisor(frequency: Optional[str]) -> int:
    f = (frequency or "").lower()
    if f in ("quarterly", "trimestral", "quarter"):
        return 3
    if f in ("yearly", "annual", "anual", "year"):
        return 12
    return 1


def _add_months(dt: datetime, months: int) -> datetime:
    """Soma meses preservando o dia; encurta pro último dia quando o mês é menor."""
    total = dt.month - 1 + months
    ano = dt.year + total // 12
    mes = total % 12 + 1
    dia = min(dt.day, monthrange(ano, mes)[1])
    return dt.replace(year=ano, month=mes, day=dia)


def _utc(value):
    """Normaliza para datetime aware em UTC."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def build_coverage_periods(events: List["SubscriptionEvent"]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Reconstrói, por assinante, os períodos em que a assinatura esteve paga.

    Base do MRR histórico: sem snapshot mensal, a única fonte de verdade do
    passado são as cobranças. Cada cobrança paga cobre da data dela até
    +1/+3/+12 meses conforme a periodicidade. Reembolso encerra a vigência na
    data do reembolso (nunca retroage pro mês da venda). Quando o último evento
    traz `access_until` explícito — caso do cancelado-com-acesso — ele prevalece
    sobre a conta.

    A conta bate com o `access_until` que a Kiwify manda em todos os assinantes
    de produção, o que serve de conferência da regra.
    """
    por_assinante: Dict[str, List] = defaultdict(list)
    for ev in events:
        por_assinante[_subscriber_key(ev)].append(ev)

    resultado: Dict[str, List[Dict[str, Any]]] = {}
    for chave, evs in por_assinante.items():
        periodos: List[Dict[str, Any]] = []
        for c in extract_paid_charges(evs):
            inicio = _utc(c.get("paid_at"))
            if not inicio:
                continue
            div = _freq_divisor(c.get("frequency"))
            periodos.append({
                "inicio": inicio,
                "fim": _add_months(inicio, div),
                "net_cents": c["net_cents"],
                "gross_cents": c["gross_cents"],
                "divisor": div,
            })
        periodos.sort(key=lambda p: p["inicio"])

        reembolsos = [
            _utc(ev.refunded_at or ev.received_at)
            for ev in evs
            if (ev.event_type or "").lower() in REFUND_EVENTS
        ]
        reembolso = min([r for r in reembolsos if r], default=None)

        if reembolso:
            cortados = []
            for p in periodos:
                if p["inicio"] >= reembolso:
                    continue
                p["fim"] = min(p["fim"], reembolso)
                cortados.append(p)
            periodos = cortados
        elif periodos:
            ultimo_ev = max(evs, key=lambda e: _utc(e.received_at) or datetime.min.replace(tzinfo=timezone.utc))
            acesso_ate = _utc(getattr(ultimo_ev, "access_until", None))
            if acesso_ate:
                periodos[-1]["fim"] = acesso_ate

        resultado[chave] = periodos
    return resultado


def _normalize_plan_label(name: Optional[str], plan_id: Optional[str] = None) -> str:
    blob = f"{name or ''} {plan_id or ''}".lower()
    if "max" in blob:
        return "max"
    if "pro" in blob:
        return "pro"
    if "essencial" in blob or "essential" in blob:
        return "essencial"
    return "essencial"


def _subscriber_key(ev: SubscriptionEvent) -> str:
    if ev.subscription_id:
        return f"sub:{ev.subscription_id}"
    if ev.customer_cpf:
        return f"cpf:{ev.customer_cpf}"
    if ev.customer_email:
        return f"email:{(ev.customer_email or '').lower()}"
    return f"id:{ev.id}"


def _dedupe_by_charge(events: List[SubscriptionEvent]) -> List[SubscriptionEvent]:
    """Um evento por cobrança (usado só por estornos, que não passam por charges.py)."""
    seen: set = set()
    result: List[SubscriptionEvent] = []
    for ev in events:
        if ev.order_id:
            if ev.order_id in seen:
                continue
            seen.add(ev.order_id)
        result.append(ev)
    return result


def total_paid_net_from_charges(events) -> int:
    return total_paid_net(events)


def revenue_from_charges_for_month(events, year: int, month: int) -> dict:
    net = gross = 0
    for c in extract_paid_charges(events):
        dt = c.get("paid_at")
        if not dt:
            continue
        # Mês civil BRT, não UTC — cobrança de fim de mês perto da meia-noite
        # (ex.: 21h BRT = 00h UTC do dia seguinte) senão cai no mês errado.
        d = _brt_date(dt) if isinstance(dt, datetime) else dt
        if d.year == year and d.month == month:
            net += c["net_cents"]
            gross += c["gross_cents"]
    return {"net": net, "gross": gross}


def _paid_total_for_events(events, paid_events=None) -> int:
    """Total pago líquido do assinante — cobranças distintas por order_ref."""
    return total_paid_net(events)


def _fees_from_charges_for_month(events, year: int, month: int) -> int:
    fees = 0
    for c in extract_paid_charges(events):
        dt = c.get("paid_at")
        if not dt:
            continue
        d = _brt_date(dt) if isinstance(dt, datetime) else dt
        if d.year == year and d.month == month:
            fees += c.get("fee_cents") or 0
    return fees


# Pra campos de ESTADO da assinatura (next_payment, access_until, status) — não pra
# valor pago. A Kiwify manda order_approved e subscription_renewed quase juntos pra
# mesma renovação, mas order_approved às vezes carrega o next_payment ANTIGO (de
# antes da renovação processar) enquanto subscription_renewed já traz o novo — e
# pode chegar alguns milissegundos DEPOIS. "Mais recente por received_at" sozinho
# pega o evento errado nesse caso. Eventos que mudam o estado da assinatura
# (renewed/late/canceled/refund/chargeback) ficam no mesmo tier — received_at
# decide; order_approved fica abaixo pra não sobrescrever um renew quase
# simultâneo com next_payment velho.
_SUBSCRIBER_STATE_PRIORITY = {
    "subscription_canceled": 2,
    "subscription_late": 2,
    "subscription_renewed": 2,
    "order_refunded": 2,
    "order_chargedback": 2,
    "chargeback": 2,
    # Snapshot de estado do import histórico Kiwify — precisa do mesmo tier de
    # subscription_canceled: sem isso, alguém com uma assinatura CANCELADA
    # antiga (prioridade 2) seguida de uma ATIVA nova (sem isto, prioridade 0)
    # sob o mesmo CPF tem o evento cancelado antigo vencendo o "latest" mesmo
    # sendo cronologicamente anterior — puxando plano/periodicidade errados
    # pro cálculo de MRR (caso real: Laysla e Cristiana, cada uma com uma
    # assinatura cancelada de periodicidade diferente da atual).
    "import_subscription_active": 2,
    "order_approved": 1,
    "compra_aprovada": 1,
}


def _subscriber_state_sort_key(ev: SubscriptionEvent) -> tuple:
    priority = _SUBSCRIBER_STATE_PRIORITY.get((ev.event_type or "").lower(), 0)
    received = ev.received_at or datetime.min.replace(tzinfo=timezone.utc)
    return (priority, received)


def _latest_by_subscriber(events: List[SubscriptionEvent]) -> Dict[str, SubscriptionEvent]:
    latest: Dict[str, SubscriptionEvent] = {}
    for ev in sorted(events, key=_subscriber_state_sort_key):
        latest[_subscriber_key(ev)] = ev
    return latest


def _is_active_now(ev: SubscriptionEvent, today: date) -> bool:
    if ev.has_access is False:
        return False
    if ev.has_access is True:
        if ev.access_until is None:
            return True
        return ev.access_until.date() >= today
    # fallback: subscription_status
    st = (ev.subscription_status or "").lower()
    if st in ("active", "ativa"):
        if ev.access_until and ev.access_until.date() < today:
            return False
        return True
    return False


def _is_canceled(ev) -> bool:
    """Assinatura cancelada — por tipo de evento ou por status da assinatura."""
    if (getattr(ev, "event_type", None) or "").lower() == "subscription_canceled":
        return True
    return (getattr(ev, "subscription_status", None) or "").lower() in ("canceled", "cancelled")


def cancel_instants(events) -> Dict[str, List[datetime]]:
    """Instantes de cancelamento REAL por assinante.

    Rodada 6, item 2. Base pro MRR histórico: uma assinatura só conta no mês M
    se cobria o fim de M E não estava cancelada até lá. Upgrade/downgrade
    (`is_plan_change`) e ajuste do produtor não são saída de cliente.
    """
    por_assinante: Dict[str, List[datetime]] = defaultdict(list)
    for ev in events:
        if (ev.event_type or "").lower() not in CANCEL_EVENTS:
            continue
        if getattr(ev, "is_plan_change", False):
            continue
        motivo = (getattr(ev, "cancel_reason", None) or "").strip().lower()
        if motivo in PRODUTOR_ADJUSTMENT_REASONS:
            continue
        quando = _utc(getattr(ev, "canceled_at", None) or ev.received_at)
        if quando:
            por_assinante[_subscriber_key(ev)].append(quando)
    return dict(por_assinante)


def _client_display_status(ev: SubscriptionEvent, is_active: bool) -> str:
    """Status de exibição na lista/ficha — late ≠ churn; late com acesso pode ser ativo no count."""
    etype = (ev.event_type or "").lower()
    sub_st = (ev.subscription_status or "").lower()
    is_late = etype == "subscription_late" or sub_st == "waiting_payment"
    is_canceled = etype == "subscription_canceled" or sub_st in ("canceled", "cancelled")

    if is_canceled and is_active:
        return "cancelado_com_acesso"
    if is_canceled:
        return "inativo"
    if is_late:
        return "atrasado"
    if is_active:
        return "ativo"
    return "inativo"


def _status_permitido(status: str, filtro: Optional[str], busca: Optional[str]) -> bool:
    """O status do cliente passa pelo filtro da lista?

    Rodada 6, item 10. `filtro` aceita vários status separados por vírgula — a
    lista abre em "Ativo + Atrasado + Cancelado c/ acesso". E busca vence filtro:
    digitar o nome de uma inativa tem que encontrá-la, senão parece que ela sumiu
    do sistema.
    """
    if busca:
        return True
    if not filtro:
        return True
    permitidos = {s.strip() for s in filtro.split(",") if s.strip()}
    return not permitidos or status in permitidos


def _uso_links_paginas_30d(db: Session, user_id: int) -> Dict[str, int]:
    """Bloco Links/Páginas da ficha individual — mesma regra da aba Uso, 30 dias."""
    from app.services.platform_usage_service import PlatformUsageService

    uso = PlatformUsageService(db).uso_de_links_e_paginas("30d", [user_id])
    return uso.get(user_id, {
        "links_em_uso": 0,
        "links_criados": 0,
        "paginas_em_uso": 0,
        "paginas_criadas": 0,
    })


class AdminMetricsService:
    def __init__(self, db: Session):
        self.db = db

    def _all_events(self) -> List[SubscriptionEvent]:
        # id como desempate: dois eventos com o MESMO received_at (caso real do
        # import histórico — continuação, cancela e reassina no mesmo instante)
        # ficariam em ordem não-determinística só com received_at, e
        # _latest_by_subscriber() decidiria "o estado atual" ao acaso a cada
        # query. id crescente = ordem de inserção, desempate estável.
        return (
            self.db.query(SubscriptionEvent)
            .order_by(SubscriptionEvent.received_at.asc(), SubscriptionEvent.id.asc())
            .all()
        )

    def active_subscribers(self, as_of: Optional[date] = None) -> List[SubscriptionEvent]:
        today = as_of or datetime.now(timezone.utc).date()
        latest = _latest_by_subscriber(self._all_events())
        return [ev for ev in latest.values() if _is_active_now(ev, today)]

    def renewing_subscribers(self, as_of: Optional[date] = None) -> List[SubscriptionEvent]:
        """Quem VAI RENOVAR: acesso vigente e não cancelada.

        Rodada 6, item 2. É a base do MRR e do card "Assinantes ativos". Quem
        cancelou mas ainda tem acesso continua em `active_subscribers()` —
        segue usando o produto e segue no denominador da aba Uso, mas não é
        mais receita recorrente esperada.
        """
        return [ev for ev in self.active_subscribers(as_of) if not _is_canceled(ev)]

    def mrr_cents(self, actives: Optional[List[SubscriptionEvent]] = None) -> Dict[str, int]:
        actives = actives if actives is not None else self.renewing_subscribers()
        # Precisão cheia por assinante, arredonda só no total — dividir em inteiro
        # (`// div`) por assinante perde centavos a cada um antes de somar.
        net_frac = 0.0
        gross_frac = 0.0
        for ev in actives:
            div = _freq_divisor(ev.plan_frequency)
            # última cobrança paga da assinatura
            paid = self._last_paid_for(ev)
            n = (paid.amount_net_cents if paid else ev.amount_net_cents) or 0
            # Bruto (Rodada 7, item 3) = preço de TABELA vigente, não a última
            # cobrança real — evita que desconto/cupom histórico distorça o
            # "faturamento potencial" que o bruto representa. Sem preço
            # cadastrado pro plano/frequência, cai no valor real pago.
            plano = _normalize_plan_label(ev.plan_name, ev.plan_id)
            tabela = list_price_cents(plano, ev.plan_frequency)
            g = tabela if tabela is not None else ((paid.amount_gross_cents if paid else ev.amount_gross_cents) or 0)
            net_frac += n / div
            gross_frac += g / div
        return {"net": int(round(net_frac)), "gross": int(round(gross_frac))}

    def mrr_at(
        self,
        momento: datetime,
        periodos: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        cancelamentos: Optional[Dict[str, List[datetime]]] = None,
    ) -> Dict[str, int]:
        """MRR num instante: vigência paga cobrindo `momento`, descontando cancelamentos.

        Um cancelamento só derruba o período em que ele caiu (ou um anterior) —
        cancelou em maio e reassinou em julho não afeta o MRR de julho.
        """
        if periodos is None:
            periodos = build_coverage_periods(self._all_events())
        if cancelamentos is None:
            cancelamentos = cancel_instants(self._all_events())
        # Mesmo cuidado de mrr_cents: precisão cheia por assinante, arredonda só no total.
        net_frac = gross_frac = 0.0
        for chave, lista in periodos.items():
            # o período mais recente que cobre o instante (renovação sobrepõe a anterior)
            cobrindo = None
            for p in lista:
                if p["inicio"] <= momento < p["fim"]:
                    if cobrindo is None or p["inicio"] > cobrindo["inicio"]:
                        cobrindo = p
            if not cobrindo:
                continue
            cancelada = any(
                cobrindo["inicio"] <= c <= momento for c in cancelamentos.get(chave, [])
            )
            if cancelada:
                continue
            net_frac += cobrindo["net_cents"] / cobrindo["divisor"]
            gross_frac += cobrindo["gross_cents"] / cobrindo["divisor"]
        return {"net": int(round(net_frac)), "gross": int(round(gross_frac))}

    def _last_paid_for(self, ev: SubscriptionEvent) -> Optional[SubscriptionEvent]:
        q = self.db.query(SubscriptionEvent).filter(SubscriptionEvent.event_type.in_(PAID_EVENTS))
        if ev.subscription_id:
            q = q.filter(SubscriptionEvent.subscription_id == ev.subscription_id)
        elif ev.customer_email:
            q = q.filter(SubscriptionEvent.customer_email == ev.customer_email)
        else:
            return None
        return q.order_by(SubscriptionEvent.received_at.desc()).first()

    def revenue_for_month(self, year: int, month: int) -> Dict[str, int]:
        start, end = _month_bounds(year, month)
        all_events = self._all_events()
        # Sem caminho legado: toda cobrança é um evento pago com order_ref.
        charges_rev = revenue_from_charges_for_month(all_events, year, month)
        gross = charges_rev["gross"]
        net = charges_rev["net"]

        refunds = (
            self.db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.event_type.in_(REFUND_EVENTS),
                func.coalesce(SubscriptionEvent.refunded_at, SubscriptionEvent.received_at) >= start,
                func.coalesce(SubscriptionEvent.refunded_at, SubscriptionEvent.received_at) <= end,
            )
            .all()
        )
        refunds = _dedupe_by_charge(refunds)
        refund_gross = sum((e.amount_gross_cents or 0) for e in refunds)
        refund_net = sum((e.amount_net_cents or 0) for e in refunds)
        return {
            "gross": gross - refund_gross,
            "net": net - refund_net,
            "refund_gross": refund_gross,
            "refund_net": refund_net,
        }

    def new_subscriptions(self, year: int, month: int) -> int:
        start, end = _month_bounds(year, month)
        paid = (
            self.db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.event_type.in_(PAID_EVENTS),
                SubscriptionEvent.received_at >= start,
                SubscriptionEvent.received_at <= end,
                SubscriptionEvent.is_plan_change.is_(False),
            )
            .all()
        )
        # primeiro pago da assinatura no período
        first_paid: Dict[str, datetime] = {}
        for ev in self.db.query(SubscriptionEvent).filter(SubscriptionEvent.event_type.in_(PAID_EVENTS)).all():
            key = _subscriber_key(ev)
            ts = ev.received_at or datetime.min.replace(tzinfo=timezone.utc)
            if key not in first_paid or ts < first_paid[key]:
                first_paid[key] = ts
        count = 0
        seen = set()
        for ev in paid:
            key = _subscriber_key(ev)
            if key in seen:
                continue
            fp = first_paid.get(key)
            if fp and start <= fp <= end:
                count += 1
                seen.add(key)
        return count

    def churn_for_month(self, year: int, month: int) -> Dict[str, Any]:
        start, end = _month_bounds(year, month)
        # renovando no início do mês — cancelado-com-acesso segue "ativo" pro
        # produto (aba Uso), mas não é mais receita recorrente esperada, então
        # não conta no denominador do churn.
        start_actives = self.renewing_subscribers(as_of=(start - timedelta(seconds=1)).date())
        start_count = max(len(start_actives), 1)
        cancels = (
            self.db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.event_type.in_(CANCEL_EVENTS),
                SubscriptionEvent.received_at >= start,
                SubscriptionEvent.received_at <= end,
                SubscriptionEvent.is_plan_change.is_(False),
            )
            .all()
        )
        # Ajuste do produtor não é churn real — cliente não saiu.
        cancels = [
            c for c in cancels
            if (c.cancel_reason or "").strip().lower() not in PRODUTOR_ADJUSTMENT_REASONS
        ]
        # unique by subscriber
        keys = {_subscriber_key(c) for c in cancels}
        n = len(keys)
        return {"count": n, "rate": round(n / start_count, 4), "start_actives": len(start_actives)}

    def _agora(self) -> datetime:
        """Ponto de corte do mês corrente — sobrescrito nos testes."""
        return datetime.now(timezone.utc)

    def renewal_rate(self, year: int, month: int) -> Optional[float]:
        """De quem venceu no período: renovou = pagou. Cancelou = não renovou.

        Rodada 6, item 4. O denominador vem do FIM das vigências pagas
        (build_coverage_periods), não de `next_payment`: o import histórico
        carimbou `next_payment = access_until` em todos os eventos do CPF, então
        quem cancelou ficava com vencimento no futuro e sumia do denominador —
        era por isso que o painel marcava 100%.
        """
        start, end = _month_bounds(year, month)
        # Vencimento que ainda não chegou não é renovação falha: no mês corrente
        # o denominador vai só até agora.
        agora = self._agora()
        if end > agora:
            end = agora
        if end < start:
            return None  # mês futuro

        eventos = self._all_events()
        periodos = build_coverage_periods(eventos)
        cancelamentos = cancel_instants(eventos)

        cobrancas_por_assinante: Dict[str, List[datetime]] = defaultdict(list)
        por_assinante: Dict[str, List] = defaultdict(list)
        for ev in eventos:
            por_assinante[_subscriber_key(ev)].append(ev)
        for chave, evs in por_assinante.items():
            for c in extract_paid_charges(evs):
                quando = _utc(c.get("paid_at"))
                if quando:
                    cobrancas_por_assinante[chave].append(quando)

        # Upgrade/downgrade (Rodada 6, item 3) atribui um subscription_id NOVO —
        # a cliente vira duas subscriber_keys pro mesmo CPF: a antiga (superada,
        # cancelada com is_plan_change=True) e a nova (o pagamento do upgrade).
        # Quando o vencimento da chave antiga cai dentro do mês medido, o
        # pagamento que a "renova" está sob a chave NOVA, não a dela mesma —
        # sem isso, a cobrança some do `pagou` e ela conta como renovação
        # falha quando é uma cliente contínua e pagante (Finding, revisão
        # final). `cancel_instants()` já exclui esse cancelamento de
        # `cancelamentos`, então só falta ensinar `pagou` a olhar pro CPF.
        chaves_superadas_por_troca: set = set()
        chaves_lado_troca_por_cpf: Dict[str, List[str]] = defaultdict(list)
        cpf_por_chave: Dict[str, str] = {}
        for chave, evs in por_assinante.items():
            cpf = next(
                (getattr(e, "customer_cpf", None) for e in evs if getattr(e, "customer_cpf", None)),
                None,
            )
            if cpf:
                cpf_por_chave[chave] = cpf
                if any(getattr(e, "is_plan_change", False) for e in evs):
                    chaves_lado_troca_por_cpf[cpf].append(chave)
            if any(
                (getattr(e, "event_type", None) or "").lower() == "subscription_canceled"
                and getattr(e, "is_plan_change", False)
                for e in evs
            ):
                chaves_superadas_por_troca.add(chave)

        # Tolerância: a cobrança de renovação cai em cima do vencimento, mas a
        # Kiwify pode processar com algumas horas de diferença (e o fim da
        # vigência é calculado por soma de meses, não pelo relógio deles).
        tolerancia = timedelta(days=3)

        denominador = 0
        renovaram = 0
        for chave, lista in periodos.items():
            vencimentos = [p["fim"] for p in lista if start <= p["fim"] <= end]
            if not vencimentos:
                continue
            venceu_em = max(vencimentos)
            denominador += 1
            # Chaves ligadas ao upgrade/downgrade dessa cliente (mesma lógica
            # do broadening de `pagou`, Rodada 6/revisão final): quando a
            # chave devida é o lado superado de uma troca de plano, a chave
            # NOVA do mesmo CPF também conta — tanto pro pagamento quanto,
            # simetricamente, pro cancelamento real (Finding, revisão final
            # rodada 6): sem isso, um cancelamento real que caiu na chave nova
            # ficava invisível pro check e a cliente contava como renovada
            # mesmo tendo saído de verdade logo depois do upgrade.
            chaves_ligadas = [chave]
            if chave in chaves_superadas_por_troca:
                cpf = cpf_por_chave.get(chave)
                for outra in chaves_lado_troca_por_cpf.get(cpf, []) if cpf else []:
                    if outra == chave:
                        continue
                    chaves_ligadas.append(outra)
            candidatos_pagamento = [
                quando for c in chaves_ligadas for quando in cobrancas_por_assinante.get(c, [])
            ]
            pagou = any(
                (venceu_em - tolerancia) <= quando <= end
                for quando in candidatos_pagamento
            )
            # Uma cobrança perto do vencimento que é desfeita por um
            # cancelamento REAL (cancel_instants já exclui troca de plano e
            # ajuste do produtor) no mesmo ciclo não é renovação de verdade —
            # a assinante não está continuando. Janela do próprio ciclo, não do
            # mês: um cancelamento semanas depois (ainda dentro do mês) não
            # pode retroagir e desfazer uma renovação genuína — ela já conta
            # como churn no mês em que realmente cancelou (churn_for_month).
            fim_ciclo = min(end, venceu_em + tolerancia)
            candidatos_cancelamento = [
                quando for c in chaves_ligadas for quando in cancelamentos.get(c, [])
            ]
            cancelou_no_ciclo = any(
                (venceu_em - tolerancia) <= quando <= fim_ciclo
                for quando in candidatos_cancelamento
            )
            if pagou and not cancelou_no_ciclo:
                renovaram += 1

        if denominador == 0:
            return None
        return round(renovaram / denominador, 4)

    def ltv_estimate_cents(self, mrr_net: int, actives_count: int) -> Optional[int]:
        if actives_count <= 0 or mrr_net <= 0:
            return None
        # média móvel 3 meses de churn
        today = datetime.now(timezone.utc).date()
        rates = []
        for i in range(1, 4):
            y = today.year
            m = today.month - i
            while m <= 0:
                m += 12
                y -= 1
            ch = self.churn_for_month(y, m)
            # só conta mês se houver eventos
            if self.db.query(SubscriptionEvent).filter(
                SubscriptionEvent.received_at >= _month_bounds(y, m)[0],
                SubscriptionEvent.received_at <= _month_bounds(y, m)[1],
            ).first():
                rates.append(ch["rate"])
        if len(rates) < 3:
            return None
        avg_churn = sum(rates) / len(rates)
        if avg_churn <= 0:
            return None
        arpu = mrr_net / actives_count
        return int(round(arpu / avg_churn))

    def plan_breakdown(self, actives: List[SubscriptionEvent]) -> Dict[str, int]:
        out = {"essencial": 0, "pro": 0, "max": 0}
        for ev in actives:
            label = _normalize_plan_label(ev.plan_name, ev.plan_id)
            out[label] = out.get(label, 0) + 1
        return out

    def alerts(self) -> Dict[str, int]:
        today = datetime.now(timezone.utc).date()
        actives = self.active_subscribers()
        soon = 0
        failed = 0
        never = 0
        no_login = 0
        for ev in actives:
            if ev.next_payment:
                d = ev.next_payment.date()
                if today <= d <= today + timedelta(days=7):
                    soon += 1
            # failed pay: latest event failed but still active
            latest_fail = None
            if ev.subscription_id or ev.customer_email:
                q = self.db.query(SubscriptionEvent).filter(SubscriptionEvent.event_type.in_(FAILED_PAY_EVENTS))
                if ev.subscription_id:
                    q = q.filter(SubscriptionEvent.subscription_id == ev.subscription_id)
                else:
                    q = q.filter(SubscriptionEvent.customer_email == ev.customer_email)
                latest_fail = q.order_by(SubscriptionEvent.received_at.desc()).first()
            if latest_fail and latest_fail.received_at and (datetime.now(timezone.utc) - latest_fail.received_at).days <= 14:
                failed += 1

            uid = ev.user_id
            if not uid and ev.customer_email:
                u = self.db.query(User).filter(User.email == ev.customer_email).first()
                uid = u.id if u else None
            if uid:
                has_shopee = (
                    self.db.query(ShopeeIntegration)
                    .filter(ShopeeIntegration.user_id == uid, ShopeeIntegration.is_active.is_(True))
                    .first()
                )
                has_fb = (
                    self.db.query(FacebookIntegration)
                    .filter(FacebookIntegration.user_id == uid, FacebookIntegration.is_active.is_(True))
                    .first()
                )
                if not has_shopee and not has_fb:
                    never += 1
                last_login = (
                    self.db.query(UserLogin)
                    .filter(UserLogin.user_id == uid)
                    .order_by(UserLogin.logged_at.desc())
                    .first()
                )
                if not last_login or (today - last_login.logged_at.date()).days > 10:
                    no_login += 1
            else:
                never += 1
                no_login += 1
        return {
            "expiring_7d": soon,
            "payment_failed": failed,
            "never_connected": never,
            "no_login_10d": no_login,
        }

    def series_12m(self) -> Dict[str, List[Dict[str, Any]]]:
        today = datetime.now(timezone.utc).date()
        first = self.db.query(func.min(SubscriptionEvent.received_at)).scalar()
        if not first:
            return {"mrr": [], "revenue": [], "new_vs_canceled": []}

        mrr_y, mrr_m = first.year, first.month
        rev_y, rev_m = first.year, first.month

        all_events = self.db.query(SubscriptionEvent).all()
        for c in extract_paid_charges(all_events):
            dt = c.get("paid_at")
            if not dt:
                continue
            d = _brt_date(dt) if isinstance(dt, datetime) else dt
            if (d.year, d.month) < (rev_y, rev_m):
                rev_y, rev_m = d.year, d.month

        # MRR histórico é RECONSTRUÍDO das cobranças, não é o retrato de hoje
        # repetido pra trás. Se um webhook antigo entrar depois, o passado fica
        # mais correto — recalcular é o comportamento desejado aqui.
        periodos = build_coverage_periods(all_events)
        cancelamentos = cancel_instants(all_events)
        agora = datetime.now(timezone.utc)
        primeira_cobranca = min(
            (p["inicio"] for lista in periodos.values() for p in lista), default=None
        )
        if primeira_cobranca:
            mrr_y, mrr_m = primeira_cobranca.year, primeira_cobranca.month

        mrr_series: List[Dict[str, Any]] = []
        y, m = mrr_y, mrr_m
        while (y, m) <= (today.year, today.month):
            # mês fechado: último instante do mês. mês corrente: retrato de agora.
            fim_do_mes = datetime(
                y, m, monthrange(y, m)[1], 23, 59, 59, tzinfo=timezone.utc
            )
            momento = agora if (y, m) == (today.year, today.month) else fim_do_mes
            mrr = self.mrr_at(momento, periodos, cancelamentos)
            mrr_series.append({
                "month": f"{y:04d}-{m:02d}",
                "net": mrr["net"],
                "gross": mrr["gross"],
            })
            m += 1
            if m > 12:
                m, y = 1, y + 1

        rev_series: List[Dict[str, Any]] = []
        y, m = rev_y, rev_m
        while (y, m) <= (today.year, today.month):
            rev = self.revenue_for_month(y, m)
            rev_series.append({
                "month": f"{y:04d}-{m:02d}",
                "net": rev["net"],
                "gross": rev["gross"],
            })
            m += 1
            if m > 12:
                m, y = 1, y + 1

        return {
            "mrr": mrr_series,
            "revenue": rev_series,
            "new_vs_canceled": self.new_vs_canceled_series(),
        }

    def new_vs_canceled_series(self) -> List[Dict[str, Any]]:
        """Novas × canceladas nos últimos 12 meses — saldo líquido que move o MRR.

        Rodada 6, item 11. Reaproveita new_subscriptions/churn_for_month, então
        herda as mesmas regras: upgrade não conta dos dois lados (is_plan_change)
        e "Cancelado pelo produtor" não conta churn.
        """
        hoje = self._agora().date()
        serie: List[Dict[str, Any]] = []
        y, m = hoje.year, hoje.month - 11
        while m <= 0:
            m += 12
            y -= 1
        while (y, m) <= (hoje.year, hoje.month):
            serie.append({
                "month": f"{y:04d}-{m:02d}",
                "novas": self.new_subscriptions(y, m),
                "canceladas": self.churn_for_month(y, m)["count"],
            })
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return serie

    def plan_frequency_distribution(self) -> List[Dict[str, Any]]:
        actives = self.renewing_subscribers()
        buckets: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: {"count": 0, "revenue_net": 0})
        for ev in actives:
            plan = _normalize_plan_label(ev.plan_name, ev.plan_id)
            freq = (ev.plan_frequency or "monthly").lower()
            if freq in ("quarterly", "trimestral"):
                freq_label = "trimestral"
            elif freq in ("yearly", "annual", "anual"):
                freq_label = "anual"
            else:
                freq_label = "mensal"
            paid = self._last_paid_for(ev)
            net = (paid.amount_net_cents if paid else ev.amount_net_cents) or 0
            buckets[(plan, freq_label)]["count"] += 1
            buckets[(plan, freq_label)]["revenue_net"] += net
        total_rev = sum(v["revenue_net"] for v in buckets.values()) or 1
        out = []
        for (plan, freq), v in sorted(buckets.items()):
            out.append({
                "plan": plan,
                "frequency": freq,
                "count": v["count"],
                "revenue_net_cents": v["revenue_net"],
                "revenue_share": round(v["revenue_net"] / total_rev, 4),
            })
        return out

    def dashboard(self, year: int, month: int) -> Dict[str, Any]:
        actives = self.renewing_subscribers()
        mrr = self.mrr_cents(actives)
        rev = self.revenue_for_month(year, month)
        churn = self.churn_for_month(year, month)
        arpu = int(round(mrr["net"] / len(actives))) if actives else 0
        return {
            "year": year,
            "month": month,
            "mrr_net_cents": mrr["net"],
            "mrr_gross_cents": mrr["gross"],
            "revenue_net_cents": rev["net"],
            "revenue_gross_cents": rev["gross"],
            "refund_net_cents": rev["refund_net"],
            "active_count": len(actives),
            "active_by_plan": self.plan_breakdown(actives),
            "new_subscriptions": self.new_subscriptions(year, month),
            "churn_count": churn["count"],
            "churn_rate": churn["rate"],
            "renewal_rate": self.renewal_rate(year, month),
            "arpu_cents": arpu,
            "ltv_cents": self.ltv_estimate_cents(mrr["net"], len(actives)),
            "alerts": self.alerts(),
            "series": self.series_12m(),
            "plan_frequency": self.plan_frequency_distribution(),
        }

    def _semaphore(self, user_id: Optional[int]) -> str:
        if not user_id:
            return "red"
        today = datetime.now(timezone.utc)
        signals = 0
        login = (
            self.db.query(UserLogin)
            .filter(UserLogin.user_id == user_id, UserLogin.logged_at >= today - timedelta(days=7))
            .first()
        )
        if login:
            signals += 1
        shopee = self.db.query(ShopeeIntegration).filter(
            ShopeeIntegration.user_id == user_id, ShopeeIntegration.is_active.is_(True)
        ).first()
        fb = self.db.query(FacebookIntegration).filter(
            FacebookIntegration.user_id == user_id, FacebookIntegration.is_active.is_(True)
        ).first()
        if shopee or fb:
            signals += 1
        recent_data = (
            self.db.query(DatasetRow)
            .filter(DatasetRow.user_id == user_id, DatasetRow.date >= (today - timedelta(days=7)).date())
            .first()
        )
        if not recent_data:
            recent_data = (
                self.db.query(AdSpend)
                .filter(AdSpend.user_id == user_id, AdSpend.date >= (today - timedelta(days=7)).date())
                .first()
            )
        if recent_data:
            signals += 1
        has_integration = bool(shopee or fb)
        if signals >= 3:
            return "green"
        if signals == 2:
            return "yellow"
        # 0-1 sinal: integração conectada nunca é vermelho sozinha — histórico de login
        # começou do zero no deploy, "sem login" hoje é normal e não pode derrubar quem
        # já deu o passo de conectar Shopee/Meta.
        if has_integration:
            return "yellow"
        return "red"

    def list_clients(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        actives_map = {_subscriber_key(e): e for e in self.active_subscribers()}
        # also include inactive with latest event
        eventos = self._all_events()
        all_latest = _latest_by_subscriber(eventos)
        # Sinal DURÁVEL de "esta chave já foi lado de uma troca de plano
        # alguma vez" — usado pela candidatura a sobrevivente do merge logo
        # abaixo. `all_latest[key].is_plan_change` sozinho é EFÊMERO: logo
        # após um upgrade o evento mais recente da sobrevivente é o próprio
        # pagamento do upgrade (is_plan_change=True), mas assim que ela tem
        # uma renovação normal (is_plan_change=False, não é ela própria lado
        # de troca) essa renovação vira o novo "latest" da chave e a
        # sobrevivente deixava de qualificar como candidata — o grupo antigo
        # apagado ficava sem absorvedor e sumia do painel (Finding, task 3b,
        # reproduzido pelo revisor).
        keys_com_plan_change = {
            _subscriber_key(e) for e in eventos if getattr(e, "is_plan_change", False)
        }

        # Kiwify atribui um subscription_id NOVO quando o cliente faz upgrade/
        # downgrade de plano (não reaproveita o antigo) — então a mesma pessoa
        # gera duas subscriber_keys: uma pro plano antigo (cancelado, superado
        # pela troca) e outra pro plano novo (atual). Sem isso ela aparece
        # duas vezes na lista. Só remove quando o evento mais recente do grupo
        # é EXATAMENTE cancelamento + is_plan_change — um cancelamento
        # genuíno (não ligado a troca de plano) continua com sua própria
        # linha, e o lado novo (pago, is_plan_change também True mas não é
        # cancelamento) nunca é removido por essa regra.
        by_cpf: Dict[str, List[str]] = defaultdict(list)
        for key, ev in all_latest.items():
            cpf = getattr(ev, "customer_cpf", None)
            if cpf:
                by_cpf[cpf].append(key)
        # Chave sobrevivente -> subscription_id dos grupos apagados que foram
        # fundidos NELA especificamente. Só sabe "qual CPF teve merge" não
        # bastava (Finding, task 3b, reproduzido pelo revisor): um CPF pode ter
        # 3+ grupos, com só um par sendo upgrade e outro sendo uma assinatura
        # antiga genuinamente independente — filtrar o total pago por
        # customer_cpf inteiro contaminava a linha independente com o dinheiro
        # do upgrade alheio. O total pago (mais abaixo) soma só o(s)
        # subscription_id listados aqui pra cada sobrevivente, nunca o CPF
        # inteiro.
        merged_into: Dict[str, List[str]] = defaultdict(list)
        for cpf, keys in by_cpf.items():
            if len(keys) <= 1:
                continue
            matched = [
                key
                for key in keys
                if (all_latest[key].event_type or "").lower() == "subscription_canceled"
                and getattr(all_latest[key], "is_plan_change", False)
            ]
            if not matched:
                continue
            if len(matched) == len(keys):
                # Todos os grupos do CPF bateram a condição de remoção — ex.:
                # upgrade Essencial->Pro (Essencial cancelada por troca) seguido
                # de um churn GENUÍNO da Pro que encontrar_par_de_plan_change
                # (subscription_event_recorder.py) pareou por engano contra o
                # pagamento Essencial original, também marcando is_plan_change.
                # Sem essa guarda, apagar os dois grupos some com a cliente
                # inteira (Finding 1, task 3b, reproduzido pelo revisor).
                # Mantém só o grupo mais recente por received_at.
                mais_recente = max(
                    matched,
                    key=lambda k: all_latest[k].received_at
                    or datetime.min.replace(tzinfo=timezone.utc),
                )
                matched = [k for k in matched if k != mais_recente]
            # Candidatos a absorver o dinheiro dos grupos apagados: só
            # sobreviventes que TÊM (em algum momento do histórico, sinal
            # durável — keys_com_plan_change) is_plan_change=True — sinal de
            # que ELES TAMBÉM são lado de uma troca de plano (o lado pago do
            # upgrade), não uma assinatura independente que só coincide de
            # compartilhar o CPF (ex.: sub-antiga, is_plan_change=False).
            candidatos = [
                key
                for key in keys
                if key not in matched and key in keys_com_plan_change
            ]
            for key in matched:
                ev_removido = all_latest[key]
                if candidatos:
                    # Mais de um candidato só acontece com múltiplos pares de
                    # upgrade sob o mesmo CPF — pareia pelo mais próximo no
                    # tempo do grupo apagado, mesma lógica de proximidade do
                    # pareador em subscription_event_recorder.py.
                    quando_removido = ev_removido.received_at or datetime.min.replace(
                        tzinfo=timezone.utc
                    )
                    alvo = min(
                        candidatos,
                        key=lambda k: abs(
                            (
                                (
                                    all_latest[k].received_at
                                    or datetime.min.replace(tzinfo=timezone.utc)
                                )
                                - quando_removido
                            ).total_seconds()
                        ),
                    )
                    if ev_removido.subscription_id:
                        merged_into[alvo].append(ev_removido.subscription_id)
                del all_latest[key]

        rows = []
        q = (filters.get("q") or "").strip().lower()
        for key, ev in all_latest.items():
            uid = ev.user_id
            user = self.db.query(User).filter(User.id == uid).first() if uid else None
            if not user and ev.customer_email:
                user = self.db.query(User).filter(User.email == ev.customer_email).first()
                uid = user.id if user else None

            is_active = key in actives_map
            status = _client_display_status(ev, is_active)

            last_login = None
            if uid:
                ll = (
                    self.db.query(UserLogin)
                    .filter(UserLogin.user_id == uid)
                    .order_by(UserLogin.logged_at.desc())
                    .first()
                )
                last_login = ll.logged_at.isoformat() if ll else None

            has_shopee = bool(
                uid
                and self.db.query(ShopeeIntegration)
                .filter(ShopeeIntegration.user_id == uid, ShopeeIntegration.is_active.is_(True))
                .first()
            )
            has_fb = bool(
                uid
                and self.db.query(FacebookIntegration)
                .filter(FacebookIntegration.user_id == uid, FacebookIntegration.is_active.is_(True))
                .first()
            )

            # Mesma prioridade de _subscriber_key() (subscription_id → CPF → e-mail) —
            # sem isso, 2 assinaturas de uma mesma pessoa com CPFs diferentes mas o
            # mesmo e-mail (ex.: reassinatura com CPF trocado) caem no filtro por
            # e-mail e mostram o total pago das DUAS combinado em cada linha.
            if key in merged_into and ev.subscription_id:
                # Este sobrevivente absorveu grupo(s) apagados pelo de-dup de
                # upgrade (Finding 2, task 3b) — soma as cobranças da(s)
                # subscription_id fundida(s) NELE especificamente, nunca do
                # CPF inteiro (isso contaminaria outra assinatura independente
                # do mesmo CPF — Finding, task 3b, reproduzido pelo revisor).
                sub_filter = SubscriptionEvent.subscription_id.in_(
                    [ev.subscription_id, *merged_into[key]]
                )
            elif ev.subscription_id:
                sub_filter = SubscriptionEvent.subscription_id == ev.subscription_id
            elif ev.customer_cpf:
                sub_filter = SubscriptionEvent.customer_cpf == ev.customer_cpf
            else:
                sub_filter = SubscriptionEvent.customer_email == ev.customer_email
            sub_events = self.db.query(SubscriptionEvent).filter(sub_filter).all()
            # Total pago líquido = soma das cobranças distintas por order_ref
            # (charges.py) — sem caminho legado, `_paid_total_for_events` é só
            # um passthrough pra `total_paid_net`.
            paid_total_net = _paid_total_for_events(sub_events)

            name = ev.customer_name or (user.name if user else None) or ""
            email = ev.customer_email or (user.email if user else "") or ""
            cpf = ev.customer_cpf or ""

            if q and q not in name.lower() and q not in email.lower() and q not in cpf.lower():
                continue

            item = {
                "user_id": uid,
                "name": name,
                "email": email,
                "cpf": cpf,
                "phone": ev.customer_phone,
                "plan": _normalize_plan_label(ev.plan_name, ev.plan_id),
                "frequency": ev.plan_frequency,
                "status": status,
                "started_at": (ev.subscription_start or ev.received_at).isoformat() if (ev.subscription_start or ev.received_at) else None,
                "next_payment": ev.next_payment.isoformat() if ev.next_payment else None,
                "access_until": ev.access_until.isoformat() if ev.access_until else None,
                "total_paid_net_cents": int(paid_total_net or 0),
                "card_rejection_reason": getattr(ev, "card_rejection_reason", None),
                "last_login_at": last_login,
                "integrations": {"shopee": has_shopee, "facebook": has_fb},
                "semaphore": self._semaphore(uid),
                "subscription_id": ev.subscription_id,
            }

            # filters
            if not _status_permitido(status, filters.get("status"), q):
                continue
            if filters.get("plan") and filters["plan"] != item["plan"]:
                continue
            if filters.get("expiring_7d"):
                if not ev.next_payment:
                    continue
                d = ev.next_payment.date()
                today = datetime.now(timezone.utc).date()
                if not (today <= d <= today + timedelta(days=7)):
                    continue
            if filters.get("never_connected") and (has_shopee or has_fb):
                continue
            if filters.get("payment_failed"):
                qf = self.db.query(SubscriptionEvent).filter(
                    SubscriptionEvent.event_type.in_(FAILED_PAY_EVENTS)
                )
                if ev.subscription_id:
                    qf = qf.filter(SubscriptionEvent.subscription_id == ev.subscription_id)
                else:
                    qf = qf.filter(SubscriptionEvent.customer_email == ev.customer_email)
                lf = qf.order_by(SubscriptionEvent.received_at.desc()).first()
                if not lf or not lf.received_at:
                    continue
                if (datetime.now(timezone.utc) - lf.received_at).days > 14:
                    continue
            if filters.get("no_login_10d"):
                if ev.user_id is None:
                    # Checa o campo BRUTO do evento, não `uid` (que pode
                    # ter sido resolvido por fallback de e-mail acima) —
                    # precisa ser exatamente o mesmo critério de
                    # _base_ativa() em platform_usage_service.py
                    # (`if ev.user_id`), senão um assinante sem user_id no
                    # evento mas com e-mail batendo numa conta real volta
                    # a aparecer na lista sem aparecer no card.
                    continue
                today = datetime.now(timezone.utc).date()
                if last_login:
                    try:
                        ld = datetime.fromisoformat(last_login.replace("Z", "+00:00")).date()
                        if (today - ld).days <= 10:
                            continue
                    except ValueError:
                        continue
                # sem login → inclui

            rows.append(item)
        return rows

    def client_detail(self, user_id: int) -> Optional[Dict[str, Any]]:
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return None
        events = (
            self.db.query(SubscriptionEvent)
            .filter(
                (SubscriptionEvent.user_id == user_id)
                | (SubscriptionEvent.customer_email == user.email)
            )
            .order_by(SubscriptionEvent.received_at.desc())
            .all()
        )
        latest = events[0] if events else None
        clients = self.list_clients({"q": user.email})
        # Pessoa com mais de uma assinatura histórica (CPFs diferentes, ex.: caso
        # Alexandre) aparece como mais de uma linha em list_clients — pega a mais
        # recente (started_at), senão a ficha mostraria o status/plano de uma
        # assinatura antiga em vez do estado atual.
        base = (
            max(clients, key=lambda c: c.get("started_at") or "")
            if clients
            else {"user_id": user_id, "name": user.name, "email": user.email}
        )
        logins = (
            self.db.query(UserLogin)
            .filter(UserLogin.user_id == user_id, UserLogin.logged_at >= datetime.now(timezone.utc) - timedelta(days=30))
            .order_by(UserLogin.logged_at.asc())
            .all()
        )
        shopee = self.db.query(ShopeeIntegration).filter(ShopeeIntegration.user_id == user_id).first()
        fb = self.db.query(FacebookIntegration).filter(FacebookIntegration.user_id == user_id).first()
        camps = self.db.query(func.count(Campaign.id)).filter(Campaign.user_id == user_id).scalar() or 0
        from datetime import date as date_cls
        start_30 = date_cls.today() - timedelta(days=30)
        commission = (
            self.db.query(func.coalesce(func.sum(DatasetRow.commission), 0))
            .filter(DatasetRow.user_id == user_id, DatasetRow.date >= start_30)
            .scalar()
        )
        spend = (
            self.db.query(func.coalesce(func.sum(AdSpend.amount), 0))
            .filter(AdSpend.user_id == user_id, AdSpend.date >= start_30)
            .scalar()
        )
        return {
            **base,
            "timeline": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "received_at": e.received_at.isoformat() if e.received_at else None,
                    "amount_net_cents": e.amount_net_cents,
                    "amount_gross_cents": e.amount_gross_cents,
                    "plan_name": e.plan_name,
                    "is_plan_change": e.is_plan_change,
                    "card_rejection_reason": e.card_rejection_reason,
                }
                for e in events
            ],
            "subscription_block": {
                "plan": _normalize_plan_label(latest.plan_name if latest else None, latest.plan_id if latest else None),
                "frequency": latest.plan_frequency if latest else None,
                "status": latest.subscription_status if latest else None,
                "access_until": latest.access_until.isoformat() if latest and latest.access_until else None,
                "next_payment": latest.next_payment.isoformat() if latest and latest.next_payment else None,
                "payment_method": latest.payment_method if latest else None,
                "has_access": latest.has_access if latest else None,
                "card_rejection_reason": latest.card_rejection_reason if latest else None,
            },
            "usage": {
                "logins_30d": [
                    {"at": l.logged_at.isoformat(), "ip": l.ip} for l in logins
                ],
                "shopee_last_sync": shopee.last_sync_at.isoformat() if shopee and shopee.last_sync_at else None,
                "facebook_last_sync": fb.last_sync_at.isoformat() if fb and getattr(fb, "last_sync_at", None) else None,
                "campaigns_count": int(camps),
                "commission_30d": float(commission or 0),
                "spend_30d": float(spend or 0),
                **_uso_links_paginas_30d(self.db, user_id),
            },
            "contact": {
                "email": user.email,
                "phone": latest.customer_phone if latest else None,
                "cpf": latest.customer_cpf if latest else user.cpf_cnpj,
            },
        }
