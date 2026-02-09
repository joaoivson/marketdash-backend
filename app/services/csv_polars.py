"""
Chunk processing with Polars (first option) and pandas fallback.
Same semantics as DatasetService.process_commission_csv and ClickService.process_click_csv:
groupby, row_hash, bulk_create. Used by process_chunk Celery task.
"""
import hashlib
import logging
from datetime import date, datetime
from io import BytesIO

import pandas as pd
from sqlalchemy.orm import Session

from app.models.dataset_row import DatasetRow
from app.models.click_row import ClickRow
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.repositories.click_row_repository import ClickRowRepository
from app.services.csv_service import ALIASES, normalize_name, find_column

logger = logging.getLogger(__name__)

BATCH_SIZE = 5000

# Formatos comuns de data/datetime em CSV (evita erro "could not find an appropriate format to parse dates")
_DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d-%m-%Y",
    "%d-%m-%y",
    "%d.%m.%Y",
    "%Y/%m/%d",
]


def _parse_date_flexible(value) -> date | None:
    """Tenta interpretar value como data (str, date ou datetime). Retorna date ou None."""
    if value is None:
        return None
    if isinstance(value, date):
        return value if not isinstance(value, datetime) else value.date()
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        pass
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(s, fmt)
            return parsed.date()
        except (ValueError, TypeError):
            continue
    return None


def _generate_row_hash(row_data: dict, user_id: int) -> str:
    """Same as DatasetService._generate_row_hash."""
    components = [
        str(user_id),
        str(row_data.get("order_id") or "nan").strip().lower(),
        str(row_data.get("product_id") or "nan").strip().lower(),
    ]
    return hashlib.md5("|".join(components).encode()).hexdigest()


def _generate_click_hash(row_data: dict, user_id: int) -> str:
    """Same as ClickService._generate_click_hash."""
    date_val = row_data.get("date")
    date_str = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)
    components = [
        str(user_id),
        date_str,
        str(row_data.get("channel") or "Desconhecido").strip().lower(),
        str(row_data.get("sub_id") or "nan").strip().lower(),
    ]
    return hashlib.md5("|".join(components).encode()).hexdigest()


def process_transaction_chunk(
    db: Session,
    dataset_id: int,
    user_id: int,
    chunk_content: bytes,
) -> int:
    """
    Validate chunk CSV (Polars or pandas), groupby, row_hash, bulk_create DatasetRows.
    Returns number of rows processed (inserted/updated).
    """
    try:
        import polars as pl
    except ImportError:
        return _process_transaction_chunk_pandas(db, dataset_id, user_id, chunk_content)

    try:
        df = pl.read_csv(BytesIO(chunk_content), encoding="utf-8-lossy", ignore_errors=True)
    except Exception as e:
        logger.warning(f"Polars read failed, falling back to pandas: {e}")
        return _process_transaction_chunk_pandas(db, dataset_id, user_id, chunk_content)

    if df.height == 0:
        return 0

    # Map columns using same ALIASES as CSVService
    original_cols = df.columns
    col_map = {}
    for target, alias_set in ALIASES.items():
        found = find_column(original_cols, alias_set)
        if found:
            col_map[target] = found

    # Build normalized frame for groupby (minimal set)
    group_cols = ["date", "platform", "category", "product", "status", "sub_id1", "order_id", "product_id"]
    metrics = ["revenue", "commission", "cost", "quantity"]
    exprs = []

    if "date" in col_map:
        exprs.append(pl.col(col_map["date"]).cast(pl.Utf8).alias("date"))
    else:
        exprs.append(pl.lit(None).cast(pl.Utf8).alias("date"))

    for col in group_cols:
        if col == "date":
            continue
        if col in col_map:
            exprs.append(pl.col(col_map[col]).cast(pl.Utf8).str.strip_chars().fill_null("nan").alias(col))
        else:
            exprs.append(pl.lit("nan").alias(col))

    for col in metrics:
        if col in col_map:
            s = pl.col(col_map[col]).cast(pl.Utf8).str.replace_all("R\\$", "").str.replace_all(" ", "").str.replace(",", ".")
            exprs.append(s.cast(pl.Float64, strict=False).fill_null(0).alias(col))
        else:
            exprs.append(pl.lit(1 if col == "quantity" else 0).alias(col))

    df_norm = df.select(exprs)
    agg_exprs = [pl.col(m).sum().alias(m) for m in metrics]
    grouped = df_norm.group_by(group_cols).agg(agg_exprs)

    # Convert to rows and bulk_create
    row_repo = DatasetRowRepository(db)
    existing_hashes = row_repo.get_existing_hashes(user_id)
    processed_hashes = set()
    total = 0
    batch = []

    for r in grouped.iter_rows(named=True):
        row_clean = {k: (None if r[k] == "nan" else r[k]) for k in group_cols}
        parsed = _parse_date_flexible(row_clean["date"])
        row_clean["date"] = parsed if parsed is not None else date.today()
        row_hash = _generate_row_hash(row_clean, user_id)
        if row_hash in processed_hashes:
            continue
        processed_hashes.add(row_hash)
        rev = float(r.get("revenue") or 0)
        comm = float(r.get("commission") or 0)
        cost = float(r.get("cost") or 0)
        qty = int(r.get("quantity") or 1)
        profit = rev - comm - cost
        batch.append(
            DatasetRow(
                dataset_id=dataset_id,
                user_id=user_id,
                date=row_clean["date"],
                product=row_clean["product"] or "nan",
                platform=row_clean["platform"],
                category=row_clean["category"],
                status=row_clean["status"],
                sub_id1=row_clean["sub_id1"],
                order_id=row_clean["order_id"],
                product_id=row_clean["product_id"],
                revenue=rev,
                commission=comm,
                cost=cost,
                profit=profit,
                quantity=qty,
                row_hash=row_hash,
            )
        )
        total += 1
        if len(batch) >= BATCH_SIZE:
            row_repo.bulk_create(batch)
            batch = []

    if batch:
        row_repo.bulk_create(batch)
    return total


def _process_transaction_chunk_pandas(db: Session, dataset_id: int, user_id: int, chunk_content: bytes) -> int:
    """Fallback: use CSVService.validate_csv and same groupby/row_hash as DatasetService."""
    import pandas as pd
    from app.services.csv_service import CSVService

    df, errors = CSVService.validate_csv(chunk_content, "chunk.csv")
    if df is None or df.empty:
        return 0

    group_cols = ["date", "platform", "category", "product", "status", "sub_id1", "order_id", "product_id"]
    for col in group_cols:
        if col not in df.columns:
            df[col] = "nan"
        else:
            df[col] = df[col].fillna("nan").astype(str)
    metrics = ["revenue", "commission", "cost", "quantity"]
    for col in metrics:
        if col not in df.columns:
            df[col] = 1 if col == "quantity" else 0
    df_grouped = df.groupby(group_cols, as_index=False).agg({m: "sum" for m in metrics})

    row_repo = DatasetRowRepository(db)
    existing_hashes = row_repo.get_existing_hashes(user_id)
    processed_hashes = set()
    batch = []
    total = 0
    for _, row_data in df_grouped.iterrows():
        row_clean = {c: (None if row_data[c] == "nan" else row_data[c]) for c in group_cols}
        d = row_clean["date"]
        try:
            if d is None or pd.isna(d):
                row_clean["date"] = date.today()
            elif isinstance(d, datetime):
                row_clean["date"] = d.date()
            elif not isinstance(d, date):
                row_clean["date"] = date.today()
        except (TypeError, ValueError):
            row_clean["date"] = date.today()
        row_hash = _generate_row_hash(row_clean, user_id)
        if row_hash in processed_hashes:
            continue
        processed_hashes.add(row_hash)
        rev = float(row_data["revenue"])
        comm = float(row_data["commission"])
        cost = float(row_data["cost"])
        qty = int(row_data["quantity"])
        profit = rev - comm - cost
        batch.append(
            DatasetRow(
                dataset_id=dataset_id,
                user_id=user_id,
                date=row_clean["date"],
                product=row_clean["product"] or "nan",
                platform=row_clean["platform"],
                category=row_clean["category"],
                status=row_clean["status"],
                sub_id1=row_clean["sub_id1"],
                order_id=row_clean["order_id"],
                product_id=row_clean["product_id"],
                revenue=rev,
                commission=comm,
                cost=cost,
                profit=profit,
                quantity=qty,
                row_hash=row_hash,
            )
        )
        total += 1
        if len(batch) >= BATCH_SIZE:
            row_repo.bulk_create(batch)
            batch = []
    if batch:
        row_repo.bulk_create(batch)
    return total


def process_click_chunk(
    db: Session,
    dataset_id: int,
    user_id: int,
    chunk_content: bytes,
) -> int:
    """Validate click chunk (Polars or pandas), groupby, row_hash, bulk_create ClickRows. Returns count."""
    try:
        import polars as pl
    except ImportError:
        return _process_click_chunk_pandas(db, dataset_id, user_id, chunk_content)

    try:
        df = pl.read_csv(BytesIO(chunk_content), encoding="utf-8-lossy", ignore_errors=True)
    except Exception as e:
        logger.warning(f"Polars read failed for click chunk, fallback to pandas: {e}")
        return _process_click_chunk_pandas(db, dataset_id, user_id, chunk_content)

    if df.height == 0:
        return 0

    original_cols = df.columns
    col_map = {}
    for target, alias_set in ALIASES.items():
        found = find_column(original_cols, alias_set)
        if found:
            col_map[target] = found

    # date, channel, sub_id, clicks
    exprs = []
    if "date" in col_map:
        exprs.append(pl.col(col_map["date"]).cast(pl.Utf8).str.to_date(strict=False).alias("date"))
    else:
        exprs.append(pl.lit(None).cast(pl.Date).alias("date"))
    if "channel" in col_map:
        exprs.append(pl.col(col_map["channel"]).cast(pl.Utf8).str.strip_chars().fill_null("Desconhecido").alias("channel"))
    else:
        exprs.append(pl.lit("Desconhecido").alias("channel"))
    if "sub_id" in col_map:
        exprs.append(pl.col(col_map["sub_id"]).cast(pl.Utf8).str.strip_chars().alias("sub_id"))
    else:
        exprs.append(pl.lit(None).cast(pl.Utf8).alias("sub_id"))
    if "clicks" in col_map:
        exprs.append(pl.col(col_map["clicks"]).cast(pl.Int64, strict=False).fill_null(0).alias("clicks"))
    else:
        exprs.append(pl.lit(1).alias("clicks"))

    df_norm = df.select(exprs)
    grouped = df_norm.group_by(["date", "channel", "sub_id"]).agg(pl.col("clicks").sum().alias("clicks"))
    click_repo = ClickRowRepository(db)
    processed_hashes = set()
    batch = []
    total = 0
    for r in grouped.iter_rows(named=True):
        d = _parse_date_flexible(r.get("date"))
        if d is None:
            d = date.today()
        row_clean = {"date": d, "channel": r.get("channel") or "Desconhecido", "sub_id": r.get("sub_id")}
        row_hash = _generate_click_hash(row_clean, user_id)
        if row_hash in processed_hashes:
            continue
        processed_hashes.add(row_hash)
        batch.append(
            ClickRow(
                dataset_id=dataset_id,
                user_id=user_id,
                date=row_clean["date"],
                channel=row_clean["channel"],
                sub_id=row_clean["sub_id"],
                clicks=int(r.get("clicks") or 0),
                row_hash=row_hash,
            )
        )
        total += 1
        if len(batch) >= BATCH_SIZE:
            click_repo.bulk_create(batch)
            batch = []
    if batch:
        click_repo.bulk_create(batch)
    return total


def _process_click_chunk_pandas(db: Session, dataset_id: int, user_id: int, chunk_content: bytes) -> int:
    import pandas as pd
    from app.services.csv_service import CSVService

    df, errors = CSVService.validate_click_csv(chunk_content, "chunk.csv")
    if df is None or df.empty:
        return 0
    df["sub_id"] = df["sub_id"].fillna("nan")
    df_grouped = df.groupby(["date", "channel", "sub_id"], as_index=False)["clicks"].sum()
    click_repo = ClickRowRepository(db)
    processed_hashes = set()
    batch = []
    total = 0
    for _, row_data in df_grouped.iterrows():
        sub_id = None if row_data["sub_id"] == "nan" else row_data["sub_id"]
        d = row_data["date"]
        if d is None or (isinstance(d, pd.Timestamp) and pd.isna(d)):
            d = date.today()
        row_clean = {"date": d, "channel": row_data["channel"], "sub_id": sub_id}
        row_hash = _generate_click_hash(row_clean, user_id)
        if row_hash in processed_hashes:
            continue
        processed_hashes.add(row_hash)
        batch.append(
            ClickRow(
                dataset_id=dataset_id,
                user_id=user_id,
                date=row_clean["date"],
                channel=row_clean["channel"],
                sub_id=row_clean["sub_id"],
                clicks=int(row_data["clicks"]),
                row_hash=row_hash,
            )
        )
        total += 1
        if len(batch) >= BATCH_SIZE:
            click_repo.bulk_create(batch)
            batch = []
    if batch:
        click_repo.bulk_create(batch)
    return total
