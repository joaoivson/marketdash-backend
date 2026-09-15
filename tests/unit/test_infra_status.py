"""
Painel de infraestrutura — o que pode mentir na tela.

O teste central é `_avaliar_ponta`: ele existe porque em 11/09/2026 o Coolify
disse `running:healthy` com a API inalcançável há horas, e um painel que
repetisse esse veredito seria pior do que painel nenhum — ensinaria a confiar
no verde. Os casos abaixo são os três modos de falha reais do Traefik.
"""
import asyncio

import httpx
import pytest

from app.services import infra_status_service as infra


def _resposta(status: int, corpo: str, tipo: str = "text/plain") -> httpx.Response:
    return httpx.Response(status, text=corpo, headers={"content-type": tipo})


class TestAvaliarPonta:
    def test_api_saudavel(self):
        corpo = '{"status":"healthy","database":"connected","redis":"connected"}'
        ok, detalhe = infra._avaliar_ponta("api", _resposta(200, corpo, "application/json"))
        assert ok is True
        assert detalhe == "healthy"

    def test_404_do_traefik_em_texto_puro(self):
        """O modo de falha de 11/09: proxy sem rota devolve isto em TODA rota."""
        ok, detalhe = infra._avaliar_ponta("api", _resposta(404, "404 page not found\n"))
        assert ok is False
        assert "404" in detalhe

    def test_200_que_nao_e_nosso(self):
        """Código 200 não prova nada — a palavra no corpo prova."""
        ok, detalhe = infra._avaliar_ponta(
            "api", _resposta(200, "<html>bem-vindo ao nginx</html>", "text/html")
        )
        assert ok is False
        assert "healthy" in detalhe

    def test_frontend_com_div_root(self):
        html = '<!doctype html><html><body><div id="root"></div></body></html>'
        ok, detalhe = infra._avaliar_ponta("frontend", _resposta(200, html, "text/html"))
        assert ok is True

    def test_frontend_sem_div_root(self):
        ok, detalhe = infra._avaliar_ponta("frontend", _resposta(200, "<h1>Coolify</h1>"))
        assert ok is False
        assert "root" in detalhe


class TestDividirStatus:
    def test_saudavel(self):
        assert infra._dividir_status("running:healthy") == ("running", "healthy")

    def test_desconhecido_nao_e_falha(self):
        """Worker sem healthcheck responde `running:unknown` todo dia. Tratar
        isso como problema pintaria metade do painel de vermelho."""
        estado, saude = infra._dividir_status("running:unknown")
        assert estado == "running"
        assert saude == "unknown"

    def test_container_parado(self):
        assert infra._dividir_status("exited:unhealthy") == ("exited", "unhealthy")

    def test_sem_status(self):
        assert infra._dividir_status(None) == ("desconhecido", None)


class TestLimite:
    @pytest.mark.parametrize("valor", [None, "", "0", 0, "0m"])
    def test_zero_significa_sem_teto(self, valor):
        """No Coolify, `0` é ausência de limite — não um teto de zero CPU."""
        assert infra._limite(valor) is None

    def test_teto_real(self):
        assert infra._limite("0.5") == "0.5"
        assert infra._limite("1536m") == "1536m"


class TestRotuloProvisorio:
    def test_recurso_novo_com_hml_no_nome(self):
        rotulo, ambiente, papel = infra._rotulo_provisorio("celery-novo-hml", "main")
        assert ambiente == "homologacao"
        assert papel == "nao_mapeado"

    def test_recurso_novo_na_main(self):
        _, ambiente, _ = infra._rotulo_provisorio("servico-novo", "main")
        assert ambiente == "producao"


class TestMontarRecursos:
    def test_status_de_resources_vence(self):
        """`/applications` guarda o status da última vez que o Coolify olhou;
        `/servers/{uuid}/resources` é o que a UI mostra. Divergindo, vale o
        mais fresco — senão o painel mostra container de pé que já caiu."""
        apps = [{"uuid": "toow0co8g40gkc44w84c4skw", "name": "api", "status": "running:healthy"}]
        recursos = [{"uuid": "toow0co8g40gkc44w84c4skw", "status": "exited:unhealthy"}]
        linhas = infra._montar_recursos(apps, [], recursos)
        assert linhas[0]["estado"] == "exited"

    def test_recurso_so_no_servidor_entra_na_lista(self):
        """Recurso invisível no painel é exatamente o que ninguém monitora."""
        recursos = [{"uuid": "zzz", "name": "servico-solto", "type": "service",
                     "status": "running:healthy"}]
        linhas = infra._montar_recursos([], [], recursos)
        assert [l["uuid"] for l in linhas] == ["zzz"]
        assert linhas[0]["papel"] == "nao_mapeado"

    def test_producao_vem_antes_de_homologacao(self):
        apps = [
            {"uuid": "r448swsggoock0wg80csws0k", "status": "running:healthy"},  # api hml
            {"uuid": "pgg440ogkco04ks4ww88swgs", "status": "running:unknown"},  # worker prod
            {"uuid": "toow0co8g40gkc44w84c4skw", "status": "running:healthy"},  # api prod
        ]
        linhas = infra._montar_recursos(apps, [], [])
        assert [(l["ambiente"], l["papel"]) for l in linhas] == [
            ("producao", "api"),
            ("producao", "worker"),
            ("homologacao", "api"),
        ]

    def test_nome_feio_do_coolify_ganha_rotulo_legivel(self):
        apps = [{"uuid": "pgg440ogkco04ks4ww88swgs", "name": "cerely-qs8480sgosccoc8go8wsg84s"}]
        linha = infra._montar_recursos(apps, [], [])[0]
        assert linha["rotulo"] == "Worker Celery"
        assert linha["nome_coolify"] == "cerely-qs8480sgosccoc8go8wsg84s"


class TestFilaLegivel:
    def test_prioridade_do_redis_fica_visivel(self):
        """O Celery emula prioridade com dois bytes de controle no nome. Sem
        traduzir, a tela mostra caractere invisível e duas filas diferentes
        parecem a mesma linha."""
        assert (
            infra._nome_legivel_da_fila("marketdash-abc\x06\x169")
            == "marketdash-abc (prioridade 9)"
        )

    def test_fila_base_sai_intacta(self):
        assert infra._nome_legivel_da_fila("marketdash-abc") == "marketdash-abc"


class TestDegradacaoSemToken:
    def test_coolify_sem_token_instrui_em_vez_de_quebrar(self, monkeypatch):
        monkeypatch.setattr(infra.settings, "COOLIFY_TOKEN", None)
        bloco = asyncio.run(infra.coletar_coolify())
        assert bloco["configurado"] is False
        assert bloco["erro"] is None
        assert "COOLIFY_TOKEN" in bloco["instrucao"]
        assert bloco["recursos"] == []

    def test_hostinger_sem_token_instrui_em_vez_de_quebrar(self, monkeypatch):
        monkeypatch.setattr(infra.settings, "HOSTINGER_API_TOKEN", None)
        bloco = asyncio.run(infra.coletar_hostinger())
        assert bloco["configurado"] is False
        assert bloco["erro"] is None
        assert "hPanel" in bloco["instrucao"]

    def test_filas_sem_redis_nao_estoura(self, monkeypatch):
        monkeypatch.setattr(infra.settings, "REDIS_URL", None)
        bloco = infra.coletar_filas()
        assert bloco["configurado"] is False
        assert bloco["filas"] == []


class TestResumirSerie:
    def test_serie_da_hostinger(self):
        serie = {"unit": "percent", "usage": [{"value": 10}, {"value": 92}, {"value": 26}]}
        assert infra._resumir_serie(serie) == {
            "atual": 26, "pico": 92, "unidade": "percent", "pontos": 3,
        }

    def test_formato_estranho_devolve_none(self):
        """Bloco que não pude exercitar contra a API real (não há token): o
        desenho é devolver ausência, nunca estourar e levar o painel com ele."""
        assert infra._resumir_serie(None) is None
        assert infra._resumir_serie({"usage": []}) is None
        assert infra._resumir_serie({"usage": [{"sem_value": 1}]}) is None


class TestSaudeInterna:
    def test_le_banco_e_redis_do_health(self):
        corpo = '{"status":"healthy","database":"connected","redis":"connected"}'
        assert infra._saude_interna(corpo) == {"database": "connected", "redis": "connected"}

    def test_redis_caido_com_app_no_ar(self):
        """O caso de 26/08: app respondendo, broker em crashloop — upload
        aceito com 201 e arquivo perdido. Só este campo mostra."""
        corpo = '{"status":"healthy","database":"connected","redis":"disconnected"}'
        assert infra._saude_interna(corpo)["redis"] == "disconnected"

    def test_corpo_que_nao_e_json(self):
        assert infra._saude_interna("404 page not found") is None


def _ponta(ambiente, tipo, ok, interna=None, latencia=120, url="https://x/health"):
    return {
        "rotulo": "X", "ambiente": ambiente, "url": url, "tipo": tipo,
        "http": 200 if ok else 404, "ok": ok, "detalhe": "d",
        "latencia_ms": latencia, "saude_interna": interna,
    }


class TestCruzar:
    def test_verde_do_coolify_com_url_fora_e_denunciado(self):
        """O incidente de 11/09 inteiro, em uma asserção."""
        recursos = infra._montar_recursos(
            [{"uuid": "toow0co8g40gkc44w84c4skw", "status": "running:healthy"}], [], []
        )
        pontas = [_ponta("producao", "api", ok=False)]
        infra._cruzar(recursos, pontas, {"ping_ms": None})
        assert "não responde" in recursos[0]["contradicao"]

    def test_concordancia_nao_gera_ruido(self):
        recursos = infra._montar_recursos(
            [{"uuid": "toow0co8g40gkc44w84c4skw", "status": "running:healthy"}], [], []
        )
        infra._cruzar(recursos, [_ponta("producao", "api", ok=True)], {"ping_ms": None})
        assert recursos[0]["contradicao"] is None

    def test_vermelho_do_coolify_com_servico_respondendo(self):
        """O sentido inverso, achado na 1ª execução real (15/09): o Coolify
        marcava as duas instâncias de Redis como `exited:unhealthy` com o
        `/health` dos dois ambientes dizendo `redis: connected`. Pintar de
        vermelho o que funciona também ensina a ignorar o painel."""
        recursos = infra._montar_recursos(
            [], [{"uuid": "h0cw0gc8owws004480g0sog8", "status": "exited:unhealthy"}], []
        )
        pontas = [_ponta("producao", "api", ok=True, interna={"redis": "connected"})]
        infra._cruzar(recursos, pontas, {"ping_ms": 10.6})
        assert "redis: connected" in recursos[0]["contradicao"]

    def test_redis_caido_de_verdade_nao_ganha_contradicao(self):
        """Sem contraprova, o vermelho do Coolify fica de pé — é o caso em que
        o painel PRECISA ser vermelho."""
        recursos = infra._montar_recursos(
            [], [{"uuid": "h0cw0gc8owws004480g0sog8", "status": "exited:unhealthy"}], []
        )
        pontas = [_ponta("producao", "api", ok=True, interna={"redis": "disconnected"})]
        infra._cruzar(recursos, pontas, {"ping_ms": None})
        assert recursos[0]["contradicao"] is None

    def test_ambiente_sem_ponta_correspondente_e_ignorado(self):
        recursos = infra._montar_recursos(
            [{"uuid": "hw88gc8ocsko04k8wkocs8kc", "status": "running:unknown"}], [], []
        )
        infra._cruzar(recursos, [_ponta("producao", "api", ok=False)], {"ping_ms": None})
        assert recursos[0]["contradicao"] is None
