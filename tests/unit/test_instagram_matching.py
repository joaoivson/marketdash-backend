"""Testes de aceite §9 — Matching.

Tabela exata do spec. A regra é: normalizar (minúsculas, sem acento, sem emoji,
pontuação virando espaço) e casar por SUBSTRING. Substring é o que faz 'QUERO'
pegar 'queroo' sem pegar 'queria'.
"""

import pytest

from app.utils.text_normalize import comentario_casa, normalizar_comentario


def _config(*palavras: str) -> list[str]:
    """Simula o que o service grava: palavras já normalizadas."""
    return [normalizar_comentario(p) for p in palavras]


class TestTabelaDeAceite:
    @pytest.mark.parametrize(
        "configurada,comentario,esperado",
        [
            (("QUERO",), "quero", True),
            (("QUERO",), "QUERO", True),
            (("QUERO",), "Eu quero esse!!", True),
            (("QUERO",), "Quéro", True),
            (("QUERO",), "queroo", True),
            (("QUERO",), "queria", False),
            (("QUERO", "LINK"), "manda o link", True),
        ],
    )
    def test_tabela_do_spec(self, configurada, comentario, esperado):
        assert comentario_casa(comentario, _config(*configurada)) is esperado

    def test_qro_nao_casa_com_quero(self):
        """Abreviação não é substring — e não deve virar match por 'parecido'."""
        assert comentario_casa("qro", _config("QUERO")) is False


class TestNormalizacao:
    def test_emoji_nao_gruda_na_palavra(self):
        assert normalizar_comentario("QUERO🙋‍♀️") == "quero"

    def test_pontuacao_vira_espaco_e_nao_some(self):
        """Colapsar, não remover.

        Se a pontuação e o espaço sumissem, 'eu li nkkk' viraria 'eulinkkk', que
        contém 'link' — e a automação da palavra LINK dispararia num comentário
        que não tem nada a ver.
        """
        assert normalizar_comentario("eu li nkkk") == "eu li nkkk"
        assert comentario_casa("eu li nkkk", _config("LINK")) is False

    def test_espacos_multiplos_colapsam(self):
        assert normalizar_comentario("eu    quero   isso") == "eu quero isso"

    def test_expressao_com_espaco_casa(self):
        assert comentario_casa("eu quero esse produto", _config("EU QUERO")) is True

    def test_comentario_vazio_nunca_casa(self):
        assert comentario_casa("", _config("QUERO")) is False
        assert comentario_casa("   ", _config("QUERO")) is False
        assert comentario_casa("🙂", _config("QUERO")) is False


class TestGatilhoQualquerPalavra:
    def test_qualquer_comentario_dispara(self):
        from app.models.instagram_automation import TRIGGER_QUALQUER, InstagramAutomation
        from app.services.instagram_comment_pipeline import automacao_dispara

        automacao = InstagramAutomation(
            nome="tudo", trigger_tipo=TRIGGER_QUALQUER, palavras=[], dm_texto="oi"
        )
        assert automacao_dispara(automacao, "lindo demais") is True
        assert automacao_dispara(automacao, "") is True
