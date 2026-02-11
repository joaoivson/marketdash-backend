"""
Normalização de order_id/product_id e geração de row_hash para deduplicação de linhas de comissão.
Usado por DatasetService e csv_polars para garantir o mesmo hash entre re-uploads do mesmo relatório.
"""
import hashlib
import math
from typing import Any


def normalize_id(value: Any) -> str:
    """
    Normaliza order_id ou product_id para uso no row_hash.
    - None, NaN ou vazio -> "nan"
    - Valores numéricos (int, float ou string numérica) -> string de inteiro (ex.: "12345.0" -> "12345")
    - Demais strings -> strip + lower
    Assim, re-exportações do mesmo relatório geram o mesmo hash e o upsert atualiza em vez de duplicar.
    """
    if value is None:
        return "nan"
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return "nan"
    s = str(value).strip().lower()
    if not s or s in ("nan", "inf", "-inf", "infinity", "-infinity"):
        return "nan"
    try:
        f = float(s)
        if math.isnan(f) or math.isinf(f):
            return "nan"
        return str(int(f))
    except (ValueError, TypeError, OverflowError):
        return "nan"


def generate_row_hash(user_id: int, order_id: Any, product_id: Any) -> str:
    """
    Gera hash MD5 determinístico para o registro de venda.
    Utiliza user_id + order_id + product_id normalizados para garantir unicidade por item de pedido.
    """
    components = [
        str(user_id),
        normalize_id(order_id),
        normalize_id(product_id),
    ]
    row_str = "|".join(components)
    return hashlib.md5(row_str.encode()).hexdigest()
