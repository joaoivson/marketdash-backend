"""Resolução de tempos do roteiro (F3): âncoras, offsets e avisos."""
from datetime import date, time

import pytest
from types import SimpleNamespace

from app.services.roteiro_service import (
    RoteiroInvalido, estimativa_de_duracao_s, ordens_no_passado,
    resolver_horarios,
)


def _passo(ordem, tipo="ancora", hora=None, data=None, offset=None,
           unidade="minutos"):
    """`offset` continua em MINUTOS na assinatura do helper — é a unidade em que
    o roteiro é pensado. O canônico gravado virou `offset_segundos` (082)."""
    return SimpleNamespace(
        ordem=ordem, tipo_tempo=tipo, hora_fixa=hora, data_fixa=data,
        offset_segundos=None if offset is None else offset * 60,
        offset_unidade=unidade,
    )


ANCORA = date(2026, 9, 1)


def test_ancora_mais_relativos_encadeiam():
    passos = [
        _passo(1, hora=time(8, 0)),
        _passo(2, tipo="relativo", offset=10),
        _passo(3, tipo="relativo", offset=110),
        _passo(4, hora=time(20, 0)),
    ]
    resolvidos, avisos = resolver_horarios(passos, ANCORA)
    horarios = [m.strftime("%H:%M") for _, m in resolvidos]
    assert horarios == ["08:00", "08:10", "10:00", "20:00"]
    assert avisos == []


def test_data_fixa_do_passo_vence_a_data_ancora():
    passos = [_passo(1, hora=time(9, 0), data=date(2026, 9, 15))]
    resolvidos, _ = resolver_horarios(passos, ANCORA)
    assert resolvidos[0][1].date() == date(2026, 9, 15)


def test_relativo_que_atravessa_a_proxima_ancora_gera_aviso():
    passos = [
        _passo(1, hora=time(8, 0)),
        _passo(2, tipo="relativo", offset=60 * 14),   # cai às 22h
        _passo(3, hora=time(9, 0)),                    # âncora às 9h
    ]
    _, avisos = resolver_horarios(passos, ANCORA)
    assert any("cai depois" in a for a in avisos)
    assert any("ANTES do passo anterior" in a for a in avisos)


def test_primeiro_passo_relativo_e_invalido():
    with pytest.raises(RoteiroInvalido):
        resolver_horarios([_passo(1, tipo="relativo", offset=10)], ANCORA)


def test_ancora_sem_hora_e_invalida():
    with pytest.raises(RoteiroInvalido):
        resolver_horarios([_passo(1, hora=None)], ANCORA)


def test_ancora_sem_data_e_sem_data_ancora_e_invalida():
    """Desde a 082 a data-âncora global saiu: cada passo de hora fixa carrega a
    própria data. Sem ela — e sem a rede da âncora legada — o passo não tem
    quando, e falhar aqui é melhor que disparar num dia arbitrário."""
    with pytest.raises(RoteiroInvalido):
        resolver_horarios([_passo(1, hora=time(9, 0))])


def test_offset_em_segundos_e_em_horas():
    passos = [
        _passo(1, hora=time(8, 0), data=ANCORA),
        _passo(2, tipo="relativo", offset=0, unidade="segundos"),
        _passo(3, tipo="relativo", offset=120, unidade="horas"),
    ]
    passos[1].offset_segundos = 30          # +30 segundos
    passos[2].offset_segundos = 2 * 3600    # +2 horas
    resolvidos, _ = resolver_horarios(passos)
    assert [m.strftime("%H:%M:%S") for _, m in resolvidos] == [
        "08:00:00", "08:00:30", "10:00:30",
    ]


def test_passo_no_passado_e_apontado_por_ordem():
    from datetime import datetime, timezone
    passos = [
        _passo(1, hora=time(8, 0), data=date(2020, 1, 1)),   # muito no passado
        _passo(2, tipo="relativo", offset=10),
    ]
    resolvidos, _ = resolver_horarios(passos)
    assert ordens_no_passado(resolvidos, datetime.now(timezone.utc)) == [1, 2]


def test_estimativa_cresce_com_o_total():
    # 2 msgs/rodada, pausa média 14s, jitter médio 2s → ~9s/msg
    assert estimativa_de_duracao_s(0) == 0
    assert 60 <= estimativa_de_duracao_s(10) <= 120
