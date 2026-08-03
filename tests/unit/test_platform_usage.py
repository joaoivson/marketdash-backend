"""Regras da aba "Uso da plataforma" e classificação de erros de sync."""
from app.services.platform_usage_service import (
    capitalizar_nome,
    nome_da_tela,
    rota_excluida,
)
from app.services.sync_monitoring_service import classificar_erro


# --- exclusão de rotas (vale pra TODOS os números da aba) ------------------------


def test_exclui_admin_login_raiz_e_paginas_publicas():
    for rota in ("/admin", "/admin/clientes", "/ADMIN/uso", "/login", "/", "",
                 "/l/abc123", "/c/minha-pagina"):
        assert rota_excluida(rota) is True, rota


def test_nao_exclui_telas_do_produto():
    for rota in ("/dashboard", "/dashboard/campanhas", "/dashboard/upload-cliques"):
        assert rota_excluida(rota) is False, rota


def test_rota_nula_e_excluida():
    assert rota_excluida(None) is True


# --- nomes amigáveis de tela ----------------------------------------------------


def test_nomes_amigaveis():
    assert nome_da_tela("/dashboard/campanhas") == "Campanhas"
    assert nome_da_tela("/dashboard/upload-cliques") == "Upload Cliques"
    assert nome_da_tela("/dashboard") == "Dashboard"
    assert nome_da_tela("/dashboard/investimentos") == "Investimentos"


def test_subrota_cai_na_tela_pai():
    assert nome_da_tela("/dashboard/campanhas/42") == "Campanhas"


def test_upload_cliques_nao_e_confundido_com_upload():
    """Prefixo mais longo primeiro — senão /upload engoliria /upload-cliques."""
    assert nome_da_tela("/dashboard/upload-cliques") == "Upload Cliques"
    assert nome_da_tela("/dashboard/upload") == "Upload"


def test_rota_desconhecida_nao_quebra():
    assert nome_da_tela("/dashboard/coisa-nova") == "Coisa Nova"


# --- capitalização de nomes -----------------------------------------------------


def test_capitaliza_preservando_particulas():
    assert capitalizar_nome("rubiane de melo carvalho") == "Rubiane de Melo Carvalho"


def test_capitaliza_nome_todo_maiusculo():
    assert capitalizar_nome("MARIA DOS SANTOS") == "Maria dos Santos"


def test_capitaliza_primeira_particula_vira_maiuscula():
    assert capitalizar_nome("de souza") == "De Souza"


def test_capitalizar_nome_vazio_ou_nulo():
    assert capitalizar_nome(None) is None
    assert capitalizar_nome("") == ""


# --- classificação de erros de sync ---------------------------------------------


def test_classifica_codigos_da_shopee():
    casos = {
        "10000": "Instabilidade Shopee",
        "10010": "Erro de query",
        "10020": "Autenticação/credencial",
        "10030": "Rate limit",
        "11000": "Negócio/parâmetros",
    }
    for codigo, rotulo in casos.items():
        msg = f"400: Erros GraphQL Shopee: [{{'message': 'error [{codigo}]: X'}}]"
        r = classificar_erro(msg)
        assert r["codigo"] == codigo, msg
        assert r["motivo"] == rotulo


def test_credencial_traduzida_pelo_nosso_codigo_ainda_classifica():
    msg = "Credencial Shopee inválida (código 10020). É preciso reconectar a conta."
    assert classificar_erro(msg)["motivo"] == "Autenticação/credencial"


def test_erro_python_e_marcado_como_interno():
    r = classificar_erro("cannot unpack non-iterable int object")
    assert r["motivo"] == "Erro interno (nosso código)"
    assert r["codigo"] is None


def test_codigo_desconhecido_cai_em_outros():
    r = classificar_erro("error [19999]: algo novo")
    assert r["codigo"] == "19999"
    assert r["motivo"] == "Outros"


def test_mensagem_vazia_nao_quebra():
    assert classificar_erro(None)["motivo"] == "Outros"
    assert classificar_erro("")["motivo"] == "Outros"
