from datetime import date
from typing import List, Optional
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import openpyxl
from openpyxl import Workbook

from app.api.v1.dependencies import get_current_user, require_active_subscription
from app.db.session import get_db
from app.models.user import User
from app.repositories.ad_spend_repository import AdSpendRepository
from app.services.ad_spend_service import AdSpendService

router = APIRouter(tags=["ad_spends"])


class AdSpendCreate(BaseModel):
    date: date
    amount: float = Field(..., gt=0)
    sub_id: Optional[str] = None
    clicks: Optional[int] = 0


class AdSpendUpdate(BaseModel):
    date: Optional[date] = None
    amount: Optional[float] = Field(None, gt=0)
    sub_id: Optional[str] = None
    clicks: Optional[int] = None


class AdSpendResponse(BaseModel):
    id: int
    date: date
    amount: float
    sub_id: Optional[str]
    clicks: Optional[int] = 0

    class Config:
        from_attributes = True


class BulkAdSpendPayload(BaseModel):
    items: List[AdSpendCreate]


@router.post("", response_model=AdSpendResponse, status_code=status.HTTP_201_CREATED)
def create_ad_spend(
    payload: AdSpendCreate,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = AdSpendService(AdSpendRepository(db))
    return service.create(current_user.id, payload.date, payload.amount, payload.sub_id, payload.clicks)


@router.post("/bulk", response_model=List[AdSpendResponse], status_code=status.HTTP_201_CREATED)
def bulk_create_ad_spend(
    payload: BulkAdSpendPayload,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = AdSpendService(AdSpendRepository(db))
    return service.bulk_create(current_user.id, payload.items)


@router.get("", response_model=List[AdSpendResponse])
def list_ad_spends(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    limit: int | None = Query(None, ge=1),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = AdSpendService(AdSpendRepository(db))
    return service.list(current_user.id, start_date, end_date, limit, offset)


@router.patch("/{ad_spend_id}", response_model=AdSpendResponse)
def update_ad_spend(
    ad_spend_id: int,
    payload: AdSpendUpdate,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = AdSpendService(AdSpendRepository(db))
    # O service já valida se o ad_spend pertence ao usuário
    return service.update(current_user.id, ad_spend_id, payload)


@router.delete("/all", status_code=status.HTTP_200_OK)
def delete_all_ad_spends(
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Deleta todos os ad_spends do usuário autenticado."""
    service = AdSpendService(AdSpendRepository(db))
    return service.delete_all(current_user.id)


@router.delete("/{ad_spend_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ad_spend(
    ad_spend_id: int,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = AdSpendService(AdSpendRepository(db))
    # O service já valida se o ad_spend pertence ao usuário
    service.delete(current_user.id, ad_spend_id)
    return None


@router.get("/template")
def download_template(
    current_user: User = Depends(require_active_subscription),
):
    """
    Download do template Excel para importação de investimentos em ads.
    Retorna um arquivo .xlsx com as colunas: Data, SubId, ValorGasto
    """
    try:
        # Criar workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Modelo"
        
        # Adicionar cabeçalhos
        ws.append(["Data", "SubId", "ValorGasto", "Cliques"])
        
        # Adicionar dados de exemplo
        today = datetime.now().strftime("%Y-%m-%d")
        ws.append([today, "ASPRADOR02", "120,50", "100"])
        ws.append([today, "", "300,00", "0"])
        
        # Ajustar largura das colunas
        ws.column_dimensions['A'].width = 12  # Data
        ws.column_dimensions['B'].width = 15  # SubId
        ws.column_dimensions['C'].width = 12  # ValorGasto
        ws.column_dimensions['D'].width = 10  # Cliques
        
        # Salvar em buffer
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        
        # Retornar arquivo com Content-Disposition header
        # Usar filename* com encoding UTF-8 para garantir compatibilidade
        filename = "modelo-investimentos.xlsx"
        return Response(
            content=buffer.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}',
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "Access-Control-Expose-Headers": "Content-Disposition, Content-Type",
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao gerar template: {str(e)}"
        )
