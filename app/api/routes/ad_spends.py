from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date
from pydantic import BaseModel, Field

from app.db.session import get_db
from app.models.ad_spend import AdSpend
from app.models.user import User

router = APIRouter(prefix="/ad_spends", tags=["ad_spends"])

# Schemas
class AdSpendCreate(BaseModel):
    date: date
    amount: float = Field(..., gt=0)
    sub_id: Optional[str] = None

class AdSpendUpdate(BaseModel):
    date: Optional[date] = None
    amount: Optional[float] = Field(None, gt=0)
    sub_id: Optional[str] = None

class AdSpendResponse(BaseModel):
    id: int
    date: date
    amount: float
    sub_id: Optional[str]

    class Config:
        from_attributes = True

class BulkAdSpendPayload(BaseModel):
    items: List[AdSpendCreate]

# Helper
def get_user(db: Session, user_id: int | None = None) -> User:
    query = db.query(User)
    if user_id is not None:
        query = query.filter(User.id == user_id)
    user = query.first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return user

@router.post("", response_model=AdSpendResponse, status_code=status.HTTP_201_CREATED)
def create_ad_spend(
    payload: AdSpendCreate,
    user_id: int | None = Query(None),
    db: Session = Depends(get_db)
):
    """Cria um registro de gasto de anúncio."""
    user = get_user(db, user_id)
    
    # Normaliza sub_id
    sub_id_val = payload.sub_id
    if sub_id_val == "__all__" or sub_id_val == "":
        sub_id_val = None

    # Opcional: Verificar se já existe um registro para essa data/sub_id e atualizar/somar
    # Por enquanto, vamos apenas criar um novo registro. O dashboard soma tudo.
    
    ad_spend = AdSpend(
        user_id=user.id,
        date=payload.date,
        sub_id=sub_id_val,
        amount=payload.amount
    )
    db.add(ad_spend)
    db.commit()
    db.refresh(ad_spend)
    return ad_spend

@router.post("/bulk", response_model=List[AdSpendResponse], status_code=status.HTTP_201_CREATED)
def bulk_create_ad_spend(
    payload: BulkAdSpendPayload,
    user_id: int | None = Query(None),
    db: Session = Depends(get_db)
):
    """Cria vários registros de gasto de anúncio em uma única chamada."""
    user = get_user(db, user_id)
    if not payload.items:
        return []

    created_items: List[AdSpend] = []
    for item in payload.items:
        sub_id_val = item.sub_id
        if sub_id_val == "__all__" or sub_id_val == "":
            sub_id_val = None
        
        ad_spend = AdSpend(
            user_id=user.id,
            date=item.date,
            sub_id=sub_id_val,
            amount=item.amount
        )
        db.add(ad_spend)
        db.flush()  # Para obter o ID sem fazer commit
        created_items.append(ad_spend)
    
    db.commit()
    
    # Refresh todos os itens para garantir dados atualizados
    for item in created_items:
        db.refresh(item)
    
    return created_items

@router.get("", response_model=List[AdSpendResponse])
def list_ad_spends(
    user_id: int | None = Query(None),
    start_date: date | None = Query(None, description="Data inicial (opcional)"),
    end_date: date | None = Query(None, description="Data final (opcional)"),
    db: Session = Depends(get_db)
):
    """Lista gastos de anúncios. Se datas forem informadas, filtra pelo intervalo; caso contrário, retorna todos do usuário."""
    user = get_user(db, user_id)
    
    query = db.query(AdSpend).filter(AdSpend.user_id == user.id)

    if start_date:
        query = query.filter(AdSpend.date >= start_date)
    if end_date:
        query = query.filter(AdSpend.date <= end_date)
    
    return query.order_by(AdSpend.date.desc(), AdSpend.id.desc()).all()


@router.patch("/{ad_spend_id}", response_model=AdSpendResponse)
def update_ad_spend(
    ad_spend_id: int,
    payload: AdSpendUpdate,
    user_id: int | None = Query(None),
    db: Session = Depends(get_db)
):
    """Atualiza um gasto de anúncio do usuário."""
    user = get_user(db, user_id)
    ad_spend = db.query(AdSpend).filter(AdSpend.id == ad_spend_id, AdSpend.user_id == user.id).first()
    if not ad_spend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registro não encontrado")

    if payload.date is not None:
        ad_spend.date = payload.date
    if payload.amount is not None:
        ad_spend.amount = payload.amount
    if payload.sub_id is not None:
        ad_spend.sub_id = None if payload.sub_id in ["", "__all__"] else payload.sub_id

    db.commit()
    db.refresh(ad_spend)
    return ad_spend


@router.delete("/{ad_spend_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ad_spend(
    ad_spend_id: int,
    user_id: int | None = Query(None),
    db: Session = Depends(get_db)
):
    """Remove um gasto de anúncio do usuário."""
    user = get_user(db, user_id)
    ad_spend = db.query(AdSpend).filter(AdSpend.id == ad_spend_id, AdSpend.user_id == user.id).first()
    if not ad_spend:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registro não encontrado")

    db.delete(ad_spend)
    db.commit()
    return {"detail": "Deleted"}
