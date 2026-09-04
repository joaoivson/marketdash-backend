"""
Gate de módulo em beta — o que decide se WhatsApp, Parâmetros e Campanhas
aparecem para uma conta.

Substitui o `isProductionHost()` do frontend, que era build-time: liberar o
módulo para uma conta de teste em produção exigia rebuild + redeploy. O default
importa mais que o resto: **módulo ausente do arquivo é FECHADO**. Se um erro de
digitação no JSON abrisse o módulo, o disparo em grupo apareceria para a base
inteira sem ninguém pedir.
"""
import json

import pytest

import app.core.feature_flags as ff


@pytest.fixture(autouse=True)
def _limpa_estado(monkeypatch):
    """As flags são cacheadas em módulo — teste que sujasse o cache
    contaminaria os seguintes (e o resto da suíte)."""
    monkeypatch.delenv("MODULOS_BETA", raising=False)
    monkeypatch.setattr(ff, "_config", None)
    yield
    ff._config = None


def _config(dados: dict, monkeypatch):
    monkeypatch.setattr(ff, "_config", dados)


def test_modulo_ausente_do_arquivo_fica_fechado(monkeypatch):
    _config({"payment_provider": "kiwify"}, monkeypatch)
    assert ff.modulos_beta_liberados(plano="max", email="a@b.com") == set()


def test_liberado_true_vale_para_todo_mundo(monkeypatch):
    _config({"modulos_beta": {"grupos_whatsapp": {"liberado": True}}}, monkeypatch)
    assert ff.modulos_beta_liberados(plano="essencial", email="a@b.com") == {
        "grupos_whatsapp"
    }


def test_liberacao_por_plano_nao_vaza_para_outro_plano(monkeypatch):
    _config(
        {"modulos_beta": {"grupos_whatsapp": {"liberado": False, "planos": ["max"]}}},
        monkeypatch,
    )
    assert ff.modulos_beta_liberados(plano="max", email="a@b.com") == {"grupos_whatsapp"}
    assert ff.modulos_beta_liberados(plano="pro", email="a@b.com") == set()


def test_liberacao_nominal_por_email_ignora_caixa(monkeypatch):
    _config(
        {
            "modulos_beta": {
                "grupos_whatsapp": {
                    "liberado": False,
                    "emails": ["Relacionamento@MarketDash.com.br"],
                }
            }
        },
        monkeypatch,
    )
    liberados = ff.modulos_beta_liberados(
        plano="pro", email="relacionamento@marketdash.com.br"
    )
    assert liberados == {"grupos_whatsapp"}
    assert ff.modulos_beta_liberados(plano="pro", email="outra@aluna.com") == set()


def test_env_vazia_fecha_tudo_e_env_preenchida_manda(monkeypatch):
    """`MODULOS_BETA` é a alavanca de produção: Coolify + restart, sem rebuild.

    Definida e VAZIA fecha tudo — diferente de não definida, que cai no arquivo.
    Sem essa distinção não haveria como recolher um beta pelo ambiente.
    """
    _config({"modulos_beta": {"grupos_whatsapp": {"liberado": True}}}, monkeypatch)

    monkeypatch.setenv("MODULOS_BETA", "")
    assert ff.modulos_beta_liberados(plano="max") == set()

    monkeypatch.setenv("MODULOS_BETA", "grupos_whatsapp, outro_modulo")
    assert ff.modulos_beta_liberados(plano="essencial") == {
        "grupos_whatsapp",
        "outro_modulo",
    }


def test_config_malformada_nao_derruba_nem_abre(monkeypatch):
    """JSON com `modulos_beta` do tipo errado não pode virar exceção no boot
    de toda sessão (o gate roda no contexto de plano) — nem abrir o módulo."""
    for lixo in ("texto", 42, ["grupos_whatsapp"]):
        _config({"modulos_beta": lixo}, monkeypatch)
        assert ff.modulos_beta_liberados(plano="max") == set()


def test_arquivo_versionado_tem_o_modulo_declarado():
    """O arquivo real precisa declarar o módulo — sem a chave, o gate fecha
    tudo em silêncio e o WhatsApp some de homologação sem ninguém pedir."""
    ff._config = None
    config = ff._load_config()
    assert "modulos_beta" in config
    assert ff.MODULO_GRUPOS_WHATSAPP in config["modulos_beta"]
    # Sanidade do JSON: precisa ser serializável e do formato que o gate lê.
    json.dumps(config["modulos_beta"])
