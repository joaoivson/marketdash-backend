"""
Normalização do número e o cliente WAHA.

Número errado aqui significa mandar mensagem para um desconhecido — a via
mais curta para o número ser denunciado. (Os testes do SAIR saíram junto com
o resumo diário — spec §9.1.)
"""
import httpx
import pytest

from app.services.waha_client import (
    ErroWhatsapp, WahaClient, chat_id_de_numero, mascarar, normalizar_numero,
    validar_jid_de_grupo,
)


@pytest.mark.parametrize("entrada,esperado", [
    ("11999998888", "5511999998888"),
    ("(11) 99999-8888", "5511999998888"),
    ("+55 11 99999-8888", "5511999998888"),
    ("55 11 99999 8888", "5511999998888"),
    ("  11 9 9999-8888  ", "5511999998888"),
    ("5511999998888", "5511999998888"),
])
def test_formatos_que_gente_digita_viram_e164(entrada, esperado):
    assert normalizar_numero(entrada) == esperado


@pytest.mark.parametrize("entrada", [
    "", "   ", "abc",
    "1199998888",        # fixo (10 dígitos) — não recebe WhatsApp
    "11899998888",       # nono dígito não é 9
    "999998888",         # sem DDD
    "5511999998888999",  # comprido demais
])
def test_numero_que_nao_serve_e_recusado(entrada):
    with pytest.raises(ValueError):
        normalizar_numero(entrada)


def test_mascara_nunca_mostra_o_numero_inteiro():
    m = mascarar("5511999998888")
    assert "99999" not in m
    assert m.startswith("5511") and m.endswith("88")


def test_chat_id_de_numero():
    assert chat_id_de_numero("5511999998888") == "5511999998888@c.us"


@pytest.mark.parametrize("jid", ["120363123456789012@g.us", "5511-1467@g.us"])
def test_jid_de_grupo_valido_passa(jid):
    assert validar_jid_de_grupo(jid) == jid


@pytest.mark.parametrize("jid", [
    "", "5511999998888", "5511999998888@c.us",
    "abc@g.us", "120363@newsletter",
])
def test_jid_de_grupo_invalido_e_recusado(jid):
    with pytest.raises(ValueError):
        validar_jid_de_grupo(jid)


# --- cliente WAHA ------------------------------------------------------------
#
# Mocks travam o comportamento contra regressão; o contrato foi conferido na
# documentação oficial do WAHA (sessions, sendText, groups, auth/qr).

def _cliente(responder):
    c = WahaClient("http://waha:3000", "chave", "mkdtestu1xabcd")
    c._transport = httpx.MockTransport(responder)
    return c


def test_sessao_ja_existente_nao_e_erro():
    # A tela de conexão chama isto toda vez que abre; 422 "already exists" é o
    # caso normal a partir da segunda vez.
    def responder(req):
        return httpx.Response(422, json={"message": "Session already exists"})

    assert _cliente(responder).criar_sessao() == {"ja_existia": True}


def test_falha_de_verdade_ao_criar_sessao_sobe_tipada():
    def responder(req):
        return httpx.Response(500, json={"message": "boom"})

    with pytest.raises(ErroWhatsapp) as e:
        _cliente(responder).criar_sessao()
    assert e.value.motivo == "criar_sessao"


def test_chave_invalida_e_auth_e_fatal():
    def responder(req):
        return httpx.Response(401, json={"message": "Unauthorized"})

    with pytest.raises(ErroWhatsapp) as e:
        _cliente(responder).estado()
    assert e.value.motivo == "auth" and e.value.fatal


def test_sessao_inexistente_devolve_info_vazia_e_estado_proprio():
    def responder(req):
        return httpx.Response(404, json={"message": "not found"})

    c = _cliente(responder)
    assert c.sessao_info() == {}
    assert c.estado() == "inexistente"
    assert c.conectado() is False


def test_estado_working_e_numero_conectado():
    def responder(req):
        return httpx.Response(200, json={
            "name": "mkdtestu1xabcd", "status": "WORKING",
            "me": {"id": "5511999998888@c.us", "pushName": "Maria"},
        })

    c = _cliente(responder)
    assert c.estado() == "WORKING"
    assert c.conectado() is True
    assert c.numero_conectado() == "5511999998888"


def test_enviar_texto_em_grupo_valida_jid_e_manda_chat_id():
    capturado = {}

    def responder(req):
        import json as _json
        capturado.update(_json.loads(req.content))
        return httpx.Response(201, json={"id": "msg1"})

    _cliente(responder).enviar_texto("120363123456789012@g.us", "oferta!")
    assert capturado["chatId"] == "120363123456789012@g.us"
    assert capturado["session"] == "mkdtestu1xabcd"

    with pytest.raises(ValueError):
        _cliente(responder).enviar_texto("abc@g.us", "oferta!")


def test_erro_de_grupo_vira_grupo_invalido_e_nao_derruba_a_sessao():
    # Fomos removidas do grupo / grupo apagado: problema de UMA linha do lote.
    def responder(req):
        return httpx.Response(400, json={"message": "group jid does not exist"})

    with pytest.raises(ErroWhatsapp) as e:
        _cliente(responder).enviar_texto("120363123456789012@g.us", "x")
    assert e.value.motivo == "grupo_invalido"
    assert not e.value.fatal


def test_erro_de_desconexao_e_fatal():
    def responder(req):
        return httpx.Response(422, json={"message": "Session status is not WORKING, disconnect"})

    with pytest.raises(ErroWhatsapp) as e:
        _cliente(responder).enviar_texto("5511999998888@c.us", "x")
    assert e.value.motivo == "desconectado" and e.value.fatal


def test_listar_grupos_pagina_e_devolve_lista():
    def responder(req):
        assert "/groups" in str(req.url)
        assert req.url.params["limit"] == "100"
        return httpx.Response(200, json=[
            {"id": "120363111@g.us", "subject": "Achadinhos",
             "participants": [{"id": "5511999998888@c.us", "role": "admin"}]},
        ])

    grupos = _cliente(responder).listar_grupos()
    assert grupos[0]["id"] == "120363111@g.us"


def test_qrcode_ausente_e_estado_normal():
    def responder(req):
        return httpx.Response(200, json={"mimetype": "image/png"})  # sem data

    assert _cliente(responder).qrcode() is None


def test_qrcode_vira_data_uri():
    def responder(req):
        return httpx.Response(200, json={"mimetype": "image/png", "data": "AAAA"})

    assert _cliente(responder).qrcode() == "data:image/png;base64,AAAA"


# --- URL do webhook atrás de proxy ------------------------------------------

def test_url_do_webhook_respeita_o_proto_do_proxy(monkeypatch):
    """
    Bug real em homologação (era Evolution, o risco é o mesmo no WAHA): o
    webhook recebeu `http://api.hml...`, que responde 301, e o provedor não
    segue redirecionamento. O SAIR nunca chegou — falha silenciosa, do tipo
    que só aparece como denúncia.

    `WAHA_WEBHOOK_URL` é zerada de propósito: este teste cobre o FALLBACK que
    deriva da request. Sem zerar, o resultado passava a depender do `.env` da
    máquina de quem roda — o teste quebrou no dia em que a env foi definida.
    """
    from types import SimpleNamespace
    from app.api.v1.routes.whatsapp import settings, url_do_webhook

    monkeypatch.setattr(settings, "WAHA_WEBHOOK_URL", None, raising=False)

    def req(headers):
        return SimpleNamespace(
            headers=headers,
            url_for=lambda nome: "http://api.hml.marketdash.com.br/api/v1/whatsapp/webhook",
        )

    assert url_do_webhook(req({"x-forwarded-proto": "https"})).startswith("https://")
    assert url_do_webhook(req({"x-forwarded-proto": "https, http"})).startswith("https://")
    assert url_do_webhook(req({})).startswith("http://")
    assert url_do_webhook(req({"x-forwarded-proto": "banana"})).startswith("http://")


def test_403_no_envio_a_grupo_e_grupo_invalido_nao_auth():
    # Fomos removidas do grupo → 403. Classificar como "auth" (fatal) mataria
    # o lote inteiro por causa de UM grupo.
    def responder(req):
        return httpx.Response(403, json={"message": "forbidden"})

    with pytest.raises(ErroWhatsapp) as e:
        _cliente(responder).enviar_texto("120363123456789012@g.us", "x")
    assert e.value.motivo == "grupo_invalido"
    assert not e.value.fatal


def test_403_fora_do_envio_continua_auth():
    def responder(req):
        return httpx.Response(403, json={"message": "forbidden"})

    with pytest.raises(ErroWhatsapp) as e:
        _cliente(responder).estado()
    assert e.value.motivo == "auth"


def test_422_generico_no_criar_sessao_e_erro_nao_ja_existia():
    # 422 é o erro de VALIDAÇÃO do WAHA (config de webhook malformada etc.);
    # engolir como "já existia" deixava a afiliada num QR eterno sem log.
    def responder(req):
        return httpx.Response(422, json={"detail": ["webhooks.0.url must be a URL"]})

    with pytest.raises(ErroWhatsapp) as e:
        _cliente(responder).criar_sessao()
    assert e.value.motivo == "criar_sessao"


def test_sessao_parada_no_listar_grupos_vira_desconectado():
    def responder(req):
        return httpx.Response(422, json={"message": "Session status is not WORKING"})

    with pytest.raises(ErroWhatsapp) as e:
        _cliente(responder).listar_grupos()
    assert e.value.motivo == "desconectado"


def test_numero_de_jid_e_o_inverso_de_chat_id():
    from app.services.waha_client import numero_de_jid
    assert numero_de_jid("5511999998888@c.us") == "5511999998888"
    assert numero_de_jid("5511999998888:12@c.us") == "5511999998888"
    assert numero_de_jid(None) == ""


def test_url_configurada_tem_precedencia_sobre_a_request(monkeypatch):
    """O outro lado da moeda: com a env definida, ela manda — é o caminho de
    produção, onde a request chega do proxy e não sabe a URL pública."""
    from types import SimpleNamespace
    from app.api.v1.routes.whatsapp import settings, url_do_webhook

    monkeypatch.setattr(settings, "WAHA_WEBHOOK_URL",
                        "https://api.exemplo.com/api/v1/whatsapp/webhook", raising=False)
    req = SimpleNamespace(headers={}, url_for=lambda nome: "http://qualquer/coisa")
    assert url_do_webhook(req) == "https://api.exemplo.com/api/v1/whatsapp/webhook"


# --- formato da resposta de /groups ------------------------------------------
#
# A documentação do WAHA diz que a resposta "depende do engine". A versão
# anterior fazia `dados if isinstance(dados, list) else []`: qualquer formato
# diferente virava ZERO grupos com o sync marcado como SUCESSO. Foi o que
# aconteceu em homologação — quatro sincronizações "bem-sucedidas" com
# `vistos=0` e nenhum log dizendo por quê.


def test_lista_crua_passa_direto():
    from app.services.waha_client import _lista_de_grupos

    dados = [{"id": "120363111@g.us"}]
    assert _lista_de_grupos(dados) == dados


@pytest.mark.parametrize("chave", ["groups", "data", "items", "results"])
def test_envelope_conhecido_e_desembrulhado(chave):
    from app.services.waha_client import _lista_de_grupos

    grupos = [{"id": "120363111@g.us"}]
    assert _lista_de_grupos({chave: grupos, "total": 1}) == grupos


@pytest.mark.parametrize("dados", [None, {}, {"erro": "x"}, "texto", 42])
def test_formato_desconhecido_e_ERRO_nunca_lista_vazia(dados):
    """Zero grupos tem que ser um fato do WhatsApp, nunca um formato que não
    soubemos ler. Devolver [] aqui é o que produziu o sync 'bem-sucedido' com
    nenhum grupo."""
    from app.services.waha_client import ErroWhatsapp, _lista_de_grupos

    with pytest.raises(ErroWhatsapp) as e:
        _lista_de_grupos(dados)
    assert e.value.motivo == "grupos"


def test_lista_vazia_de_verdade_continua_vazia():
    """O caso legítimo: a conta não tem grupo nenhum."""
    from app.services.waha_client import _lista_de_grupos

    assert _lista_de_grupos([]) == []
    assert _lista_de_grupos({"groups": []}) == []


def test_resposta_embrulhada_chega_ao_chamador():
    """Fio completo pelo cliente, não só pelo helper."""
    def responder(req):
        return httpx.Response(200, json={"groups": [{"id": "120363111@g.us"}], "total": 1})

    assert _cliente(responder).listar_grupos()[0]["id"] == "120363111@g.us"


def test_resposta_em_formato_ilegivel_sobe_erro_pelo_cliente():
    def responder(req):
        return httpx.Response(200, json={"inesperado": True})

    with pytest.raises(ErroWhatsapp):
        _cliente(responder).listar_grupos()


@pytest.mark.parametrize("bruto,esperado", [
    ("120363111@g.us", "120363111@g.us"),
    ({"_serialized": "120363111@g.us"}, "120363111@g.us"),
    ({"server": "g.us", "user": "120363111"}, "120363111@g.us"),
    ({"user": "120363111"}, "120363111@g.us"),
    ("5511999998888@c.us", None),          # conversa, não grupo
    (None, None), ({}, None), (123, None),
])
def test_jid_do_grupo_aceita_string_e_objeto(bruto, esperado):
    """`str(dict)` nunca termina em `@g.us`: com o id em forma de objeto, TODO
    grupo era descartado em silêncio e o sync terminava com zero."""
    from app.services.whatsapp_grupo_sync_service import jid_do_grupo

    assert jid_do_grupo({"id": bruto}) == esperado


# --- engine GOWS: as structs do whatsmeow, em PascalCase ---------------------
#
# Em 26/08/2026 o sync trouxe 499 grupos em 5 páginas e gravou ZERO, terminando
# "com sucesso". O log do backend entregou a causa: `100 de 100 itens sem JID
# reconhecível (chaves do 1º: ['AddressingMode', 'AnnounceVersionID',
# 'CreatorCountryCode', 'DefaultMembershipApprovalMode', 'DisappearingTimer',
# 'GroupCreated', 'IsAnnounce', 'IsDefaultSubGroup'])`.
#
# São os campos de `types.GroupInfo` do whatsmeow: o GOWS serializa a struct Go
# como ela é, com as embutidas achatadas. O parser lia só `id` minúsculo.

def _grupo_gows(jid="120363412019840927@g.us", nome="Achadinhos SP",
                participantes=None, is_announce=False):
    """Payload como o GOWS devolve — chaves reais observadas em produção."""
    return {
        "JID": jid,                       # types.JID tem MarshalText → string
        "OwnerJID": "553498557753@s.whatsapp.net",
        "Name": nome,                     # de GroupName, embutida e achatada
        "NameSetAt": "2026-08-01T10:00:00Z",
        "Topic": "",
        "IsLocked": False,
        "IsAnnounce": is_announce,
        "AnnounceVersionID": "1724668800",
        "IsEphemeral": False,
        "DisappearingTimer": 0,
        "IsDefaultSubGroup": False,
        "GroupCreated": "2026-01-15T12:00:00Z",
        "CreatorCountryCode": "55",
        "AddressingMode": "pn",
        "DefaultMembershipApprovalMode": "",
        "Participants": participantes if participantes is not None else [
            {"JID": "553498557753@s.whatsapp.net", "IsAdmin": True,
             "IsSuperAdmin": False, "DisplayName": "", "Error": 0},
            {"JID": "5511999998888@s.whatsapp.net", "IsAdmin": False,
             "IsSuperAdmin": False, "DisplayName": "", "Error": 0},
        ],
    }


def test_gows_o_grupo_e_reconhecido_pelo_campo_JID():
    from app.services.whatsapp_grupo_sync_service import jid_do_grupo

    assert jid_do_grupo(_grupo_gows()) == "120363412019840927@g.us"


def test_gows_nome_e_participantes_saem_do_PascalCase():
    from app.services.whatsapp_grupo_sync_service import _extrair_agregados, _valor

    dados = _grupo_gows()
    assert _valor(dados, "subject", "name") == "Achadinhos SP"
    ag = _extrair_agregados(dados, {"553498557753"})
    assert ag["participantes"] == 2
    assert ag["sou_admin"] is True


def test_gows_admin_vem_de_IsAdmin_e_nao_do_papel_em_texto():
    from app.services.whatsapp_grupo_sync_service import _extrair_agregados

    # o nosso número está no grupo, mas sem ser admin
    dados = _grupo_gows(participantes=[
        {"JID": "553498557753@s.whatsapp.net", "IsAdmin": False, "IsSuperAdmin": False},
    ])
    assert _extrair_agregados(dados, {"553498557753"})["sou_admin"] is False

    dados = _grupo_gows(participantes=[
        {"JID": "553498557753@s.whatsapp.net", "IsAdmin": False, "IsSuperAdmin": True},
    ])
    assert _extrair_agregados(dados, {"553498557753"})["sou_admin"] is True


def test_gows_com_endereçamento_LID_ainda_reconhece_o_nosso_numero():
    """
    Grupo em modo LID: o participante vem como `…@lid` e o telefone fica em
    `PhoneNumber`. Comparar só o JID faria o número não se achar — e todo grupo
    nasceria "não sou admin", travando envio e convite.
    """
    from app.services.whatsapp_grupo_sync_service import _extrair_agregados

    dados = _grupo_gows(participantes=[
        {"JID": "81273733259337@lid", "LID": "81273733259337@lid",
         "PhoneNumber": "553498557753@s.whatsapp.net", "IsAdmin": True},
    ])
    dados["AddressingMode"] = "lid"
    assert _extrair_agregados(dados, {"553498557753"})["sou_admin"] is True
    # e também quando o que conhecemos de nós é o próprio LID
    assert _extrair_agregados(dados, {"81273733259337"})["sou_admin"] is True


def test_gows_IsAnnounce_fecha_o_grupo_para_quem_nao_e_admin():
    from app.services.whatsapp_grupo_sync_service import _extrair_agregados

    dados = _grupo_gows(is_announce=True, participantes=[
        {"JID": "553498557753@s.whatsapp.net", "IsAdmin": False},
    ])
    ag = _extrair_agregados(dados, {"553498557753"})
    assert ag["sou_admin"] is False and ag["permite_envio"] is False

    aberto = _grupo_gows(is_announce=False, participantes=[
        {"JID": "553498557753@s.whatsapp.net", "IsAdmin": False},
    ])
    assert _extrair_agregados(aberto, {"553498557753"})["permite_envio"] is True


def test_noweb_e_webjs_continuam_funcionando():
    """A correção do GOWS não pode quebrar os formatos que já vinham."""
    from app.services.whatsapp_grupo_sync_service import _extrair_agregados, jid_do_grupo

    noweb = {
        "id": "120363412019840927@g.us",
        "subject": "Achadinhos SP",
        "announce": False,
        "participants": [
            {"id": "553498557753@s.whatsapp.net", "role": "admin"},
            {"id": "5511999998888@s.whatsapp.net", "role": "participant"},
        ],
    }
    assert jid_do_grupo(noweb) == "120363412019840927@g.us"
    ag = _extrair_agregados(noweb, {"553498557753"})
    assert ag["participantes"] == 2 and ag["sou_admin"] is True

    webjs = {"id": {"_serialized": "120363412019840927@g.us"}, "name": "X"}
    assert jid_do_grupo(webjs) == "120363412019840927@g.us"


def test_participante_de_outro_numero_nao_nos_torna_admin():
    from app.services.whatsapp_grupo_sync_service import _extrair_agregados

    dados = _grupo_gows(participantes=[
        {"JID": "5511999998888@s.whatsapp.net", "IsAdmin": True},
    ])
    assert _extrair_agregados(dados, {"553498557753"})["sou_admin"] is False


@pytest.mark.parametrize("corpo,tipo,esperado", [
    # medido contra o WAHA real (GOWS) em 26/08: link inteiro em texto puro
    ("https://chat.whatsapp.com/FG7OijWw40p6pukvb4ZyOB", "text/plain",
     "https://chat.whatsapp.com/FG7OijWw40p6pukvb4ZyOB"),
    # só o código, também em texto puro
    ("FG7OijWw40p6pukvb4ZyOB", "text/plain",
     "https://chat.whatsapp.com/FG7OijWw40p6pukvb4ZyOB"),
    # e as formas JSON que outros engines podem usar
    ('{"code": "FG7OijWw40p6pukvb4ZyOB"}', "application/json",
     "https://chat.whatsapp.com/FG7OijWw40p6pukvb4ZyOB"),
    ('{"InviteCode": "FG7OijWw40p6pukvb4ZyOB"}', "application/json",
     "https://chat.whatsapp.com/FG7OijWw40p6pukvb4ZyOB"),
])
def test_convite_de_grupo_em_texto_puro_ou_json(corpo, tipo, esperado):
    """
    O link vinha e era descartado: `dados.get("code")` num corpo de texto puro
    dá None, e o erro era engolido — 169 grupos de admin, zero convites, sem
    nenhuma pista. O `_pedir` embrulha corpo não-JSON em {"texto": ...}.
    """
    def responder(req):
        return httpx.Response(200, content=corpo.encode(),
                              headers={"content-type": tipo})

    assert _cliente(responder).convite_do_grupo("120363000000000001@g.us") == esperado


def test_falha_de_convite_sobe_o_motivo_em_vez_de_none():
    """Silêncio aqui foi o que impediu de descobrir o caso acima."""
    def responder(req):
        return httpx.Response(403, json={"error": "not admin"})

    with pytest.raises(ErroWhatsapp) as e:
        _cliente(responder).convite_do_grupo("120363000000000001@g.us")
    assert e.value.motivo == "convite" and "403" in e.value.detalhe
