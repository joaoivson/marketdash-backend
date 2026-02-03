import pandas as pd
import numpy as np
from typing import List, Dict, Any, Tuple
from datetime import datetime, date as date_cls
from io import BytesIO
import logging
import unicodedata

logger = logging.getLogger(__name__)

# Colunas alvo
TARGET_COLUMNS = ["date", "product", "revenue", "cost", "commission", "quantity"]

# Mapeamento flexível de aliases (normalizados: minúsculo, sem acentos, sem espaços/pontuação)
ALIASES = {
    "date": {"date", "data", "datapedido", "data_do_pedido", "datadopedido", "horario", "horario_do_pedido", "horariodopedido", "tempo", "tempo_de_conclusao", "tempo_conclusao", "tempo_dos_cliques"},
    "time": {"hora", "horario", "hora_do_pedido", "horario_do_pedido", "tempo_dos_cliques"},
    "product": {"product", "produto", "produto_nome", "product_name", "nome_do_item"},
    "order_id": {"order_id", "idpedido", "id_do_pedido", "id_dopedido", "id_pagamento", "idpagamento", "numero_do_pedido", "id_do_pedido"},
    "product_id": {"product_id", "id_do_item", "id_item", "item_id", "id_do_produto", "product_id"},
    "platform": {"platform", "plataforma", "canal", "channel", "origem", "origem_do_pedido"},
    "revenue": {
        "revenue", "receita", "valor", "valorvenda", "valor_receita", "valor_venda", "gross_value", "total",
        "valor_de_c", "valor_de_compra", "valor_de_compra_r", "valor_de_compra_rs", "valor_compra", "faturamento",
        "preco_r", "preco_rs", "preco", "preco_r$", "preco_rs$", "preco$", "valor_de_compra_r_s", "valor_de_compra_r",
        "valor_de_compra_r_s_r"
    },
    "cost": {"cost", "custo", "valorcusto", "custo_total", "valor_do_r", "valor_gasto", "valor_gasto_anuncios", "gasto_anuncios"},
    "commission": {
        "commission", "comissao", "comissão", "taxa", "fee", "commission_value", "taxa_de_cc", "taxa_de_cartao",
        "comissao_liquido", "comissao_liquido_do_afiliado_rs", "comissao_liquido_do_afiliado_r",
        "comissao_liquida", "comissao_liquida_do_afiliado_rs", "comissao_liquida_do_afiliado_r",
        "comissao_liquida_do_afiliado", "comissao_liquida_do_afiliado_r_s", "comissao_liquida_do_afiliado_r",
        "comissao_liquida_do_afiliado_r", "comissao_liquida_do_afiliado_r_s_r",
        "comissa_o_liquida_do_afiliado_r", "comissa_o_la_quida_do_afiliado_r",
        # Shopee / afiliados variações
        "comissao_total_do_item_r", "comissao_total_do_item_rs", "comissao_total_do_item_r$",
        "comissao_total_do_pedido_r", "comissao_total_do_pedido_rs", "comissao_total_do_pedido_r$",
        "taxa_de_comissao_shopee_do_item", "taxa_de_comissao_do_vendedor_do_item",
        "comissao_do_item_da_shopee_r", "comissao_do_item_da_marca_r", "comissao_do_vendedor_r",
        "comissao_shopee_r", "comissao_shopee_rs", "comissao_shopee_r$", "comissao_shopee_rs$"
    },
    "quantity": {"quantity", "quantidade", "qtd", "item_count", "count", "vendas", "sales_count"},

    "status": {"status", "status_do_pedido", "status_pedido"},
    "category": {"categoria", "categoria_global", "categoria_global_l1"},
    "sub_id1": {"sub_id1", "subid1"},
    "channel": {"channel", "canal", "origem", "origem_do_pedido", "plataforma", "platform", "referenciador", "referrer"},
    "clicks": {"clicks", "cliques", "total_de_cliques", "cliques_por_canal", "cliques_por_hora", "quantidade_cliques", "cliques_count"},
    "sub_id": {"sub_id", "subid", "subid1", "subid2", "id_sub", "referencia"},
}


def normalize_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    only_ascii = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Substituir qualquer caractere não alfanumérico por underscore, mas preservar o underscore se já existir
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in only_ascii.lower())
    # Remover underscores duplicados e das extremidades
    cleaned = "_".join(filter(None, cleaned.split("_")))
    
    # Log para debug (opcional, habilitar se necessário)
    # print(f"DEBUG: '{name}' -> '{cleaned}'")
    
    return cleaned


def find_column(df_cols: List[str], aliases: set) -> str:
    # Priorizar correspondências exatas de substrings importantes para evitar captura errada de taxas
    priority_order = [
        "comissao_liquida_do_afiliado_r", 
        "comissao_liquido_do_afiliado_r",
        "comissa_o_liquida_do_afiliado_r", 
        "comissa_o_la_quida_do_afiliado_r",
        "valor_de_compra_r",
        "valor_venda",
        "revenue",
        "commission"
    ]
    
    # 1. Tentar encontrar as colunas prioritárias primeiro
    for priority in priority_order:
        if priority in aliases:
            for col in df_cols:
                if normalize_name(col) == priority:
                    return col
                    
    # 2. Se não encontrar prioritária, usar a lógica original de busca no set
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
        Limpa strings com R$, espaços e converte para numérico de forma robusta.
        Detecta automaticamente se o separador decimal é ponto ou vírgula.
        """
        def clean_value(val):
            if pd.isna(val) or val is None:
                return np.nan
            
            # Converter para string e limpar
            s = str(val).replace("R$", "").replace(" ", "").strip()
            
            # Remover caracteres invisíveis ou de codificação se houver
            s = "".join(ch for ch in s if ch.isdigit() or ch in ".,-")
            
            if not s:
                return np.nan
                
            # Se houver vírgula e ponto, assume formato 1.234,56 (Padrão BR)
            if "," in s and "." in s:
                s = s.replace(".", "").replace(",", ".")
            # Se houver apenas vírgula, assume que é o decimal: 1234,56 -> 1234.56
            elif "," in s:
                s = s.replace(",", ".")
            # Se houver apenas ponto:
            elif "." in s:
                # Se houver múltiplos pontos, remove todos (milhar): 1.000.000 -> 1000000
                if s.count(".") > 1:
                    s = s.replace(".", "")
                # Se houver apenas um ponto, mas a parte decimal tiver 2 ou 3 dígitos, 
                # e o número for pequeno, é quase certo que é decimal (ex: 1.197 ou 1.19)
                # Se o número for "1.000", é ambíguo, mas float(s) trata como 1.0.
            
            try:
                result = float(s)
                # print(f"DEBUG_CLEAN: '{val}' -> '{s}' -> {result}") # Habilitar se necessário
                return result
            except ValueError:
                return np.nan

        return series.apply(clean_value)

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
            for target in ["revenue", "cost", "commission", "quantity"]:
                if target in col_map:
                    numeric_series = CSVService._clean_numeric_series(df[col_map[target]])
                    out[target] = numeric_series.fillna(0)
                else:
                    if target == "quantity":
                        out[target] = 1 # Padrão para quantidade é 1
                    else:
                        out[target] = 0
                        errors.append(f"Coluna '{target}' ausente; preenchendo com 0.")

            # Status, categoria, sub_id1, plataforma, order_id, product_id
            out["status"] = df[col_map["status"]].astype(str).str.strip() if "status" in col_map else None
            out["category"] = df[col_map["category"]].astype(str).str.strip() if "category" in col_map else None
            out["sub_id1"] = df[col_map["sub_id1"]].astype(str).str.strip() if "sub_id1" in col_map else None
            out["platform"] = df[col_map["platform"]].astype(str).str.strip() if "platform" in col_map else None
            out["order_id"] = df[col_map["order_id"]].astype(str).str.strip() if "order_id" in col_map else None
            out["product_id"] = df[col_map["product_id"]].astype(str).str.strip() if "product_id" in col_map else None

            # Limpezas - converter date para datetime primeiro
            out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
            
            # Calcular mes_ano após garantir que date é uma Series de dates
            def calc_mes_ano(d):
                if d is None or pd.isna(d):
                    return None
                if isinstance(d, (pd.Timestamp, date_cls)) or hasattr(d, 'year'):
                    try:
                        return f"{d.year:04d}-{d.month:02d}"
                    except (AttributeError, TypeError):
                        return None
                return None
            
            out["mes_ano"] = out["date"].apply(calc_mes_ano)
            if out["time"].isnull().all():
                out["time"] = None
            out["product"] = out["product"].replace({"": "Produto"}, regex=False)
            out["revenue"] = out["revenue"].clip(lower=0)
            out["cost"] = out["cost"].clip(lower=0)
            out["commission"] = out["commission"].clip(lower=0)
            out["quantity"] = out["quantity"].clip(lower=0)

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
    def validate_click_csv(file_content: bytes, filename: str) -> Tuple[pd.DataFrame, List[str]]:
        """
        Valida e processa CSV de cliques.
        Retorna dataframe com date, time, channel, clicks, sub_id, raw_data.
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
                errors.append("Não foi possível decodificar o arquivo CSV de cliques.")
                return None, errors

            if df.empty:
                errors.append("O arquivo CSV de cliques está vazio.")
                return None, errors

            original_cols = df.columns.tolist()
            col_map = {}
            for target, alias_set in ALIASES.items():
                found = find_column(original_cols, alias_set)
                if found:
                    col_map[target] = found

            out = pd.DataFrame()

            # Date e time (lógica similar ao original)
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
                    errors.append("Data ausente no arquivo de cliques; usando hoje.")

            out["date"] = pd.to_datetime(parsed_date, errors="coerce").dt.date

            if "time" in col_map:
                parsed_time = pd.to_datetime(df[col_map["time"]], errors="coerce")
                out["time"] = parsed_time.dt.time
            else:
                out["time"] = None

            # Canal / Platform
            if "channel" in col_map:
                out["channel"] = df[col_map["channel"]].astype(str).str.strip()
            elif "platform" in col_map:
                out["channel"] = df[col_map["platform"]].astype(str).str.strip()
            else:
                out["channel"] = "Desconhecido"
                errors.append("Coluna de canal não encontrada; usando 'Desconhecido'.")

            # Cliques
            if "clicks" in col_map:
                out["clicks"] = pd.to_numeric(df[col_map["clicks"]], errors="coerce").fillna(0).astype(int)
            else:
                # Se não houver coluna de cliques, assume 1 clique por linha (cada linha é um evento)
                out["clicks"] = 1
                logger.info("Coluna de cliques não encontrada; assumindo 1 por linha.")

            # Sub ID
            if "sub_id" in col_map:
                out["sub_id"] = df[col_map["sub_id"]].astype(str).str.strip()
            elif "sub_id1" in col_map:
                out["sub_id"] = df[col_map["sub_id1"]].astype(str).str.strip()
            else:
                out["sub_id"] = None

            # raw_data
            if df_orig is not None:
                rows_raw = df_orig.iloc[out.index].replace({np.nan: None})
                out["raw_data"] = rows_raw.to_dict("records")

            return out.reset_index(drop=True), errors

        except Exception as e:
            logger.error(f"Erro ao processar CSV de cliques: {str(e)}")
            errors.append(f"Erro ao processar CSV de cliques: {str(e)}")
            return None, errors

    @staticmethod
    def dataframe_to_dict_list(df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Convert DataFrame to list of dictionaries for database insertion.
        """
        return df.to_dict('records')

