import pandas as pd
import numpy as np
from typing import List, Dict, Any, Tuple
from datetime import datetime, date as date_cls
from io import BytesIO
import logging
import unicodedata

logger = logging.getLogger(__name__)

# Colunas alvo
TARGET_COLUMNS = ["date", "product", "revenue", "cost", "commission"]

# Mapeamento flexível de aliases (normalizados: minúsculo, sem acentos, sem espaços/pontuação)
ALIASES = {
    "date": {"date", "data", "datapedido", "data_do_pedido", "datadopedido", "horario", "horario_do_pedido", "horariodopedido", "tempo", "tempo_de_conclusao", "tempo_conclusao"},
    "time": {"hora", "horario", "hora_do_pedido", "horario_do_pedido"},
    "product": {"product", "produto", "idpedido", "id_do_pedido", "id_dopedido", "id_pagamento", "idpagamento", "produto_nome", "product_name", "nome_do_item"},
    "revenue": {
        "revenue", "receita", "valor", "valorvenda", "valor_receita", "valor_venda", "gross_value", "total",
        "valor_de_c", "valor_de_compra", "valor_de_compra_r", "valor_de_compra_rs", "valor_compra", "faturamento",
        "preco_r", "preco_rs", "preco", "preco_r$", "preco_rs$", "preco$"
    },
    "cost": {"cost", "custo", "valorcusto", "custo_total", "valor_do_r", "valor_gasto", "valor_gasto_anuncios", "gasto_anuncios"},
    "commission": {
        "commission", "comissao", "comissão", "taxa", "fee", "commission_value", "taxa_de_cc", "taxa_de_cartao",
        "comissao_liquido", "comissao_liquido_do_afiliado_rs", "comissao_liquido_do_afiliado_r",
        # Shopee / afiliados variações
        "comissao_shopee_r", "comissao_shopee_rs", "comissao_shopee_r$", "comissao_shopee_rs$",
        "comissao_total_do_item_r", "comissao_total_do_item_rs", "comissao_total_do_item_r$",
        "comissao_total_do_pedido_r", "comissao_total_do_pedido_rs", "comissao_total_do_pedido_r$",
        "taxa_de_comissao_shopee_do_item", "taxa_de_comissao_do_vendedor_do_item",
        "comissao_do_item_da_shopee_r", "comissao_do_item_da_marca_r", "comissao_do_vendedor_r"
    },
    "status": {"status", "status_do_pedido", "status_pedido"},
    "category": {"categoria", "categoria_global", "categoria_global_l1"},
    "sub_id1": {"sub_id1", "subid1"},
}


def normalize_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    only_ascii = "".join(c for c in nfkd if not unicodedata.combining(c))
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in only_ascii.lower())
    cleaned = "_".join(filter(None, cleaned.split("_")))
    return cleaned


def find_column(df_cols: List[str], aliases: set) -> str:
    for col in df_cols:
        norm = normalize_name(col)
        if norm in aliases:
            return col
    return ""


class CSVValidationError(Exception):
    """Exception raised for CSV validation errors."""
    pass


class CSVService:
    """Service for processing and validating CSV files."""

    @staticmethod
    def _clean_numeric_series(series: pd.Series) -> pd.Series:
        """
        Limpa strings com R$, espaços, separadores de milhar e converte vírgula decimal para ponto.
        """
        cleaned = (
            series.astype(str)
            .str.replace(r"[R$\s]", "", regex=True)
            .str.replace(".", "", regex=False)  # remove separador de milhar
            .str.replace(",", ".", regex=False)  # vírgula -> ponto
        )
        return pd.to_numeric(cleaned, errors="coerce")

    @staticmethod
    def validate_csv(file_content: bytes, filename: str) -> Tuple[pd.DataFrame, List[str]]:
        """
        Validate and parse CSV file (flexível). Se colunas estiverem ausentes, cria padrões.
        Retorna dataframe sempre com as colunas TARGET_COLUMNS + profit.
        """
        errors = []

        try:
            df = None
            df_orig = None
            for encoding in ['utf-8', 'latin-1', 'iso-8859-1']:
                try:
                    df = pd.read_csv(BytesIO(file_content), encoding=encoding)
                    df_orig = df.copy()
                    break
                except UnicodeDecodeError:
                    continue

            if df is None:
                errors.append("Não foi possível decodificar o arquivo CSV. Verifique a codificação.")
                return None, errors

            if df.empty:
                errors.append("O arquivo CSV está vazio.")
                return None, errors

            original_cols = df.columns.tolist()
            norm_map = {col: normalize_name(col) for col in original_cols}

            col_map = {}
            for target, alias_set in ALIASES.items():
                found = find_column(original_cols, alias_set)
                if found:
                    col_map[target] = found

            # Criar dataframe final
            out = pd.DataFrame()

            # Date e time
            if "date" in col_map:
                parsed_date = pd.to_datetime(df[col_map["date"]], errors="coerce", dayfirst=True)
            else:
                parsed_date = None
                for col in original_cols:
                    candidate = pd.to_datetime(df[col], errors="coerce", dayfirst=True)
                    if candidate.notna().any():
                        parsed_date = candidate
                        break
                if parsed_date is None:
                    parsed_date = pd.Timestamp("today")
                    errors.append("Coluna de data ausente; usando data atual.")

            out["date"] = parsed_date.dt.date if hasattr(parsed_date, "dt") else parsed_date

            if "time" in col_map:
                parsed_time = pd.to_datetime(df[col_map["time"]], errors="coerce")
                out["time"] = parsed_time.dt.time
            else:
                out["time"] = None

            # Produto
            if "product" in col_map:
                out["product"] = df[col_map["product"]].astype(str).str.strip()
            else:
                out["product"] = df.index.astype(str)
                errors.append("Coluna de produto ausente; usando índice como produto.")

            # Numéricas
            for target in ["revenue", "cost", "commission"]:
                if target in col_map:
                    numeric_series = CSVService._clean_numeric_series(df[col_map[target]])
                    out[target] = numeric_series.fillna(0)
                else:
                    out[target] = 0
                    errors.append(f"Coluna '{target}' ausente; preenchendo com 0.")

            # Status, categoria, sub_id1
            out["status"] = df[col_map["status"]].astype(str).str.strip() if "status" in col_map else None
            out["category"] = df[col_map["category"]].astype(str).str.strip() if "category" in col_map else None
            out["sub_id1"] = df[col_map["sub_id1"]].astype(str).str.strip() if "sub_id1" in col_map else None
            out["mes_ano"] = out["date"].apply(lambda d: f"{d.year:04d}-{d.month:02d}" if isinstance(d, (pd.Timestamp, date_cls)) or hasattr(d, 'year') else None)

            # Limpezas
            out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
            if out["time"].isnull().all():
                out["time"] = None
            out["product"] = out["product"].replace({"": "Produto"}, regex=False)
            out["revenue"] = out["revenue"].clip(lower=0)
            out["cost"] = out["cost"].clip(lower=0)
            out["commission"] = out["commission"].clip(lower=0)

            # Profit
            out["profit"] = out["revenue"] - out["cost"] - out["commission"]

            # raw_data preserva colunas originais (sanitizando NaN -> None)
            if df_orig is not None:
                rows_raw = df_orig.iloc[out.index]
                rows_raw = rows_raw.replace({np.nan: None})
                out["raw_data"] = rows_raw.to_dict("records")

            # Remove linhas vazias de produto
            out = out[out["product"] != ""]

            if out.empty:
                errors.append("Após processamento, nenhuma linha válida restou.")
                return None, errors

            return out.reset_index(drop=True), errors

        except pd.errors.EmptyDataError:
            errors.append("O arquivo CSV está vazio ou mal formatado.")
            return None, errors
        except Exception as e:
            logger.error(f"Erro ao processar CSV: {str(e)}")
            errors.append(f"Erro ao processar arquivo CSV: {str(e)}")
            return None, errors

    @staticmethod
    def dataframe_to_dict_list(df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Convert DataFrame to list of dictionaries for database insertion.
        """
        return df.to_dict('records')

