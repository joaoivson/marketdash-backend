"""Precedência entre automações que cobrem o mesmo comentário.

O cenário que motiva: a conta tem uma automação por POST (link do produto
daquele post) e cria também uma que vale para QUALQUER post com a palavra
"quero" — porque 27% das pessoas comentam "quero" em vez da palavra do post.

Sem ordem explícita, a "qualquer post" pode responder primeiro e mandar o link
errado para a cliente. E o pior: a ordem vinha do Postgres sem `ORDER BY`, então
o erro seria intermitente e não reproduziria.
"""

import pytest

from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    ESCOPO_POST_ESPECIFICO,
    ESCOPO_QUALQUER,
    ESCOPO_STORY_ESPECIFICO,
    ESCOPO_STORY_QUALQUER,
    TRIGGER_PALAVRAS,
    InstagramAutomation,
)
from app.services.instagram_comment_pipeline import (
    automacao_dispara,
    ordenar_por_especificidade,
)


def _automacao(id_: int, escopo: str, palavras: list[str], media_id=None, nome="a"):
    return InstagramAutomation(
        id=id_,
        nome=nome,
        escopo=escopo,
        media_id=media_id,
        trigger_tipo=TRIGGER_PALAVRAS,
        palavras=palavras,
        status=AUTOMACAO_ATIVA,
    )


class TestEspecificidade:
    def test_post_escolhido_e_mais_especifico_que_qualquer(self):
        assert _automacao(1, ESCOPO_POST_ESPECIFICO, ["quero"], "m1").especificidade == 1
        assert _automacao(2, ESCOPO_QUALQUER, ["quero"]).especificidade == 0

    def test_story_escolhido_e_mais_especifico_que_story_qualquer(self):
        assert _automacao(1, ESCOPO_STORY_ESPECIFICO, ["quero"], "s1").especificidade == 1
        assert _automacao(2, ESCOPO_STORY_QUALQUER, ["quero"]).especificidade == 0


class TestOrdenacao:
    def test_a_do_post_ganha_da_de_qualquer_post(self):
        """O caso que manda o link errado para a cliente se quebrar."""
        coringa = _automacao(1, ESCOPO_QUALQUER, ["quero"], nome="coringa")
        do_post = _automacao(9, ESCOPO_POST_ESPECIFICO, ["quero"], "m1", nome="algodão")

        # A coringa foi criada ANTES (id menor) — sem a regra, ela vinha primeiro.
        ordenadas = ordenar_por_especificidade([coringa, do_post])

        assert ordenadas[0].nome == "algodão"

    def test_ordem_independe_de_como_o_banco_devolveu(self):
        coringa = _automacao(1, ESCOPO_QUALQUER, ["quero"], nome="coringa")
        do_post = _automacao(9, ESCOPO_POST_ESPECIFICO, ["quero"], "m1", nome="algodão")

        assert [a.nome for a in ordenar_por_especificidade([coringa, do_post])] == \
               [a.nome for a in ordenar_por_especificidade([do_post, coringa])]

    def test_entre_iguais_vence_a_mais_antiga_sempre(self):
        """Empate real precisa de desfecho FIXO — intermitente não reproduz."""
        nova = _automacao(20, ESCOPO_QUALQUER, ["quero"], nome="nova")
        antiga = _automacao(3, ESCOPO_QUALQUER, ["quero"], nome="antiga")

        assert ordenar_por_especificidade([nova, antiga])[0].nome == "antiga"
        assert ordenar_por_especificidade([antiga, nova])[0].nome == "antiga"

    def test_lista_vazia_nao_quebra(self):
        assert ordenar_por_especificidade([]) == []


class TestMatchingComPrecedencia:
    """A escolha final do pipeline: primeira da lista ordenada que casa."""

    def _escolher(self, automacoes, texto):
        return next(
            (a for a in ordenar_por_especificidade(automacoes) if automacao_dispara(a, texto)),
            None,
        )

    def test_quero_no_post_com_automacao_usa_a_do_post(self):
        coringa = _automacao(1, ESCOPO_QUALQUER, ["quero"], nome="coringa")
        do_post = _automacao(9, ESCOPO_POST_ESPECIFICO, ["quero", "algodao"], "m1", nome="algodão")

        assert self._escolher([coringa, do_post], "Quero").nome == "algodão"

    def test_quero_em_post_sem_automacao_cai_na_coringa(self):
        """O post sem automação específica: a coringa é o que salva o comentário."""
        coringa = _automacao(1, ESCOPO_QUALQUER, ["quero"], nome="coringa")

        assert self._escolher([coringa], "Quero").nome == "coringa"

    def test_palavra_do_post_nao_e_roubada_pela_coringa(self):
        """66% das pessoas comentam a palavra do post — esse é o caminho comum."""
        coringa = _automacao(1, ESCOPO_QUALQUER, ["quero"], nome="coringa")
        do_post = _automacao(9, ESCOPO_POST_ESPECIFICO, ["algodao"], "m1", nome="algodão")

        assert self._escolher([coringa, do_post], "Algodão").nome == "algodão"

    def test_sem_nenhuma_que_case_devolve_none(self):
        do_post = _automacao(9, ESCOPO_POST_ESPECIFICO, ["algodao"], "m1")

        assert self._escolher([do_post], "que lindo!") is None
