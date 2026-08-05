"""Os models do Diagnóstico IA batem com a migration 043."""
from app.models.ai_diagnostic import (
    STATUS_ERRO, STATUS_GERANDO, STATUS_PRONTO, AiDiagnostic, AiDiagnosticMessage,
)
from app.models.ai_credit_ledger import AiCreditLedger


def test_tabelas_com_os_nomes_da_migration():
    assert AiDiagnostic.__tablename__ == "ai_diagnostics"
    assert AiDiagnosticMessage.__tablename__ == "ai_diagnostic_messages"
    assert AiCreditLedger.__tablename__ == "ai_credit_ledger"


def test_colunas_do_diagnostico():
    cols = set(AiDiagnostic.__table__.columns.keys())
    assert {"user_id", "periodo_inicio", "periodo_fim", "snapshot", "relatorio",
            "status", "erro_mensagem", "modelo", "tokens_entrada", "tokens_saida",
            "criado_em", "concluido_em"} <= cols


def test_colunas_do_ledger():
    cols = set(AiCreditLedger.__table__.columns.keys())
    assert {"user_id", "diagnostic_id", "tipo", "creditos", "saldo_apos"} <= cols


def test_colunas_do_diagnostico_message():
    cols = set(AiDiagnosticMessage.__table__.columns.keys())
    assert {"id", "diagnostic_id", "papel", "conteudo", "criado_em"} <= cols


def test_status_comeca_em_gerando():
    assert AiDiagnostic.__table__.c.status.default.arg == STATUS_GERANDO
    assert (STATUS_GERANDO, STATUS_PRONTO, STATUS_ERRO) == ("gerando", "pronto", "erro")
