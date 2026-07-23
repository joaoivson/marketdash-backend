"""Rotas do painel administrativo interno."""
from __future__ import annotations

import csv
import io
from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import cast, Date, func
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_admin
from app.db.session import get_db
from app.models.admin_client_note import AdminClientNote
from app.models.expense import Expense
from app.models.page_view import PageView
from app.models.sync_error_log import SyncErrorLog
from app.models.user import User
from app.models.user_login import UserLogin
from app.services.admin_dre_service import AdminDreService
from app.services.admin_metrics_service import AdminMetricsService

router = APIRouter(prefix="/admin", tags=["admin-panel"])

EXPENSE_CATEGORIES = {"Infra", "Ferramentas", "Taxas", "Marketing", "Outros"}


class ExpenseIn(BaseModel):
    date: date
    category: str
    supplier: Optional[str] = None
    description: Optional[str] = None
    amount_cents: int = Field(ge=0)
    recurring: bool = False
    notes: Optional[str] = None


class NoteIn(BaseModel):
    body: str = Field(min_length=1)


class PageViewIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)


def _ym(year: Optional[int], month: Optional[int]) -> tuple[int, int]:
    today = datetime.now(timezone.utc).date()
    return year or today.year, month or today.month


@router.get("/dashboard")
def admin_dashboard(
    year: Optional[int] = None,
    month: Optional[int] = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    y, m = _ym(year, month)
    return AdminMetricsService(db).dashboard(y, m)


@router.get("/alerts")
def admin_alerts(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return AdminMetricsService(db).alerts()


@router.get("/clients")
def admin_clients(
    q: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    plan: Optional[str] = None,
    expiring_7d: bool = False,
    payment_failed: bool = False,
    never_connected: bool = False,
    no_login_10d: bool = False,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return AdminMetricsService(db).list_clients({
        "q": q,
        "status": status_filter,
        "plan": plan,
        "expiring_7d": expiring_7d,
        "payment_failed": payment_failed,
        "never_connected": never_connected,
        "no_login_10d": no_login_10d,
    })


@router.get("/clients/export.csv")
def export_clients_csv(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = AdminMetricsService(db).list_clients({})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "email", "plan", "frequency", "status", "next_payment", "total_paid_net_cents", "semaphore"])
    for r in rows:
        w.writerow([
            r.get("name"), r.get("email"), r.get("plan"), r.get("frequency"),
            r.get("status"), r.get("next_payment"), r.get("total_paid_net_cents"), r.get("semaphore"),
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=admin_clients.csv"},
    )


@router.get("/clients/{user_id}")
def admin_client_detail(
    user_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    detail = AdminMetricsService(db).client_detail(user_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    notes = (
        db.query(AdminClientNote)
        .filter(AdminClientNote.user_id == user_id)
        .order_by(AdminClientNote.created_at.desc())
        .all()
    )
    detail["notes"] = [
        {"id": n.id, "body": n.body, "created_at": n.created_at.isoformat() if n.created_at else None}
        for n in notes
    ]
    return detail


@router.post("/clients/{user_id}/notes")
def add_client_note(
    user_id: int,
    payload: NoteIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not db.query(User).filter(User.id == user_id).first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    note = AdminClientNote(user_id=user_id, author_user_id=admin.id, body=payload.body.strip())
    db.add(note)
    db.commit()
    db.refresh(note)
    return {"id": note.id, "body": note.body, "created_at": note.created_at.isoformat()}


@router.get("/expenses")
def list_expenses(
    year: Optional[int] = None,
    month: Optional[int] = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    y, m = _ym(year, month)
    start = date(y, m, 1)
    if m == 12:
        end = date(y + 1, 1, 1)
    else:
        end = date(y, m + 1, 1)
    rows = (
        db.query(Expense)
        .filter(Expense.date >= start, Expense.date < end)
        .order_by(Expense.date.desc())
        .all()
    )
    by_cat: Dict[str, int] = {}
    for e in rows:
        by_cat[e.category] = by_cat.get(e.category, 0) + e.amount_cents
    return {
        "items": [
            {
                "id": e.id,
                "date": e.date.isoformat(),
                "category": e.category,
                "supplier": e.supplier,
                "description": e.description,
                "amount_cents": e.amount_cents,
                "recurring": e.recurring,
                "notes": e.notes,
            }
            for e in rows
        ],
        "total_cents": sum(e.amount_cents for e in rows),
        "by_category": [{"category": k, "amount_cents": v} for k, v in sorted(by_cat.items())],
    }


@router.post("/expenses", status_code=status.HTTP_201_CREATED)
def create_expense(
    payload: ExpenseIn,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.category not in EXPENSE_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category must be one of {sorted(EXPENSE_CATEGORIES)}")
    e = Expense(
        date=payload.date,
        category=payload.category,
        supplier=payload.supplier,
        description=payload.description,
        amount_cents=payload.amount_cents,
        recurring=payload.recurring,
        notes=payload.notes,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return {"id": e.id}


@router.put("/expenses/{expense_id}")
def update_expense(
    expense_id: int,
    payload: ExpenseIn,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    e = db.query(Expense).filter(Expense.id == expense_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    if payload.category not in EXPENSE_CATEGORIES:
        raise HTTPException(status_code=400, detail="invalid category")
    e.date = payload.date
    e.category = payload.category
    e.supplier = payload.supplier
    e.description = payload.description
    e.amount_cents = payload.amount_cents
    e.recurring = payload.recurring
    e.notes = payload.notes
    db.commit()
    return {"id": e.id}


@router.delete("/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(
    expense_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    e = db.query(Expense).filter(Expense.id == expense_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(e)
    db.commit()
    return None


@router.post("/expenses/repeat-previous-month")
def repeat_previous_month(
    year: Optional[int] = None,
    month: Optional[int] = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    y, m = _ym(year, month)
    # previous month
    if m == 1:
        py, pm = y - 1, 12
    else:
        py, pm = y, m - 1
    start = date(py, pm, 1)
    end = date(y, m, 1)
    prev = (
        db.query(Expense)
        .filter(Expense.date >= start, Expense.date < end, Expense.recurring.is_(True))
        .all()
    )
    created = 0
    for e in prev:
        # shift date to current month (same day or last day)
        from calendar import monthrange
        day = min(e.date.day, monthrange(y, m)[1])
        neo = Expense(
            date=date(y, m, day),
            category=e.category,
            supplier=e.supplier,
            description=e.description,
            amount_cents=e.amount_cents,
            recurring=True,
            notes=e.notes,
        )
        db.add(neo)
        created += 1
    db.commit()
    return {"created": created}


@router.get("/dre")
def admin_dre(
    year: Optional[int] = None,
    month: Optional[int] = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    y, m = _ym(year, month)
    return AdminDreService(db).full(y, m)


@router.get("/dre/export.csv")
def export_dre_csv(
    year: Optional[int] = None,
    month: Optional[int] = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    y, m = _ym(year, month)
    dre = AdminDreService(db).month_statement(y, m)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["line", "amount_cents"])
    w.writerow(["gross", dre["gross_cents"]])
    w.writerow(["refund_gross", dre["refund_gross_cents"]])
    w.writerow(["gross_after_refund", dre["gross_after_refund_cents"]])
    w.writerow(["fees", dre["fees_cents"]])
    w.writerow(["revenue_net", dre["revenue_net_cents"]])
    for c in dre["expenses_by_category"]:
        w.writerow([f"expense:{c['category']}", c["amount_cents"]])
    w.writerow(["expenses_total", dre["expenses_total_cents"]])
    w.writerow(["result", dre["result_cents"]])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=dre_{y}_{m:02d}.csv"},
    )


@router.get("/usage")
def admin_usage(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    from datetime import timedelta

    since = datetime.now(timezone.utc) - timedelta(days=30)
    logins = (
        db.query(cast(UserLogin.logged_at, Date).label("d"), func.count())
        .filter(UserLogin.logged_at >= since)
        .group_by("d")
        .order_by("d")
        .all()
    )
    errors = (
        db.query(SyncErrorLog)
        .filter(SyncErrorLog.created_at >= since)
        .order_by(SyncErrorLog.created_at.desc())
        .limit(100)
        .all()
    )
    pages = (
        db.query(PageView.path, func.count().label("c"))
        .filter(PageView.viewed_at >= since)
        .group_by(PageView.path)
        .order_by(func.count().desc())
        .limit(20)
        .all()
    )
    syncs_by_source = (
        db.query(SyncErrorLog.source, cast(SyncErrorLog.created_at, Date).label("d"), func.count())
        .filter(SyncErrorLog.created_at >= since)
        .group_by(SyncErrorLog.source, "d")
        .all()
    )
    return {
        "logins_per_day": [{"date": str(d), "count": c} for d, c in logins],
        "sync_errors": [
            {
                "id": e.id,
                "user_id": e.user_id,
                "source": e.source,
                "error_message": e.error_message[:500],
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in errors
        ],
        "top_pages": [{"path": p, "count": c} for p, c in pages],
        "sync_activity_proxy": [
            {"source": s, "date": str(d), "count": c} for s, d, c in syncs_by_source
        ],
        "note": "Chamadas API/dia: proxy por erros/atividade de sync (não é APM completo).",
    }


@router.post("/page-views", status_code=status.HTTP_204_NO_CONTENT)
def record_page_view(
    payload: PageViewIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Beacon de page view — qualquer usuário autenticado (ranking de telas)."""
    db.add(PageView(user_id=user.id, path=payload.path[:500]))
    db.commit()
    return None
