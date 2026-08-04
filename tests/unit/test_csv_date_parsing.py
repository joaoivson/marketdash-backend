"""
Parsing de datas do CSV.

Bug real (04/08/2026): usuária não conseguia subir o relatório de cliques.
`pd.to_datetime(..., dayfirst=True)` era aplicado direto a datas ISO, que é o
formato do relatório da Shopee. O pandas lia `2026-08-03` como 8 de março e,
quando o dia passava de 12 (28, 29, 30, 31), não conseguia interpretar e
devolvia NaT. No arquivo dela: 184 de 253 linhas viravam nulas e as 69
restantes iam parar meses atrás.
"""
import pandas as pd

from app.services.csv_service import CSVService


def _parse(valores):
    return CSVService.parse_datetime_series(pd.Series(valores))


# --- ISO (relatório da Shopee) --------------------------------------------------


def test_iso_nao_troca_dia_por_mes():
    r = _parse(["2026-08-03 20:45:43"])
    assert r[0] == pd.Timestamp("2026-08-03 20:45:43")


def test_iso_com_dia_maior_que_12_nao_vira_nulo():
    """O caso que zerava 184 linhas: dia 28..31 não existe como mês."""
    r = _parse(["2026-07-28 10:00:00", "2026-07-29 10:00:00",
                "2026-07-30 10:00:00", "2026-07-31 10:00:00"])
    assert r.notna().all()
    assert [d.day for d in r] == [28, 29, 30, 31]
    assert [d.month for d in r] == [7, 7, 7, 7]


def test_iso_so_data():
    r = _parse(["2026-08-03", "2026-01-31"])
    assert r[0] == pd.Timestamp("2026-08-03")
    assert r[1] == pd.Timestamp("2026-01-31")


def test_arquivo_real_da_usuaria_nao_perde_nenhuma_linha():
    """Amostra do WebsiteClickReport que estava travando o upload."""
    valores = [
        "2026-08-03 20:45:43", "2026-08-03 17:47:16", "2026-08-01 13:27:04",
        "2026-07-28 08:12:00", "2026-07-29 23:59:59", "2026-07-30 12:00:00",
        "2026-07-31 00:00:01", "2026-08-02 06:30:00",
    ]
    r = _parse(valores)
    assert r.isna().sum() == 0
    # e as datas são as do arquivo, não o espelho de dia/mês
    assert sorted({d.month for d in r}) == [7, 8]


# --- formato BR (não pode regredir) ---------------------------------------------


def test_formato_br_continua_lendo_dia_primeiro():
    r = _parse(["03/08/2026", "28/07/2026"])
    assert r[0] == pd.Timestamp("2026-08-03")
    assert r[1] == pd.Timestamp("2026-07-28")


def test_br_com_hora():
    r = _parse(["03/08/2026 14:30:00"])
    assert r[0] == pd.Timestamp("2026-08-03 14:30:00")


# --- robustez -------------------------------------------------------------------


def test_valores_invalidos_viram_nat_sem_quebrar():
    r = _parse(["2026-08-03", "não é data", ""])
    assert r.notna().sum() == 1


def test_serie_vazia():
    assert len(_parse([])) == 0


def test_mistura_escolhe_a_leitura_que_aproveita_mais_linhas():
    # maioria ISO com uma linha suja: continua ISO, sem virar dayfirst
    r = _parse(["2026-07-28 10:00", "2026-07-29 10:00", "lixo"])
    assert r.notna().sum() == 2
    assert r[0].day == 28
