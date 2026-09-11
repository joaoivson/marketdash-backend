"""Ledger de entrega do webhook (migration 083).

O que estes testes protegem é a resposta a uma pergunta que em 11/09/2026 não
tinha resposta: "a Meta entregou o comentário?". Antes do ledger, um comentário
que chegava e era descartado por não ter automação cobrindo o post deixava o
banco EXATAMENTE igual a um comentário que nunca chegou.

A regra que não pode quebrar: o ledger é diagnóstico e nunca pode mudar o
desfecho do comentário. Banco fora do ar → a linha não nasce, mas a task é
enfileirada do mesmo jeito.
"""

import asyncio
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


class _RequestFake:
    """Só o que `receber_webhook` usa: o corpo cru e o ip do cliente."""

    def __init__(self, corpo: bytes):
        self._corpo = corpo
        self.client = type("C", (), {"host": "1.2.3.4"})()

    async def body(self) -> bytes:
        return self._corpo


def _assinar(corpo: bytes) -> str:
    return "sha256=" + hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()


def _chamar(payload: dict, monkeypatch, gravar=None):
    """Executa o webhook capturando o ledger e as chamadas de fila."""
    corpo = json.dumps(payload).encode()

    gravadas: list[list[dict]] = []
    enfileiradas: list[tuple] = []

    def _gravar(linhas):
        gravadas.append(linhas)
        return (gravar or (lambda l: list(range(100, 100 + len(l)))))(linhas)

    monkeypatch.setattr(webhook, "_gravar_entregas", _gravar)
    monkeypatch.setattr(
        webhook, "_enfileirar",
        lambda ig, valor, entrega_id=None: enfileiradas.append(("comentario", ig, valor, entrega_id)),
    )
    monkeypatch.setattr(
        webhook, "_enfileirar_story_reply",
        lambda ig, evt, entrega_id=None: enfileiradas.append(("story", ig, evt, entrega_id)),
    )

    resposta = asyncio.run(webhook.receber_webhook(_RequestFake(corpo), _assinar(corpo)))
    return resposta, (gravadas[0] if gravadas else []), enfileiradas


def _payload_comentario(comment_id="c1", media_id="m1"):
    return {
        "object": "instagram",
        "entry": [
            {
                "id": "1784100",
                "time": 1757000000,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": comment_id,
                            "text": "Quero",
                            "from": {"id": "999", "username": "fulana"},
                            "media": {"id": media_id},
                        },
                    }
                ],
            }
        ],
    }


class TestLedgerDeEntrega:
    def test_comentario_grava_uma_linha_e_passa_o_id_para_a_task(self, monkeypatch):
        resposta, linhas, enfileiradas = _chamar(_payload_comentario(), monkeypatch)

        assert resposta["enfileirados"] == 1
        assert len(linhas) == 1
        assert linhas[0]["tipo"] == "comentario"
        assert linhas[0]["item_id"] == "c1"
        assert linhas[0]["media_id"] == "m1"
        assert linhas[0]["ig_user_id"] == "1784100"
        # O id da linha tem que chegar na task — é ele que liga o que CHEGOU ao
        # que o pipeline decidiu.
        assert enfileiradas[0][3] == 100

    def test_ordem_dos_ids_acompanha_a_ordem_dos_itens(self, monkeypatch):
        payload = _payload_comentario("c1", "m1")
        payload["entry"][0]["changes"].append(
            {
                "field": "comments",
                "value": {"id": "c2", "text": "Eu quero", "media": {"id": "m2"}},
            }
        )
        _, linhas, enfileiradas = _chamar(payload, monkeypatch)

        assert [l["item_id"] for l in linhas] == ["c1", "c2"]
        assert [e[3] for e in enfileiradas] == [100, 101]

    def test_dm_comum_vira_uma_linha_agregada_e_nao_enfileira(self, monkeypatch):
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "1784100",
                    "messaging": [
                        {"sender": {"id": "777"}, "message": {"mid": "m-1", "text": "oi"}},
                        {"sender": {"id": "778"}, "message": {"mid": "m-2", "text": "tudo bem?"}},
                    ],
                }
            ],
        }
        resposta, linhas, enfileiradas = _chamar(payload, monkeypatch)

        assert resposta["enfileirados"] == 0
        assert enfileiradas == []
        # Uma linha só, agregada: guardar metadado de cada DM privada seria
        # guardar mais do que o diagnóstico precisa.
        assert len(linhas) == 1
        assert linhas[0]["desfecho"] == "fora_do_escopo"
        assert "2 item" in linhas[0]["detalhe"]

    def test_reply_de_story_entra_como_item(self, monkeypatch):
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "1784100",
                    "messaging": [
                        {
                            "sender": {"id": "777"},
                            "timestamp": 1757000000000,
                            "message": {
                                "mid": "mid-real",
                                "text": "Quero",
                                "reply_to": {"story": {"id": "s1"}},
                            },
                        }
                    ],
                }
            ],
        }
        resposta, linhas, enfileiradas = _chamar(payload, monkeypatch)

        assert resposta["enfileirados"] == 1
        assert linhas[0]["tipo"] == "story_reply"
        assert linhas[0]["item_id"] == "mid-real"
        assert enfileiradas[0][0] == "story"
        assert enfileiradas[0][3] == 100

    def test_banco_fora_do_ar_nao_impede_o_enfileiramento(self, monkeypatch):
        """A regra de ouro: o ledger é diagnóstico, não é caminho crítico."""
        _, _, enfileiradas = _chamar(
            _payload_comentario(), monkeypatch, gravar=lambda linhas: [None] * len(linhas)
        )

        assert len(enfileiradas) == 1
        assert enfileiradas[0][3] is None

    def test_assinatura_invalida_deixa_rastro_antes_do_403(self, monkeypatch):
        from fastapi import HTTPException

        gravadas = []
        monkeypatch.setattr(
            webhook, "_gravar_entregas", lambda linhas: gravadas.append(linhas) or [1]
        )
        corpo = json.dumps(_payload_comentario()).encode()

        with pytest.raises(HTTPException) as exc:
            asyncio.run(webhook.receber_webhook(_RequestFake(corpo), "sha256=forjado"))

        assert exc.value.status_code == 403
        # É o rastro que separa "a Meta não entregou" de "entregou e recusamos".
        assert gravadas[0][0]["assinatura_ok"] is False
        assert gravadas[0][0]["desfecho"] == "assinatura_invalida"


class TestCarimboDeDesfecho:
    def test_sem_id_e_no_op(self):
        webhook.marcar_desfecho_entrega(None, "enviado")  # não pode levantar

    def test_falha_ao_carimbar_nao_propaga(self, monkeypatch):
        import app.db.session as sessao

        monkeypatch.setattr(
            sessao, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("banco fora"))
        )
        webhook.marcar_desfecho_entrega(1, "enviado")  # engole e segue
