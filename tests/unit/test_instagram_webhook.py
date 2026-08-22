"""Assinatura do webhook e handshake — a porta de entrada precisa ser fechada."""

import base64
import hashlib
import hmac
import json

import pytest

from app.api.webhooks import instagram as webhook
from app.core.config import settings

SEGREDO = "segredo-de-teste"


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(settings, "INSTAGRAM_APP_SECRET", SEGREDO, raising=False)
    monkeypatch.setattr(settings, "INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "verif-123", raising=False)


def _assinar(corpo: bytes) -> str:
    return "sha256=" + hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()


class TestAssinatura:
    def test_assinatura_correta_passa(self):
        corpo = json.dumps({"object": "instagram"}).encode()
        assert webhook.verificar_assinatura(corpo, _assinar(corpo)) is True

    def test_corpo_adulterado_e_recusado(self):
        corpo = json.dumps({"object": "instagram"}).encode()
        assinatura = _assinar(corpo)
        assert webhook.verificar_assinatura(corpo + b" ", assinatura) is False

    def test_sem_header_e_recusado(self):
        assert webhook.verificar_assinatura(b"{}", None) is False

    def test_prefixo_errado_e_recusado(self):
        corpo = b"{}"
        bruto = hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()
        assert webhook.verificar_assinatura(corpo, bruto) is False
        assert webhook.verificar_assinatura(corpo, "sha1=" + bruto) is False

    def test_sem_app_secret_recusa_tudo(self, monkeypatch):
        """Servidor mal configurado tem que fechar a porta, não abrir."""
        monkeypatch.setattr(settings, "INSTAGRAM_APP_SECRET", None, raising=False)
        corpo = b"{}"
        assert webhook.verificar_assinatura(corpo, _assinar(corpo)) is False


class TestHandshake:
    def test_token_correto_devolve_o_challenge_em_texto_puro(self):
        resposta = webhook.verificar_webhook(
            hub_mode="subscribe", hub_challenge="1234", hub_verify_token="verif-123"
        )
        assert resposta.body == b"1234"
        assert resposta.media_type == "text/plain"

    def test_token_errado_da_403(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            webhook.verificar_webhook(
                hub_mode="subscribe", hub_challenge="1234", hub_verify_token="errado"
            )
        assert exc.value.status_code == 403


class TestSignedRequest:
    def _montar(self, payload: dict) -> str:
        bruto = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        assinatura = hmac.new(SEGREDO.encode(), bruto.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(assinatura).decode().rstrip("=") + "." + bruto

    def test_decodifica_payload_valido(self):
        assinado = self._montar({"user_id": "17841400000000000"})
        assert webhook._parse_signed_request(assinado) == {"user_id": "17841400000000000"}

    def test_assinatura_forjada_e_recusada(self):
        assinado = self._montar({"user_id": "1"})
        forjado = "AAAA." + assinado.split(".", 1)[1]
        assert webhook._parse_signed_request(forjado) is None

    def test_formato_invalido_nao_quebra(self):
        assert webhook._parse_signed_request("") is None
        assert webhook._parse_signed_request("sem-ponto") is None
