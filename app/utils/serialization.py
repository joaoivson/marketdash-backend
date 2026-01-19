import datetime
import math
from decimal import Decimal
from typing import Any, Dict, Optional

import pandas as pd


def serialize_value(value: Any):
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


def clean_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)) and not (isinstance(value, float) and math.isnan(value)):
        return float(value)
    if isinstance(value, str):
        cleaned = (
            value.replace("R$", "")
            .replace("%", "")
            .replace(" ", "")
            .replace("\u00a0", "")
        )
        has_comma = "," in cleaned
        has_dot = "." in cleaned
        if has_comma and has_dot:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif has_comma:
            cleaned = cleaned.replace(".", "").replace(",", ".")
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


def normalize_raw_data(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return raw
    normalized = {}
    for k, v in raw.items():
        key_lower = k.lower()
        if key_lower.startswith("valor") or key_lower.startswith("comiss"):
            parsed = clean_number(v)
            normalized[k] = parsed if parsed is not None else serialize_value(v)
        else:
            normalized[k] = serialize_value(v)
    return normalized
