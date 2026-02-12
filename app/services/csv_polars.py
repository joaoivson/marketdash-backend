"""
Chunk processing with Polars (first option) and pandas fallback.
Same semantics as DatasetService.process_commission_csv and ClickService.process_click_csv:
groupby, row_hash, bulk_create. Used by process_chunk Celery task.
"""
import hashlib
import logging

from app.utils.row_hash import generate_row_hash as _generate_row_hash_impl
from datetime import date
from io import BytesIO

from sqlalchemy.orm import Session

from app.models.dataset_row import DatasetRow
from app.models.click_row import ClickRow
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.repositories.click_row_repository import ClickRowRepository
from app.services.csv_service import ALIASES, normalize_name, find_column

logger = logging.getLogger(__name__)

BATCH_SIZE = 5000


def _generate_row_hash(row_data: dict, user_id: int) -> str:
    """Same as DatasetService._generate_row_hash; uses shared normalize_id for stable hash across re-uploads."""
    return _generate_row_hash_impl(
        user_id,
        row_data.get("order_id"),
        row_data.get("product_id"),
        row_data.get("status"),
    )


def _generate_click_hash(row_data: dict, user_id: int) -> str:
    """Unicidade por (user_id, date, channel, sub_id)."""
    date_val = row_data.get("date")
    date_str = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)
    sub_id_val = row_data.get("sub_id")
    sub_id_str = (str(sub_id_val).strip().lower() if sub_id_val not in (None, "") else "")
    components = [
        str(user_id),
        date_str,
        str(row_data.get("channel") or "Desconhecido").strip().lower(),
        sub_id_str,
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

    # Build normalized frame for groupby (minimal set) — incluir time quando coluna for datetime
    group_cols = ["date", "time", "platform", "category", "product", "status", "sub_id1", "order_id", "product_id"]
    metrics = ["revenue", "commission", "cost", "quantity"]
    exprs = []

    # Aceitar coluna datetime (ex.: 2026-01-07 23:59:22) e extrair data e hora
    if "date" in col_map:
        date_col = pl.col(col_map["date"]).cast(pl.Utf8)
        dt_col = date_col.str.to_datetime(strict=False)
        exprs.append(dt_col.dt.date().alias("date"))
        exprs.append(dt_col.dt.time().alias("time"))
    else:
        exprs.append(pl.lit(None).cast(pl.Date).alias("date"))
        exprs.append(pl.lit(None).cast(pl.Time).alias("time"))

    for col in group_cols:
        if col in ("date", "time"):
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
        row_clean = {}
        for k in group_cols:
            if k == "time":
                row_clean[k] = r.get(k)
            else:
                row_clean[k] = None if r[k] == "nan" else r[k]
        if isinstance(row_clean["date"], str):
            try:
                row_clean["date"] = date.fromisoformat(row_clean["date"]) if row_clean["date"] else None
            except Exception:
                row_clean["date"] = date.today()
        if row_clean.get("date") is None:
            row_clean["date"] = date.today()
        _time = row_clean.get("time")
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
                time=_time,
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

    group_cols = ["date", "time", "platform", "category", "product", "status", "sub_id1", "order_id", "product_id"]
    if "time" not in df.columns:
        df["time"] = None
    for col in group_cols:
        if col not in df.columns:
            df[col] = None if col == "time" else "nan"
        elif col != "time":
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
        row_clean = {}
        for c in group_cols:
            if c == "time":
                row_clean[c] = row_data.get(c)
            else:
                row_clean[c] = None if row_data[c] == "nan" else row_data[c]
        row_hash = _generate_row_hash(row_clean, user_id)
        if row_hash in processed_hashes:
            continue
        processed_hashes.add(row_hash)
        rev = float(row_data["revenue"])
        comm = float(row_data["commission"])
        cost = float(row_data["cost"])
        qty = int(row_data["quantity"])
        profit = rev - comm - cost
        _time = row_clean.get("time")
        if _time is not None:
            try:
                import pandas as _pd
                if _pd.isna(_time):
                    _time = None
            except Exception:
                pass
        batch.append(
            DatasetRow(
                dataset_id=dataset_id,
                user_id=user_id,
                date=row_clean["date"],
                time=_time,
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

    # date, time, channel, sub_id, clicks — aceitar datetime e extrair data e hora
    exprs = []
    if "date" in col_map:
        date_col = pl.col(col_map["date"]).cast(pl.Utf8)
        dt_col = date_col.str.to_datetime(strict=False)
        exprs.append(dt_col.dt.date().alias("date"))
        exprs.append(dt_col.dt.time().alias("time"))
    else:
        exprs.append(pl.lit(None).cast(pl.Date).alias("date"))
        exprs.append(pl.lit(None).cast(pl.Time).alias("time"))
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
    # Agrupar por (date, channel, sub_id): um ClickRow por grupo; clicks = soma, time = primeira hora
    agg_exprs = [pl.col("clicks").sum().alias("clicks")]
    if "time" in df_norm.columns:
        agg_exprs.append(pl.col("time").first().alias("time"))
    grouped = df_norm.group_by(["date", "channel", "sub_id"]).agg(agg_exprs)
    click_repo = ClickRowRepository(db)
    processed_hashes = set()
    batch = []
    total = 0
    for r in grouped.iter_rows(named=True):
        d = r.get("date")
        if isinstance(d, str):
            try:
                d = date.fromisoformat(d) if d else date.today()
            except Exception:
                d = date.today()
        if d is None:
            d = date.today()
        _time = r.get("time")
        _sub_id = r.get("sub_id")
        if _sub_id is not None and isinstance(_sub_id, str) and _sub_id.strip() == "":
            _sub_id = None
        row_clean = {"date": d, "channel": r.get("channel") or "Desconhecido", "sub_id": _sub_id}
        row_hash = _generate_click_hash(row_clean, user_id)
        if row_hash in processed_hashes:
            continue
        processed_hashes.add(row_hash)
        batch.append(
            ClickRow(
                dataset_id=dataset_id,
                user_id=user_id,
                date=row_clean["date"],
                time=_time,
                channel=row_clean["channel"],
                sub_id=_sub_id,
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
    from app.services.csv_service import CSVService

    df, errors = CSVService.validate_click_csv(chunk_content, "chunk.csv")
    if df is None or df.empty:
        return 0
    if "sub_id" not in df.columns:
        df["sub_id"] = None
    agg_dict = {"clicks": "sum"}
    if "time" in df.columns:
        agg_dict["time"] = "first"
    df_grouped = df.groupby(["date", "channel", "sub_id"], as_index=False).agg(agg_dict)
    click_repo = ClickRowRepository(db)
    processed_hashes = set()
    batch = []
    total = 0
    for _, row_data in df_grouped.iterrows():
        sub_id_val = row_data.get("sub_id")
        if sub_id_val is not None:
            if isinstance(sub_id_val, float):
                import math
                if math.isnan(sub_id_val):
                    sub_id_val = None
                else:
                    sub_id_val = str(int(sub_id_val)) if sub_id_val == sub_id_val else None
            elif isinstance(sub_id_val, str) and sub_id_val.strip() == "":
                sub_id_val = None
            else:
                sub_id_val = str(sub_id_val).strip() or None
        row_clean = {"date": row_data["date"], "channel": row_data["channel"], "sub_id": sub_id_val, "clicks": row_data["clicks"]}
        row_hash = _generate_click_hash(row_clean, user_id)
        if row_hash in processed_hashes:
            continue
        processed_hashes.add(row_hash)
        batch.append(
            ClickRow(
                dataset_id=dataset_id,
                user_id=user_id,
                date=row_clean["date"],
                time=row_data.get("time"),
                channel=row_clean["channel"],
                sub_id=sub_id_val,
                clicks=int(row_clean["clicks"]),
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
