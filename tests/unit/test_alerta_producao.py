"""
Alerta de queda de produção por WhatsApp.

O teste que importa é o de **deduplicação**: a sonda roda a cada ~10 min e uma
queda de 3 h viraria 18 mensagens idênticas. Alerta repetido é alerta que se
aprende a silenciar — e aí a próxima queda de verdade passa batida.
"""
from app.services import alerta_producao_service as alerta


class RedisFalso:
    def __init__(self):
        self.dados = {}

    def get(self, k):
        return self.dados.get(k)

    def set(self, k, v, ex=None):
        self.dados[k] = v

    def delete(self, k):
        self.dados.pop(k, None)


class ClienteFalso:
    """WahaClient de mentira: registra o que seria enviado."""

    def __init__(self, falhar_para=()):
        self.enviadas = []
        self.falhar_para = falhar_para

    def configurado(self):
        return True

    def enviar_texto(self, chat_id, texto):
        if any(n in chat_id for n in self.falhar_para):
            from app.services.waha_client import ErroWhatsapp

            raise ErroWhatsapp("numero_invalido", chat_id)
        self.enviadas.append((chat_id, texto))
        return {"id": "x"}


def _montar(monkeypatch, cliente=None, redis=None, numeros="34998557753,34998937753"):
    cliente = cliente or ClienteFalso()
    redis = redis if redis is not None else RedisFalso()
    monkeypatch.setattr(alerta.settings, "ALERTA_WHATSAPP_NUMEROS", numeros)
    monkeypatch.setattr(alerta.settings, "ALERTA_WHATSAPP_SESSAO", "sessao-do-suporte")
    monkeypatch.setattr(alerta, "_cliente", lambda: cliente)
    monkeypatch.setattr(alerta, "_redis", lambda: redis)
    return cliente, redis


class TestDeduplicacao:
    def test_primeira_queda_avisa(self, monkeypatch):
        cliente, redis = _montar(monkeypatch)
        r = alerta.registrar("caiu", "API /health devolveu HTTP 000.")
        assert r["enviado"] is True
        assert len(cliente.enviadas) == 2          # os dois destinos
        assert redis.dados[alerta.CHAVE_INCIDENTE]  # incidente aberto

    def test_segunda_sonda_no_mesmo_incidente_fica_calada(self, monkeypatch):
        cliente, _ = _montar(monkeypatch)
        alerta.registrar("caiu", "x")
        r = alerta.registrar("caiu", "x")
        assert r["enviado"] is False
        assert "já avisado" in r["motivo"]
        assert len(cliente.enviadas) == 2          # continua 2, não 4

    def test_volta_avisa_uma_vez_e_fecha(self, monkeypatch):
        cliente, redis = _montar(monkeypatch)
        alerta.registrar("caiu", "x")
        r = alerta.registrar("voltou")
        assert r["enviado"] is True
        assert alerta.CHAVE_INCIDENTE not in redis.dados
        assert "normalizada" in cliente.enviadas[-1][1]
        assert alerta.registrar("voltou")["enviado"] is False  # a próxima é silêncio

    def test_caminho_feliz_nunca_manda_mensagem(self, monkeypatch):
        """A sonda passa a cada 10 min com produção sã — se isso custasse
        mensagem, o número seria silenciado no primeiro dia."""
        cliente, _ = _montar(monkeypatch)
        for _ in range(5):
            alerta.registrar("voltou")
        assert cliente.enviadas == []

    def test_incidente_fecha_mesmo_se_o_envio_falhar(self, monkeypatch):
        """Manter aberto faria a PRÓXIMA queda ficar muda — o pior desfecho."""
        cliente, redis = _montar(monkeypatch)
        alerta.registrar("caiu", "x")
        falho = ClienteFalso(falhar_para=("34",))
        monkeypatch.setattr(alerta, "_cliente", lambda: falho)
        # normalizar_numero prefixa 55; o filtro acima derruba os dois destinos
        alerta.registrar("voltou")
        assert alerta.CHAVE_INCIDENTE not in redis.dados


class TestEntrega:
    def test_falha_em_um_numero_nao_impede_o_outro(self, monkeypatch):
        # "8937753" casa com as DUAS formas desse número (13 e 12 dígitos), o
        # que simula um destino realmente inalcançável.
        cliente = ClienteFalso(falhar_para=("8937753",))
        _montar(monkeypatch, cliente=cliente)
        r = alerta.registrar("caiu", "x")
        assert r["enviado"] is True
        assert len(cliente.enviadas) == 1
        assert r["falhas"][0]["motivo"] == "numero_invalido"

    def test_numeros_viram_e164_com_55(self, monkeypatch):
        cliente, _ = _montar(monkeypatch, numeros=" 34998557753 , (34) 99893-7753 ")
        alerta.registrar("caiu", "x")
        assert [c for c, _ in cliente.enviadas] == [
            "5534998557753@c.us",
            "5534998937753@c.us",
        ]

    def test_cai_para_a_forma_de_12_digitos_quando_a_de_13_falha(self, monkeypatch):
        """O JID real de boa parte da base brasileira não tem o nono dígito —
        o primeiro teste real deste alerta falhou nos dois destinos por isso."""
        cliente = ClienteFalso(falhar_para=("5534998557753", "5534998937753"))
        _montar(monkeypatch, cliente=cliente)
        r = alerta.registrar("caiu", "x")
        assert r["enviado"] is True
        assert [c for c, _ in cliente.enviadas] == [
            "553498557753@c.us",
            "553498937753@c.us",
        ]

    def test_numero_invalido_no_env_nao_derruba_os_validos(self, monkeypatch):
        cliente, _ = _montar(monkeypatch, numeros="34998557753,abc")
        alerta.registrar("caiu", "x")
        assert len(cliente.enviadas) == 1


class TestDegradacao:
    def test_sem_redis_nao_envia(self, monkeypatch):
        """Sem dedup, a cada 10 min sai uma mensagem igual — pior que ficar
        calado, porque ensina a silenciar o número."""
        cliente, _ = _montar(monkeypatch, redis=None)
        monkeypatch.setattr(alerta, "_redis", lambda: None)
        r = alerta.registrar("caiu", "x")
        assert r["enviado"] is False
        assert cliente.enviadas == []

    def test_sem_numeros_configurados(self, monkeypatch):
        _montar(monkeypatch, numeros="")
        assert alerta.registrar("caiu", "x")["enviado"] is False

    def test_estado_desconhecido(self, monkeypatch):
        _montar(monkeypatch)
        assert alerta.registrar("qualquer-coisa")["enviado"] is False


class TestTexto:
    def test_mensagem_de_queda_diz_o_que_fazer(self, monkeypatch):
        cliente, _ = _montar(monkeypatch)
        alerta.registrar("caiu", "API /health devolveu HTTP 000 (esperado 200).")
        texto = cliente.enviadas[0][1]
        assert "produção fora do ar" in texto
        assert "HTTP 000" in texto
        assert "Infraestrutura" in texto      # para onde ir
        assert "Hostinger" in texto          # a limitação que não se desfaz sozinha

    def test_mensagem_de_volta_traz_a_duracao(self, monkeypatch):
        cliente, redis = _montar(monkeypatch)
        alerta.registrar("caiu", "x")
        from datetime import datetime, timedelta, timezone

        redis.dados[alerta.CHAVE_INCIDENTE] = (
            datetime.now(timezone.utc) - timedelta(minutes=42)
        ).isoformat()
        alerta.registrar("voltou")
        assert "42 min" in cliente.enviadas[-1][1]
