"""Rodada 2: o direct sai como template `button`, com fallback para texto puro.

O fallback não é decoração: se a Meta recusar o template em produção, é o que
mantém a automação funcionando enquanto o formato volta pelo interruptor
(INSTAGRAM_DM_FORMATO=texto).
"""
import importlib
import os

import pytest

from app.services.instagram_login_client import montar_mensagem_dm


class TestMontagemDaMensagem:
    def test_com_link_e_botao_vira_template(self):
        m = montar_mensagem_dm("Oi, aqui está", "https://mkt.com/l/x", "Pegar o link")
        payload = m["attachment"]["payload"]
        assert m["attachment"]["type"] == "template"
        assert payload["template_type"] == "button"
        assert payload["text"] == "Oi, aqui está"
        assert payload["buttons"] == [
            {"type": "web_url", "url": "https://mkt.com/l/x", "title": "Pegar o link"}
        ]
        assert "text" not in m, "template não pode ir junto com `text` solto"

    def test_sem_link_continua_texto_puro(self):
        assert montar_mensagem_dm("só o texto") == {"text": "só o texto"}

    def test_link_sem_titulo_de_botao_cai_no_texto_com_link_no_fim(self):
        """Fallback: melhor o link no corpo do que uma mensagem sem o link."""
        m = montar_mensagem_dm("Oi", "https://mkt.com/l/x", None)
        assert m == {"text": "Oi\n\nhttps://mkt.com/l/x"}

    def test_titulo_do_botao_e_cortado_em_20(self):
        """A Meta recusa título longo; cortar é melhor que a mensagem inteira falhar."""
        m = montar_mensagem_dm("Oi", "https://mkt.com/l/x", "um texto de botão bem longo demais")
        assert len(m["attachment"]["payload"]["buttons"][0]["title"]) == 20

    def test_emoji_no_texto_atravessa_intacto(self):
        m = montar_mensagem_dm("Oii 💙✨", "https://mkt.com/l/x", "Pegar 🔗")
        assert m["attachment"]["payload"]["text"] == "Oii 💙✨"
        assert m["attachment"]["payload"]["buttons"][0]["title"] == "Pegar 🔗"


class TestInterruptorDeFormato:
    """Voltar ao formato antigo tem que ser env var — sem rebuild de imagem."""

    def _flags(self, valor=None):
        if valor is None:
            os.environ.pop("INSTAGRAM_DM_FORMATO", None)
        else:
            os.environ["INSTAGRAM_DM_FORMATO"] = valor
        import app.core.feature_flags as ff

        return importlib.reload(ff)

    def teardown_method(self):
        os.environ.pop("INSTAGRAM_DM_FORMATO", None)
        import app.core.feature_flags as ff

        importlib.reload(ff)

    def test_default_e_botao(self):
        assert self._flags().dm_com_botao() is True

    def test_env_texto_desliga_o_botao(self):
        assert self._flags("texto").dm_com_botao() is False

    def test_valor_invalido_nao_desliga_o_botao(self):
        """Digitar errado no Coolify não pode virar mudança silenciosa de formato."""
        assert self._flags("BOTAOO").dm_com_botao() is True

    @pytest.mark.parametrize("valor", ["texto", "TEXTO", " Texto "])
    def test_env_aceita_caixa_e_espaco(self, valor):
        assert self._flags(valor).instagram_dm_formato() == "texto"
