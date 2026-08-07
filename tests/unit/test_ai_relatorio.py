"""
Forma mínima do relatório — a checagem que fica entre a IA e o débito.

O caso que importa: `completar_json` aceita qualquer JSON válido. Se a IA
devolver um objeto sem seções, a tela renderiza quase em branco (ela tem
blindagem contra campo faltando) e os 10 créditos já foram cobrados.
"""
import pytest

from app.services.ai_relatorio import RelatorioInvalido, validar_relatorio

COMPLETO = {
    "resumo_executivo": "Mês saudável, com a campanha X puxando o resultado.",
    "escalar": [{"nome": "X", "motivo": "ROAS 2,4", "acao": "subir verba"}],
    "pausar": [],
    "observar": [],
    "detalhamento": [{"nome": "X", "diagnostico": "vai bem", "custo": ""}],
    "numeros": {"destaque": "R$ 1.200,00", "atencao": ""},
    "proximos_passos": ["Revisar a X."],
    "perguntas_sugeridas": ["Por que a X vai bem?"],
}


def test_relatorio_completo_passa_intacto():
    r = validar_relatorio(COMPLETO)
    assert r["resumo_executivo"] == COMPLETO["resumo_executivo"]
    assert r["escalar"] == COMPLETO["escalar"]
    assert r["numeros"]["destaque"] == "R$ 1.200,00"


@pytest.mark.parametrize("bruto", [
    None, [], "texto", 42,
    {},
    {"escalar": [{"nome": "X"}]},                       # sem resumo
    {"resumo_executivo": "", "escalar": [{"nome": "X"}]},
    {"resumo_executivo": "   ", "escalar": [{"nome": "X"}]},
    {"resumo_executivo": 7, "escalar": [{"nome": "X"}]},
])
def test_sem_resumo_utilizavel_e_invalido(bruto):
    with pytest.raises(RelatorioInvalido):
        validar_relatorio(bruto)


def test_resumo_sozinho_nao_e_relatorio():
    # É o caso que gerava tela em branco cobrada.
    with pytest.raises(RelatorioInvalido):
        validar_relatorio({"resumo_executivo": "Tudo certo."})


def test_secoes_vazias_com_perguntas_sugeridas_ainda_e_invalido():
    # perguntas_sugeridas alimenta o chat, não é conteúdo do relatório.
    with pytest.raises(RelatorioInvalido):
        validar_relatorio({
            "resumo_executivo": "Tudo certo.",
            "escalar": [], "pausar": [], "observar": [],
            "detalhamento": [], "proximos_passos": [],
            "perguntas_sugeridas": ["E daí?"],
        })


def test_uma_secao_qualquer_com_conteudo_basta():
    for secao in ("escalar", "pausar", "observar", "detalhamento", "proximos_passos"):
        r = validar_relatorio({"resumo_executivo": "Resumo.", secao: ["algo"]})
        assert r[secao] == ["algo"]


def test_campos_de_tipo_errado_viram_vazios_em_vez_de_quebrar_a_tela():
    r = validar_relatorio({
        "resumo_executivo": "  Resumo com espaço.  ",
        "escalar": "deveria ser lista",
        "pausar": None,
        "observar": {"nome": "X"},
        "proximos_passos": ["passo"],
        "numeros": "deveria ser objeto",
    })
    assert r["resumo_executivo"] == "Resumo com espaço."
    assert r["escalar"] == [] and r["pausar"] == [] and r["observar"] == []
    assert r["numeros"] == {}
    assert r["perguntas_sugeridas"] == []   # ausente vira lista, não KeyError


def test_nao_muda_o_dicionario_recebido():
    original = {"resumo_executivo": "Resumo.", "escalar": ["a"]}
    copia = dict(original)
    validar_relatorio(original)
    assert original == copia
