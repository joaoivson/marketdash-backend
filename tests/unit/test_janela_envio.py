"""Janela de horário (D4): BRT, pausa do meio-dia, bordas e config lixo."""
from datetime import datetime

from app.services.janela_envio_service import (
    BRT, ConfigJanela, JanelaDia, carregar_config, janela_aberta,
    proxima_abertura,
)


def _dt(ano, mes, dia, h, m=0):
    return datetime(ano, mes, dia, h, m, tzinfo=BRT)


def test_padrao_08_22_todos_os_dias():
    c = ConfigJanela()
    assert janela_aberta(c, _dt(2026, 9, 1, 12)) is True     # terça meio-dia
    assert janela_aberta(c, _dt(2026, 9, 1, 7, 59)) is False
    assert janela_aberta(c, _dt(2026, 9, 1, 22, 0)) is False  # fim exclusivo


def test_dia_desativado_e_pausa_do_meio_dia():
    c = ConfigJanela(dias={
        "6": JanelaDia(ativo=False),   # domingo off
        "0": JanelaDia(pausa_inicio=datetime(2000,1,1,12).time(),
                       pausa_fim=datetime(2000,1,1,14).time()),
    })
    assert janela_aberta(c, _dt(2026, 9, 6, 12)) is False    # domingo
    assert janela_aberta(c, _dt(2026, 8, 31, 13)) is False   # segunda na pausa
    assert janela_aberta(c, _dt(2026, 8, 31, 14)) is True    # pausa acabou


def test_toggle_geral_desligado_libera_tudo():
    c = ConfigJanela(ativo=False)
    assert janela_aberta(c, _dt(2026, 9, 1, 3)) is True


def test_proxima_abertura_no_mesmo_dia_e_no_dia_seguinte():
    c = ConfigJanela()
    # às 6h → abre às 8h do mesmo dia
    assert proxima_abertura(c, _dt(2026, 9, 1, 6)) == _dt(2026, 9, 1, 8)
    # às 23h → abre às 8h do dia seguinte
    assert proxima_abertura(c, _dt(2026, 9, 1, 23)) == _dt(2026, 9, 2, 8)
    # dentro da janela → o próprio momento
    aberto = _dt(2026, 9, 1, 12)
    assert proxima_abertura(c, aberto) == aberto


def test_proxima_abertura_pula_dia_desativado_e_retoma_pos_pausa():
    c = ConfigJanela(dias={"6": JanelaDia(ativo=False)})
    # domingo 12h → segunda 8h
    assert proxima_abertura(c, _dt(2026, 9, 6, 12)) == _dt(2026, 9, 7, 8)
    c2 = ConfigJanela(dias={"1": JanelaDia(
        pausa_inicio=datetime(2000,1,1,12).time(),
        pausa_fim=datetime(2000,1,1,14).time())})
    # terça 12h30 (na pausa) → 14h
    assert proxima_abertura(c2, _dt(2026, 9, 1, 12, 30)) == _dt(2026, 9, 1, 14)


def test_config_lixo_degrada_para_o_padrao():
    c = carregar_config({"dias": {"0": {"inicio": "25:99"}}})
    assert janela_aberta(c, _dt(2026, 9, 1, 12)) is True


def test_janela_que_nunca_abre_devolve_none_nao_livelock():
    c = ConfigJanela(dias={str(i): JanelaDia(ativo=False) for i in range(7)})
    assert proxima_abertura(c, _dt(2026, 9, 1, 12)) is None
