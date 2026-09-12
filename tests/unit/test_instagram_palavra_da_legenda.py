"""Extração da palavra que a legenda manda comentar.

Serve para pré-preencher a automação. A assimetria que governa o desenho:

  - NÃO sugerir nada custa a aluna digitar a palavra;
  - sugerir a palavra ERRADA cria uma automação que dispara no gatilho errado e
    manda o link errado para a cliente dela.

Os dois erros não têm o mesmo preço, então na dúvida a função devolve None.

As legendas abaixo são transcrições de publicações REAIS da conta
@promosdabeatrizz_ (283 publicações varridas em 11/09/2026).
"""

import pytest

from app.utils.text_normalize import palavra_pedida_na_legenda as extrair


class TestFormatosReais:
    @pytest.mark.parametrize(
        "legenda, esperado",
        [
            ('✨Comente " ALGODÃO " para receber o link agora! E siga @promosdabeatrizz', "ALGODÃO"),
            ('✨Comente " CAIXA DE FERRAMENTAS " para receber o link agora!', "CAIXA DE FERRAMENTAS"),
            ("✨Comente “PERFUME” para receber o link", "PERFUME"),
            ("Comente MAMADEIRA para receber o link", "MAMADEIRA"),
            ('Comenta "BASE COREANA" que eu te mando', "BASE COREANA"),
            ('✨Comente " VARIZES " para receber o link agora!', "VARIZES"),
            ('✨Comente " CÓLICA " para receber o link agora!', "CÓLICA"),
            ('✨Comente "AXILIA" para receber o link agora!', "AXILIA"),
        ],
    )
    def test_extrai_a_palavra(self, legenda, esperado):
        assert extrair(legenda) == esperado

    def test_espaco_interno_e_preservado_e_o_das_bordas_nao(self):
        assert extrair('Comente "  BASE   COREANA  " para receber') == "BASE COREANA"


class TestRecusaQuandoNaoDaParaAfirmar:
    """Cada um destes, se extraísse, viraria link errado para a cliente."""

    @pytest.mark.parametrize(
        "legenda",
        [
            None,
            "",
            "   ",
            "Olha que lindo esse conjunto, corre que tá acabando",
            "Chegou novidade na loja 🔥🔥",
        ],
    )
    def test_sem_pedido_na_legenda_nao_sugere(self, legenda):
        assert extrair(legenda) is None

    @pytest.mark.parametrize("generico", ["QUERO", "quero", "EU QUERO", "LINK", "aqui"])
    def test_palavra_generica_nao_vira_automacao_de_post(self, generico):
        """Legenda que pede "quero" não identifica ESTE post.

        27% das pessoas comentam "quero" em qualquer publicação. Virar
        palavra-chave de um post específico faria essa automação responder o
        comentário de qualquer outro — é caso de automação "qualquer post",
        criada de propósito, não de sugestão automática.
        """
        assert extrair(f'✨Comente " {generico} " para receber o link agora!') is None


class TestLimites:
    def test_palavra_longa_demais_nao_e_palavra_chave(self):
        frase = "ISSO AQUI TUDO QUE EU ESCREVI SEM PARAR PORQUE ESQUECI AS ASPAS MESMO"
        assert extrair(f"Comente {frase} para receber") is None

    def test_quebra_de_linha_entre_o_verbo_e_a_palavra_e_aceita(self):
        """A palavra em si nunca cruza linha — só o espaço até ela.

        Legenda quebrada assim existe e é um pedido legítimo; a palavra
        continua confinada a uma linha pelo próprio padrão.
        """
        assert extrair("Comente\nALGODÃO para receber") == "ALGODÃO"

    def test_sem_aspas_so_aceita_ate_duas_palavras(self):
        """Sem aspas quem delimita é a preposição, e ela pode estar longe."""
        assert extrair("Comente BASE COREANA para receber") == "BASE COREANA"
        assert extrair("Comente CAIXA DE FERRAMENTAS para receber") is None

    def test_com_aspas_aceita_ate_quatro(self):
        assert extrair('Comente "CAIXA DE FERRAMENTAS" para receber') == "CAIXA DE FERRAMENTAS"
