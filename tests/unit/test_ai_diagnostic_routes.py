"""Mapeamento de exceção → status HTTP nas rotas do Diagnóstico IA."""
import pytest
from fastapi import HTTPException

from app.api.v1.routes.ai_diagnostics import traduzir_erro
from app.services.ai_credit_service import SaldoInsuficiente
from app.services.ai_diagnostic_service import (
    GeracaoEmAndamento, LimiteDeMensagens, PeriodoVazio,
)
from app.services.openai_client import ErroIA


def test_sem_saldo_vira_402_com_saldo_no_corpo():
    e = traduzir_erro(SaldoInsuficiente(saldo=3, necessario=10))
    assert e.status_code == 402
    assert e.detail["saldo"] == 3
    assert e.detail["necessario"] == 10


def test_periodo_vazio_vira_422():
    assert traduzir_erro(PeriodoVazio()).status_code == 422


def test_geracao_em_andamento_vira_409():
    assert traduzir_erro(GeracaoEmAndamento(7)).status_code == 409


def test_limite_de_mensagens_vira_429():
    assert traduzir_erro(LimiteDeMensagens()).status_code == 429


def test_ia_indisponivel_vira_503():
    assert traduzir_erro(ErroIA("sem_chave")).status_code == 503


def test_erro_desconhecido_nao_e_traduzido():
    assert traduzir_erro(RuntimeError("boom")) is None
