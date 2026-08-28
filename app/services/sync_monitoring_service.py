"""Observabilidade de execuções de sync (Shopee/Facebook) para o painel admin.

Separado de admin_metrics_service.py (que é billing/assinatura) — sync_runs é uma
preocupação operacional diferente.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

# Painel admin é BR — dias do gráfico devem bater com o que o usuário vê em pt-BR.
_APP_TZ = ZoneInfo("America/Sao_Paulo")

from app.models.sync_run import SyncRun
from app.repositories.facebook_integration_repository import FacebookIntegrationRepository
from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository
from app.repositories.sync_run_repository import STALE_RUNNING_SECONDS, SyncRunRepository

# STALE_RUNNING_SECONDS mora no repository: é o MESMO limiar que o
# `fechar_orfaos()` usa para encerrar esses runs. Duas cópias sairiam de sincronia
# e o painel passaria a marcar "(travada?)" numa faixa diferente da que fecha.

# Código da API Shopee → rótulo. Sem isso "108 erros" não diz se a ação é nossa,
# da aluna ou de ninguém (instabilidade do fornecedor).
MOTIVOS_SHOPEE = {
    "10000": "Instabilidade Shopee",
    "10010": "Erro de query",
    "10020": "Autenticação/credencial",
    "10030": "Rate limit",
    "11000": "Negócio/parâmetros",
    "11001": "Negócio/parâmetros",
    "11002": "Negócio/parâmetros",
}
MOTIVO_INTERNO = "Erro interno (nosso código)"
MOTIVO_OUTROS = "Outros"

_RE_CODIGO_SHOPEE = re.compile(r"error\s*\[(\d{4,5})\]", re.IGNORECASE)


def classificar_erro(mensagem: Optional[str]) -> Dict[str, Optional[str]]:
    """
    Extrai o código estruturado da mensagem e devolve o motivo.

    A Shopee responde `error [10000]: System Error` — parsear isso é o que separa
    "instabilidade do fornecedor, ignora" de "credencial da aluna expirou, aja".
    """
    if not mensagem:
        return {"codigo": None, "motivo": MOTIVO_OUTROS}
    achado = _RE_CODIGO_SHOPEE.search(mensagem)
    if achado:
        codigo = achado.group(1)
        return {"codigo": codigo, "motivo": MOTIVOS_SHOPEE.get(codigo, MOTIVO_OUTROS)}
    # credencial inválida também chega já traduzida pelo nosso código
    if "credencial" in mensagem.lower() and "10020" in mensagem:
        return {"codigo": "10020", "motivo": MOTIVOS_SHOPEE["10020"]}
    if "shopee" not in mensagem.lower():
        return {"codigo": None, "motivo": MOTIVO_INTERNO}
    return {"codigo": None, "motivo": MOTIVO_OUTROS}


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _serialize_run(run: SyncRun) -> Dict[str, Any]:
    started = _as_utc(run.started_at)
    finished = _as_utc(run.finished_at)
    duration_seconds = (finished - started).total_seconds() if started and finished else None
    is_stale_running = (
        run.status == "running"
        and started is not None
        and (datetime.now(timezone.utc) - started).total_seconds() > STALE_RUNNING_SECONDS
    )
    return {
        "id": run.id,
        "source": run.source,
        "trigger": run.trigger,
        "user_id": run.user_id,
        "days_back": run.days_back,
        "empty_attempt": run.empty_attempt,
        "status": run.status,
        "started_at": started.isoformat() if started else None,
        "finished_at": finished.isoformat() if finished else None,
        "duration_seconds": duration_seconds,
        "records_fetched": run.records_fetched,
        "records_upserted": run.records_upserted,
        "is_suspected_partial": run.is_suspected_partial,
        "is_stale_running": is_stale_running,
        "error_message": run.error_message,
        "details": run.details,
        **(
            {"erro_codigo": c["codigo"], "erro_motivo": c["motivo"]}
            if (c := classificar_erro(run.error_message)) and run.error_message
            else {"erro_codigo": None, "erro_motivo": None}
        ),
    }


def _mapa_usuarios(db: Session, user_ids) -> Dict[int, Dict[str, Any]]:
    """id → nome/e-mail. 'User: 20' é id interno e não diz nada pra quem lê."""
    ids = {uid for uid in user_ids if uid}
    if not ids:
        return {}
    from app.models.user import User
    from app.services.platform_usage_service import capitalizar_nome

    return {
        uid: {
            "user_id": uid,
            "nome": capitalizar_nome(nome) or (email or f"#{uid}"),
            "email": email,
        }
        for uid, nome, email in db.query(User.id, User.name, User.email).filter(User.id.in_(ids))
    }


class SyncMonitoringService:
    def __init__(self, db: Session):
        self.db = db
        self.run_repo = SyncRunRepository(db)

    def list_runs(
        self,
        source: Optional[str] = None,
        trigger: Optional[str] = None,
        status: Optional[str] = None,
        user_id: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        runs = self.run_repo.list_by_filters(
            source=source, trigger=trigger, status=status, user_id=user_id, limit=limit,
        )
        usuarios = _mapa_usuarios(self.db, [r.user_id for r in runs])
        saida = []
        for r in runs:
            item = _serialize_run(r)
            item["usuario"] = usuarios.get(r.user_id)
            saida.append(item)
        return saida

    def erros_por_motivo(self, horas: int = 24, source: Optional[str] = None) -> List[Dict[str, Any]]:
        """Agrupa as falhas recentes por motivo — dá direção pro número bruto."""
        desde = datetime.now(timezone.utc) - timedelta(hours=horas)
        q = self.db.query(SyncRun).filter(
            SyncRun.status == "failed", SyncRun.started_at >= desde
        )
        if source:
            q = q.filter(SyncRun.source == source)

        contagem: Dict[str, Dict[str, Any]] = {}
        for run in q.all():
            c = classificar_erro(run.error_message)
            chave = c["motivo"] or MOTIVO_OUTROS
            bucket = contagem.setdefault(
                chave, {"motivo": chave, "codigo": c["codigo"], "total": 0, "usuarios": set()}
            )
            bucket["total"] += 1
            if run.user_id:
                bucket["usuarios"].add(run.user_id)

        total = sum(b["total"] for b in contagem.values())
        return [
            {
                "motivo": b["motivo"],
                "codigo": b["codigo"],
                "total": b["total"],
                "usuarios": len(b["usuarios"]),
                "proporcao": round(b["total"] / total, 4) if total else 0,
            }
            for b in sorted(contagem.values(), key=lambda x: x["total"], reverse=True)
        ]

    def full_sync_health(
        self,
        source: str = "shopee",
        trigger: str = "cron_full",
        since: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Cruza integrações ativas contra sync_runs pra responder "a madrugada completou
        pra todo mundo?" — hoje só dá pra inferir usuário por usuário via last_sync_at.
        """
        since = since or (datetime.now(timezone.utc) - timedelta(hours=24))

        if source == "shopee":
            active_user_ids = {i.user_id for i in ShopeeIntegrationRepository(self.db).get_all_active()}
        elif source == "facebook":
            active_user_ids = {i.user_id for i in FacebookIntegrationRepository(self.db).get_all_active()}
        else:
            active_user_ids = set()

        runs = self.run_repo.list_by_filters(source=source, trigger=trigger, since=since, limit=None)

        success_user_ids = {r.user_id for r in runs if r.status == "success" and r.user_id in active_user_ids}
        failed_user_ids = {
            r.user_id for r in runs if r.status == "failed" and r.user_id in active_user_ids
        } - success_user_ids
        missing_user_ids = active_user_ids - success_user_ids - failed_user_ids

        return {
            "source": source,
            "trigger": trigger,
            "since": since.isoformat(),
            "total_ativos": len(active_user_ids),
            "sucesso": len(success_user_ids),
            "falha": len(failed_user_ids),
            "sem_execucao": sorted(missing_user_ids),
        }

    def source_summary(self, source: str) -> Dict[str, Any]:
        """Card resumo por fonte pra tela Uso: última sync + status, chamadas/erros 24h."""
        since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        runs_24h = self.run_repo.list_by_filters(source=source, since=since_24h, limit=None)
        latest = self.run_repo.list_by_filters(source=source, limit=1)
        last_run = latest[0] if latest else None
        return {
            "source": source,
            "last_sync_at": last_run.started_at.isoformat() if last_run and last_run.started_at else None,
            "last_status": last_run.status if last_run else None,
            "calls_24h": len(runs_24h),
            "errors_24h": sum(1 for r in runs_24h if r.status == "failed"),
        }

    def daily_call_counts(self, source: str, days: int = 30) -> List[Dict[str, Any]]:
        """Chamadas (execuções) e erros por dia civil em America/Sao_Paulo, últimos N dias."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        runs = self.run_repo.list_by_filters(source=source, since=since, limit=None)
        buckets: Dict[str, Dict[str, int]] = {}
        for r in runs:
            started = _as_utc(r.started_at)
            if not started:
                continue
            # Antes: .date() em UTC — sync às 22h BRT caía no "dia seguinte" no gráfico.
            day = started.astimezone(_APP_TZ).date().isoformat()
            b = buckets.setdefault(day, {"calls": 0, "errors": 0})
            b["calls"] += 1
            if r.status == "failed":
                b["errors"] += 1
        return [{"date": d, **v} for d, v in sorted(buckets.items())]

    def latest_successful(self, source: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        if user_id is not None:
            run = self.run_repo.latest_for_user(user_id, source)
            return _serialize_run(run) if run and run.status == "success" else None
        runs = self.run_repo.list_by_filters(source=source, status="success", limit=1)
        return _serialize_run(runs[0]) if runs else None
