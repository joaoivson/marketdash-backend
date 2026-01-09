from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from sqlalchemy.orm import Session
from typing import List
from datetime import date, timedelta

from app.db.session import get_db
from app.models.user import User
from app.models.dataset import Dataset
from app.models.dataset_row import DatasetRow
from app.schemas.dataset import DatasetResponse, DatasetRowResponse
from app.services.csv_service import CSVService
import pandas as pd
import datetime
import math
from decimal import Decimal
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm.attributes import flag_modified

router = APIRouter(prefix="/datasets", tags=["datasets"])


def get_any_user(db: Session, user_id: int | None = None) -> User:
    query = db.query(User)
    if user_id is not None:
        query = query.filter(User.id == user_id)
    user = query.first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nenhum usuário encontrado para associar ao dataset")
    return user


def serialize_value(value):
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
    except Exception:
        pass
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _clean_number(value):
    """
    Converte strings monetárias/numéricas com vírgula/ponto/R$/% para float.
    Respeita ponto como separador decimal quando não há vírgula (não zera decimais).
    """
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)) and not (isinstance(value, float) and math.isnan(value)):
        return float(value)
    if isinstance(value, str):
        cleaned = (
            value.replace("R$", "")
            .replace("%", "")
            .replace(" ", "")
            .replace("\u00a0", "")  # nbsp
        )
        has_comma = "," in cleaned
        has_dot = "." in cleaned

        if has_comma and has_dot:
            # assume dot = thousand, comma = decimal
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif has_comma:
            # assume comma decimal, strip thousand dots if any
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # only dot or digits -> keep dot as decimal
            cleaned = cleaned

        try:
            num = float(cleaned)
            if math.isnan(num):
                return None
            return num
        except Exception:
            return None
    try:
        num = float(value)
        if math.isnan(num):
            return None
        return num
    except Exception:
        return None


def normalize_raw_data(raw: dict) -> dict:
    """
    Garante que campos que começam com 'valor' ou 'comiss' sejam numéricos.
    """
    if not isinstance(raw, dict):
        return raw
    normalized = {}
    for k, v in raw.items():
        key_lower = k.lower()
        if key_lower.startswith("valor") or key_lower.startswith("comiss"):
            parsed = _clean_number(v)
            normalized[k] = parsed if parsed is not None else serialize_value(v)
        else:
            normalized[k] = serialize_value(v)
    return normalized


def serialize_row(row: DatasetRow) -> dict:
    return {
        "id": row.id,
        "dataset_id": row.dataset_id,
        "user_id": row.user_id,
        "date": serialize_value(row.date),
        "transaction_date": serialize_value(row.transaction_date),
        "time": serialize_value(row.time),
        "product": row.product,
        "product_name": row.product_name,
        "platform": row.platform,
        "revenue": serialize_value(row.revenue),
        "cost": serialize_value(row.cost),
        "commission": serialize_value(row.commission),
        "profit": serialize_value(row.profit),
        "gross_value": serialize_value(row.gross_value),
        "commission_value": serialize_value(row.commission_value),
        "net_value": serialize_value(row.net_value),
        "quantity": serialize_value(row.quantity),
        "status": row.status,
        "category": row.category,
        "sub_id1": row.sub_id1,
        "mes_ano": row.mes_ano,
        "raw_data": row.raw_data,
    }


@router.post("/upload", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
async def upload_csv(
    file: UploadFile = File(...),
    user_id: int | None = Query(None),
    db: Session = Depends(get_db)
):
    """Upload e processar arquivo CSV."""
    # Validate file type
    if not file.filename.endswith('.csv'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Apenas arquivos CSV são permitidos"
        )
    
    # Read file content
    file_content = await file.read()
    
    # Validate and process CSV
    df, errors = CSVService.validate_csv(file_content, file.filename)
    
    if df is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erro ao processar CSV: {'; '.join(errors)}"
        )
    
    # Create dataset record
    user = get_any_user(db, user_id)
    dataset = Dataset(
        user_id=user.id,
        filename=file.filename
    )
    db.add(dataset)
    db.flush()  # Get dataset.id
    
    # Convert DataFrame to list of dicts
    rows_data = CSVService.dataframe_to_dict_list(df)

    def _sanitize(value):
        if value is None:
            return None
        try:
            if isinstance(value, float) and math.isnan(value):
                return None
        except Exception:
            pass
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime().isoformat()
        if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if hasattr(value, "item"):
            try:
                return value.item()
            except Exception:
                return value
        return value
    
    # Create dataset rows
    dataset_rows = []
    for row_data in rows_data:
        raw_data = row_data.get("raw_data") if isinstance(row_data, dict) else None
        raw_data_json = normalize_raw_data(raw_data) if raw_data is not None else raw_data
        dataset_row = DatasetRow(
            dataset_id=dataset.id,
            user_id=user.id,
            date=row_data['date'],
            time=row_data.get('time'),
            product=row_data['product'],
            revenue=row_data['revenue'],
            cost=row_data['cost'],
            commission=row_data['commission'],
            profit=row_data['profit'],
            status=row_data.get('status'),
            category=row_data.get('category'),
            sub_id1=row_data.get('sub_id1'),
            mes_ano=row_data.get('mes_ano'),
            raw_data=raw_data_json,
        )
        dataset_rows.append(dataset_row)
    
    db.add_all(dataset_rows)
    db.commit()
    db.refresh(dataset)
    
    # Return response with warnings if any
    if errors:
        # Note: In production, you might want to return warnings differently
        pass
    
    return dataset


class AdSpendPayload(BaseModel):
    amount: float = Field(..., gt=0, description="Valor investido em anúncios")
    sub_id1: str | None = Field(None, description="Sub_id1 opcional para associar o gasto")


@router.post("/latest/ad_spend")
def set_ad_spend(
    payload: AdSpendPayload,
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Define/atualiza a coluna de valor gasto em anúncios no dataset mais recente.
    Se sub_id1 for informado, aplica somente às linhas com esse sub_id1; caso contrário, aplica a todas.
    """
    query = db.query(Dataset)
    if user_id is not None:
        query = query.filter(Dataset.user_id == user_id)
    latest = query.order_by(Dataset.uploaded_at.desc()).first()
    if not latest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhum dataset encontrado para o usuário",
        )

    rows_query = db.query(DatasetRow).filter(DatasetRow.dataset_id == latest.id)
    if payload.sub_id1:
        rows_query = rows_query.filter(DatasetRow.sub_id1 == payload.sub_id1)

    # Ordenar por ID para paginação consistente
    rows_query = rows_query.order_by(DatasetRow.id)
    
    total_rows = rows_query.count()
    if total_rows == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma linha encontrada para aplicar o valor de anúncio",
        )

    # Lógica de Rateio: Divide o valor total pelo número de linhas afetadas
    amount_per_row = payload.amount / total_rows

    # Processar em lotes para evitar timeout/memory overflow
    BATCH_SIZE = 1000  # Aumentei o batch pois bulk_update é mais eficiente
    processed = 0
    updated = 0

    while processed < total_rows:
        batch = rows_query.limit(BATCH_SIZE).offset(processed).all()
        if not batch:
            break
            
        mappings = []
        for row in batch:
            raw = dict(row.raw_data) if row.raw_data else {}
            raw["Valor gasto anuncios"] = amount_per_row
            mappings.append({"id": row.id, "raw_data": raw})
            updated += 1
        
        # bulk_update_mappings é muito mais rápido que iterar e salvar individualmente
        db.bulk_update_mappings(DatasetRow, mappings)
        db.commit() # Salva o lote atual
        
        processed += len(batch)

    return {
        "updated": updated,
        "dataset_id": latest.id,
        "sub_id1": payload.sub_id1,
        "amount": payload.amount,
    }


@router.get("/latest/rows", response_model=List[DatasetRowResponse])
def list_latest_rows(
    user_id: int | None = Query(None),
    start_date: date = Query(..., description="Data inicial (obrigatória)"),
    end_date: date = Query(..., description="Data final (obrigatória)"),
    db: Session = Depends(get_db),
):
    """
    Listar linhas do dataset mais recente do usuário (ou primeiro usuário).
    Intervalo máximo permitido: 90 dias.
    """
    query = db.query(Dataset)
    if user_id is not None:
        query = query.filter(Dataset.user_id == user_id)
    latest = query.order_by(Dataset.uploaded_at.desc()).first()
    if not latest:
        return []
    resolved_end = end_date
    resolved_start = start_date

    if resolved_start > resolved_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Data inicial não pode ser maior que a data final.",
        )

    if (resolved_end - resolved_start).days > 90:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O intervalo máximo permitido é de 90 dias.",
        )

    rows = (
        db.query(DatasetRow)
        .filter(DatasetRow.dataset_id == latest.id)
        .filter(DatasetRow.date >= resolved_start)
        .filter(DatasetRow.date <= resolved_end)
        .order_by(DatasetRow.date.desc())
        .all()
    )
    return JSONResponse(content=[serialize_row(r) for r in rows])


@router.get("/all/rows", response_model=List[DatasetRowResponse])
def list_all_rows(
    user_id: int | None = Query(None),
    start_date: date = Query(..., description="Data inicial (obrigatória)"),
    end_date: date = Query(..., description="Data final (obrigatória)"),
    db: Session = Depends(get_db),
):
    """
    Lista linhas de todos os datasets do usuário (ou primeiro usuário), dentro do intervalo informado.
    Mantém a regra de intervalo máximo de 90 dias.
    """
    dataset_query = db.query(Dataset)
    if user_id is not None:
        dataset_query = dataset_query.filter(Dataset.user_id == user_id)
    dataset_sample = dataset_query.first()
    if not dataset_sample:
        return []
    resolved_user_id = user_id if user_id is not None else dataset_sample.user_id

    resolved_end = end_date
    resolved_start = start_date

    if resolved_start > resolved_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Data inicial não pode ser maior que a data final.",
        )

    if (resolved_end - resolved_start).days > 90:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O intervalo máximo permitido é de 90 dias.",
        )

    rows = (
        db.query(DatasetRow)
        .join(Dataset, DatasetRow.dataset_id == Dataset.id)
        .filter(Dataset.user_id == resolved_user_id)
        .filter(DatasetRow.date >= resolved_start)
        .filter(DatasetRow.date <= resolved_end)
        .order_by(DatasetRow.date.desc())
        .all()
    )
    # Se nada for encontrado no range solicitado, retorna todas as linhas do usuário
    if not rows:
        rows = (
            db.query(DatasetRow)
            .join(Dataset, DatasetRow.dataset_id == Dataset.id)
            .filter(Dataset.user_id == resolved_user_id)
            .order_by(DatasetRow.date.desc())
            .all()
        )
    return JSONResponse(content=[serialize_row(r) for r in rows])


@router.get("", response_model=List[DatasetResponse])
def list_datasets(
    user_id: int | None = Query(None),
    db: Session = Depends(get_db)
):
    """Listar todos os datasets do usuário."""
    query = db.query(Dataset)
    if user_id is not None:
        query = query.filter(Dataset.user_id == user_id)
    datasets = query.order_by(Dataset.uploaded_at.desc()).all()
    
    return datasets


@router.get("/{dataset_id}/rows", response_model=List[DatasetRowResponse])
def list_dataset_rows(
    dataset_id: int,
    start_date: date = Query(..., description="Data inicial (obrigatória)"),
    end_date: date = Query(..., description="Data final (obrigatória)"),
    db: Session = Depends(get_db),
):
    """Listar linhas de um dataset específico (período obrigatório, máx 90 dias)."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset não encontrado"
        )

    resolved_end = end_date
    resolved_start = start_date

    if resolved_start > resolved_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Data inicial não pode ser maior que a data final.",
        )

    if (resolved_end - resolved_start).days > 90:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O intervalo máximo permitido é de 90 dias.",
        )

    rows = (
        db.query(DatasetRow)
        .filter(DatasetRow.dataset_id == dataset_id)
        .filter(DatasetRow.date >= resolved_start)
        .filter(DatasetRow.date <= resolved_end)
        .order_by(DatasetRow.date.desc())
        .all()
    )
    return JSONResponse(content=[serialize_row(r) for r in rows])


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(
    dataset_id: int,
    db: Session = Depends(get_db)
):
    """Obter detalhes de um dataset específico."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset não encontrado"
        )
    
    return dataset


@router.post("/{dataset_id}/refresh", response_model=DatasetResponse)
async def refresh_dataset(
    dataset_id: int,
    db: Session = Depends(get_db)
):
    """
    Reprocessar/atualizar um dataset.
    
    Nota: Este endpoint está preparado para integração futura com API externa.
    Por enquanto, apenas retorna o dataset existente.
    """
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    
    if not dataset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset não encontrado"
        )
    
    # TODO: Implementar lógica de atualização via API externa quando necessário
    # Por enquanto, apenas retorna o dataset existente
    
    return dataset

