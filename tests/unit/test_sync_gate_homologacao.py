"""Gate de sincronização em homologação (app/core/sync_gate.py).

O gate só pode ligar no ambiente certo: se ligasse em produção, nenhuma
aluna sincronizaria — falha bem pior do que a que ele previne.
"""

import pytest

from app.core.ambiente import (
    REF_HOMOLOGACAO,
    REF_PRODUCAO,
    identidade_do_banco,
    is_homologacao,
    is_producao,
)
from app.core.config import settings
from app.core.sync_gate import EMAILS_LIBERADOS_EM_HOMOLOGACAO, sync_liberado_para

URL_HML = f"postgresql://postgres.{REF_HOMOLOGACAO}:senha@aws-0-sa-east-1.pooler.supabase.com:5432/postgres"
URL_HML_DIRETA = f"postgresql://postgres:senha@db.{REF_HOMOLOGACAO}.supabase.co:5432/postgres"
URL_PROD = f"postgresql://postgres.{REF_PRODUCAO}:senha@aws-0-sa-east-1.pooler.supabase.com:5432/postgres"
URL_LOCAL = "postgresql://postgres:postgres@localhost:5432/marketdash"

LUIZ = "lfernandooliveira@outlook.com"


class TestDeteccaoDeAmbiente:
    @pytest.mark.parametrize("url", [URL_HML, URL_HML_DIRETA])
    def test_homologacao_reconhecida_nos_dois_formatos_de_url(self, url):
        """Pooler (`postgres.<ref>`) e conexão direta (`db.<ref>.supabase.co`)."""
        assert is_homologacao(url) is True
        assert is_producao(url) is False

    def test_producao_nao_e_homologacao(self):
        assert is_homologacao(URL_PROD) is False
        assert is_producao(URL_PROD) is True

    def test_banco_local_nao_e_nenhum_dos_dois(self):
        """Dev local não pode ser confundido com ambiente gerenciado."""
        assert is_homologacao(URL_LOCAL) is False
        assert is_producao(URL_LOCAL) is False

    def test_url_vazia_nao_liga_o_gate(self):
        assert is_homologacao("") is False

    def test_identidade_do_banco_local_e_estavel(self):
        """Sem ref, cai no hash — e o mesmo banco tem sempre a mesma fila."""
        assert identidade_do_banco(URL_LOCAL) == identidade_do_banco(URL_LOCAL)
        assert identidade_do_banco(URL_LOCAL) != identidade_do_banco(URL_PROD)


class TestGateEmHomologacao:
    @pytest.fixture(autouse=True)
    def _em_homologacao(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", URL_HML)

    def test_luiz_sincroniza(self):
        assert sync_liberado_para(LUIZ) is True

    @pytest.mark.parametrize(
        "email",
        [LUIZ.upper(), f"  {LUIZ}  ", "LFernandoOliveira@Outlook.com"],
    )
    def test_email_do_luiz_e_reconhecido_com_caixa_e_espaco_diferentes(self, email):
        """O e-mail chega do banco/Supabase sem garantia de normalização."""
        assert sync_liberado_para(email) is True

    def test_outra_conta_nao_sincroniza(self):
        assert sync_liberado_para("relacionamento@marketdash.com.br") is False

    def test_conta_sem_email_nao_sincroniza(self):
        """Integração órfã (usuário apagado) não passa pelo gate."""
        assert sync_liberado_para(None) is False
        assert sync_liberado_para("") is False


class TestForaDeHomologacao:
    def test_producao_libera_todo_mundo(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", URL_PROD)
        assert sync_liberado_para("qualquer@aluna.com") is True
        assert sync_liberado_para(None) is True

    def test_dev_local_libera_todo_mundo(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", URL_LOCAL)
        assert sync_liberado_para("qualquer@aluna.com") is True


class TestFilaDoCeleryNaoMudou:
    """`_fila_do_banco()` passou a usar `identidade_do_banco()`.

    Se o nome da fila mudar, o worker em produção fica escutando uma fila que
    ninguém mais alimenta — sync para de rodar sem erro nenhum.
    """

    @pytest.mark.parametrize(
        "url,esperado",
        [
            (URL_PROD, f"marketdash-{REF_PRODUCAO}"),
            (URL_HML, f"marketdash-{REF_HOMOLOGACAO}"),
            (URL_HML_DIRETA, f"marketdash-{REF_HOMOLOGACAO}"),
        ],
    )
    def test_nome_da_fila_continua_derivado_da_ref(self, monkeypatch, url, esperado):
        monkeypatch.setattr(settings, "DATABASE_URL", url)
        from app.tasks.celery_app import _fila_do_banco

        assert _fila_do_banco() == esperado

    def test_banco_sem_ref_cai_no_hash_de_12_digitos(self, monkeypatch):
        monkeypatch.setattr(settings, "DATABASE_URL", URL_LOCAL)
        from app.tasks.celery_app import _fila_do_banco

        fila = _fila_do_banco()
        assert fila.startswith("marketdash-")
        assert len(fila.removeprefix("marketdash-")) == 12


def test_lista_de_liberados_tem_o_luiz():
    """Lista vazia desligaria o gate em silêncio (todo mundo bloqueado)."""
    assert LUIZ in EMAILS_LIBERADOS_EM_HOMOLOGACAO
