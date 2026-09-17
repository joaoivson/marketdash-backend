"""Envio retroativo de directs (17/09/2026).

O que estes testes protegem:
- nenhum retroativo faz o que o webhook não faria (própria conta, já respondido,
  sem palavra, mesma pessoa, janela de 7 dias da Meta);
- os grupos da prévia somam o total — cada comentário sabe por que ficou de fora;
- o envio recalcula no servidor, vai para a fila de LOTE (priority=9), espaçado;
- o valor enfileirado é aceito pelo pipeline real como se fosse do webhook.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    CONEXAO_ATIVA,
    DM_ENVIADO,
    DM_SEM_MATCH,
    ESCOPO_POST_ESPECIFICO,
    ESCOPO_QUALQUER,
    TRIGGER_PALAVRAS,
    InstagramAutomation,
    InstagramConnection,
    InstagramEvent,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.services import instagram_login_client as ig
from app.services import instagram_retroativo_service as retro
from app.services.instagram_comment_pipeline import InstagramCommentPipeline
from app.utils.text_normalize import normalizar_comentario

IG_USER_ID = "17841400000000000"
IG_USERNAME = "promos.da.aluna"
MEDIA_ID = "18000000000000000"
AGORA = datetime.now(timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            InstagramConnection.__table__,
            InstagramAutomation.__table__,
            InstagramEvent.__table__,
        ],
    )
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


@pytest.fixture
def conexao(db):
    from app.core.encryption import encrypt_value

    c = InstagramConnection(
        user_id=1,
        ig_user_id=IG_USER_ID,
        ig_username=IG_USERNAME,
        access_token=encrypt_value("token-de-teste"),
        token_expires_at=AGORA + timedelta(days=50),
        status=CONEXAO_ATIVA,
    )
    db.add(c)
    db.commit()
    return c


def _automacao(db, conexao, **kwargs) -> InstagramAutomation:
    padrao = dict(
        user_id=1,
        connection_id=conexao.id,
        nome="Calcinhas Algodão",
        escopo=ESCOPO_POST_ESPECIFICO,
        media_id=MEDIA_ID,
        trigger_tipo=TRIGGER_PALAVRAS,
        palavras=[normalizar_comentario(p) for p in ["quero", "algodão"]],
        palavras_exibicao=["Quero", "algodão"],
        resposta_publica_ativa=False,
        resposta_publica_variacoes=[],
        dm_texto="Aqui está o link",
        status=AUTOMACAO_ATIVA,
    )
    padrao.update(kwargs)
    a = InstagramAutomation(**padrao)
    db.add(a)
    db.commit()
    return a


def _c(comment_id, texto="quero", pessoa="p1", horas_atras=1.0, username=None, replies=None):
    ts = (AGORA - timedelta(hours=horas_atras)).strftime("%Y-%m-%dT%H:%M:%S+0000")
    comentario = {
        "id": comment_id,
        "text": texto,
        "timestamp": ts,
        "username": username or f"user_{pessoa}",
        "from": {"id": pessoa, "username": username or f"user_{pessoa}"},
    }
    if replies:
        comentario["replies"] = {"data": replies}
    return comentario


def _evento(db, comment_id, dm_status, commenter_id="px", automation_id=None):
    db.add(
        InstagramEvent(
            user_id=1,
            automation_id=automation_id,
            comment_id=comment_id,
            media_id=MEDIA_ID,
            commenter_id=commenter_id,
            dm_status=dm_status,
        )
    )
    db.commit()


class _GraphFalsa:
    def __init__(self, paginas):
        self.paginas = paginas
        self.chamadas = []

    async def list_comments(self, token, media_id, after=None, limit=50):
        self.chamadas.append((media_id, after))
        indice = int(after) if after else 0
        pagina = {"data": self.paginas[indice]}
        if indice + 1 < len(self.paginas):
            pagina["paging"] = {"cursors": {"after": str(indice + 1)}, "next": "https://..."}
        return pagina


@pytest.fixture
def fila(monkeypatch):
    enfileiradas = []

    class _TaskFalsa:
        @staticmethod
        def apply_async(kwargs=None, priority=None, countdown=None):
            enfileiradas.append({"kwargs": kwargs, "priority": priority, "countdown": countdown})

    import app.tasks.instagram_tasks as tasks

    monkeypatch.setattr(tasks, "processar_comentario_instagram_task", _TaskFalsa)
    monkeypatch.setattr(retro, "get_client", lambda: None)
    return enfileiradas


def _usar_graph(monkeypatch, paginas) -> _GraphFalsa:
    graph = _GraphFalsa(paginas)
    monkeypatch.setattr(ig, "list_comments", graph.list_comments)
    return graph


def _servico(db):
    return retro.InstagramRetroativoService(InstagramAutomationRepository(db))


# --------------------------------------------------------------------------- #
#  Classificação                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_previa_separa_cada_comentario_no_grupo_certo(db, conexao, monkeypatch, fila):
    automacao = _automacao(db, conexao)
    _evento(db, "respondido", DM_ENVIADO, commenter_id="p2", automation_id=automacao.id)
    _evento(db, "sem-match-antigo", DM_SEM_MATCH, commenter_id="p3")
    _usar_graph(
        monkeypatch,
        [
            [
                _c("elegivel", pessoa="p1"),
                _c("respondido", pessoa="p2"),
                _c("sem-match-antigo", pessoa="p3", texto="lindo"),
                _c("sem-palavra", pessoa="p4", texto="que lindo"),
                _c("mesma-pessoa-ja-recebeu", pessoa="p2", texto="quero"),
                _c("velho", pessoa="p5", horas_atras=24 * 8),
                # Resposta pública NOSSA, vinda sem `from` — pega pelo username.
                {"id": "nossa-resposta", "text": "Te mandei no direct", "username": IG_USERNAME,
                 "timestamp": _c("x")["timestamp"]},
                _c("resposta-em-thread", pessoa="p6", replies=[_c("reply-quero", pessoa="p7")]),
            ]
        ],
    )

    previa = await _servico(db).previa(1, automacao.id)

    assert previa.total_comentarios == 9
    assert previa.elegiveis == 3  # elegivel, resposta-em-thread, reply-quero
    assert previa.ja_respondidos == 1
    assert previa.ja_processados == 1
    assert previa.sem_palavra == 1
    assert previa.pessoa_ja_recebeu == 1
    assert previa.fora_da_janela == 1
    assert previa.da_propria_conta == 1
    soma = (
        previa.elegiveis + previa.ja_respondidos + previa.ja_processados + previa.sem_palavra
        + previa.pessoa_ja_recebeu + previa.fora_da_janela + previa.da_propria_conta
    )
    assert soma == previa.total_comentarios


@pytest.mark.asyncio
async def test_mesma_pessoa_varias_vezes_so_o_primeiro_comentario_entra(db, conexao, monkeypatch, fila):
    automacao = _automacao(db, conexao)
    _usar_graph(
        monkeypatch,
        [[_c("segundo", pessoa="p1", horas_atras=1), _c("primeiro", pessoa="p1", horas_atras=5)]],
    )

    _, _, classificados, _ = await _servico(db).levantar(1, automacao.id)

    grupos = {c.comment_id: c.grupo for c in classificados}
    assert grupos == {"primeiro": retro.GRUPO_ELEGIVEL, "segundo": retro.GRUPO_PESSOA_JA_RECEBEU}


@pytest.mark.asyncio
async def test_comentario_expirado_nao_gasta_a_pessoa(db, conexao, monkeypatch, fila):
    """Ela comentou há 8 dias e de novo ontem: o de ontem ainda pode receber."""
    automacao = _automacao(db, conexao)
    _usar_graph(
        monkeypatch,
        [[_c("antigo", pessoa="p1", horas_atras=24 * 8), _c("ontem", pessoa="p1", horas_atras=20)]],
    )

    previa = await _servico(db).previa(1, automacao.id)

    assert previa.elegiveis == 1
    assert previa.fora_da_janela == 1


@pytest.mark.asyncio
async def test_previa_informa_quando_o_primeiro_elegivel_expira(db, conexao, monkeypatch, fila):
    automacao = _automacao(db, conexao)
    _usar_graph(monkeypatch, [[_c("a", pessoa="p1", horas_atras=24 * 6), _c("b", pessoa="p2")]])

    previa = await _servico(db).previa(1, automacao.id)

    esperado = AGORA - timedelta(hours=24 * 6) + timedelta(days=7)
    assert abs((previa.primeiro_expira_em - esperado).total_seconds()) < 2


@pytest.mark.asyncio
async def test_le_todas_as_paginas_de_comentarios(db, conexao, monkeypatch, fila):
    automacao = _automacao(db, conexao)
    graph = _usar_graph(
        monkeypatch, [[_c("a", pessoa="p1")], [_c("b", pessoa="p2")], [_c("c", pessoa="p3")]]
    )

    previa = await _servico(db).previa(1, automacao.id)

    assert previa.elegiveis == 3
    assert [after for _, after in graph.chamadas] == [None, "1", "2"]


@pytest.mark.asyncio
async def test_teto_de_leitura_marca_truncado(db, conexao, monkeypatch, fila):
    automacao = _automacao(db, conexao)
    monkeypatch.setattr(retro, "MAX_COMENTARIOS_ANALISADOS", 2)
    _usar_graph(monkeypatch, [[_c("a", pessoa="p1"), _c("b", pessoa="p2")], [_c("c", pessoa="p3")]])

    previa = await _servico(db).previa(1, automacao.id)

    assert previa.total_comentarios == 2
    assert previa.truncado is True


# --------------------------------------------------------------------------- #
#  Envio                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_envio_enfileira_so_elegiveis_no_lote_e_espacado(db, conexao, monkeypatch, fila):
    automacao = _automacao(db, conexao)
    _evento(db, "respondido", DM_ENVIADO, commenter_id="p9", automation_id=automacao.id)
    _usar_graph(
        monkeypatch,
        [[_c("a", pessoa="p1", horas_atras=3), _c("b", pessoa="p2", horas_atras=2),
          _c("respondido", pessoa="p9"), _c("x", pessoa="p3", texto="bonito")]],
    )

    resposta = await _servico(db).enviar(1, automacao.id)

    assert resposta.enfileirados == 2
    assert [f["kwargs"]["valor"]["id"] for f in fila] == ["a", "b"]
    assert all(f["priority"] == 9 for f in fila), "retroativo é lote: só 0 ou 9 são consumidos"
    assert [f["countdown"] for f in fila] == [0, retro.INTERVALO_ENTRE_ENVIOS_S]
    assert fila[0]["kwargs"]["ig_user_id"] == IG_USER_ID
    assert fila[0]["kwargs"]["valor"]["media"] == {"id": MEDIA_ID}
    assert fila[0]["kwargs"]["entrega_id"] is None


@pytest.mark.asyncio
async def test_envio_com_automacao_pausada_recusa_sem_ler_a_meta(db, conexao, monkeypatch, fila):
    automacao = _automacao(db, conexao, status="pausada")
    graph = _usar_graph(monkeypatch, [[_c("a")]])

    with pytest.raises(HTTPException) as exc:
        await _servico(db).enviar(1, automacao.id)

    assert exc.value.status_code == 409
    assert graph.chamadas == []
    assert fila == []


@pytest.mark.asyncio
async def test_previa_funciona_com_automacao_pausada(db, conexao, monkeypatch, fila):
    """A aluna precisa ver o tamanho do buraco antes de decidir religar."""
    automacao = _automacao(db, conexao, status="pausada")
    _usar_graph(monkeypatch, [[_c("a")]])
    previa = await _servico(db).previa(1, automacao.id)
    assert previa.elegiveis == 1


@pytest.mark.asyncio
async def test_retroativo_so_existe_para_publicacao_especifica(db, conexao, monkeypatch, fila):
    automacao = _automacao(db, conexao, escopo=ESCOPO_QUALQUER, media_id=None)
    with pytest.raises(HTTPException) as exc:
        await _servico(db).previa(1, automacao.id)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_automacao_de_outra_aluna_da_404(db, conexao, monkeypatch, fila):
    automacao = _automacao(db, conexao)
    with pytest.raises(HTTPException) as exc:
        await _servico(db).previa(2, automacao.id)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_clique_duplo_recusa_o_segundo_envio(db, conexao, monkeypatch, fila):
    automacao = _automacao(db, conexao)
    _usar_graph(monkeypatch, [[_c("a")]])

    class _RedisFalso:
        def __init__(self):
            self.chaves = set()

        def set(self, chave, valor, nx=False, ex=None):
            if nx and chave in self.chaves:
                return None
            self.chaves.add(chave)
            return True

        def delete(self, chave):
            self.chaves.discard(chave)

    redis = _RedisFalso()
    monkeypatch.setattr(retro, "get_client", lambda: redis)

    await _servico(db).enviar(1, automacao.id)
    with pytest.raises(HTTPException) as exc:
        await _servico(db).enviar(1, automacao.id)

    assert exc.value.status_code == 409
    assert len(fila) == 1


@pytest.mark.asyncio
async def test_falha_ao_ler_a_meta_solta_a_trava(db, conexao, monkeypatch, fila):
    automacao = _automacao(db, conexao)
    chaves = set()

    class _Redis:
        def set(self, chave, valor, nx=False, ex=None):
            if nx and chave in chaves:
                return None
            chaves.add(chave)
            return True

        def delete(self, chave):
            chaves.discard(chave)

    monkeypatch.setattr(retro, "get_client", lambda: _Redis())

    async def _falha(*args, **kwargs):
        raise ig.InstagramApiError("Erro da Meta", codigo=2, permanente=False)

    monkeypatch.setattr(ig, "list_comments", _falha)

    with pytest.raises(HTTPException):
        await _servico(db).enviar(1, automacao.id)

    assert chaves == set(), "sem soltar, a aluna esperaria 5 min para tentar de novo"


@pytest.mark.asyncio
async def test_valor_enfileirado_passa_pelo_pipeline_real_e_envia(db, conexao, monkeypatch, fila):
    """O formato montado aqui precisa ser o que o pipeline do webhook entende."""
    automacao = _automacao(db, conexao)
    _usar_graph(monkeypatch, [[_c("a", pessoa="p1")]])
    await _servico(db).enviar(1, automacao.id)

    dms = []

    async def _dm(token, ig_user_id, comment_id, texto, link=None, botao_texto=None):
        dms.append(comment_id)
        return {"message_id": "m1"}

    async def _sem_espera(self):
        return None

    monkeypatch.setattr(ig, "send_private_reply", _dm)
    monkeypatch.setattr(InstagramCommentPipeline, "_espacar_envio", _sem_espera)

    kwargs = fila[0]["kwargs"]
    resultado = await InstagramCommentPipeline(
        InstagramAutomationRepository(db)
    ).processar_comentario(kwargs["ig_user_id"], kwargs["valor"])

    assert resultado["status"] == "enviado"
    assert dms == ["a"]
    evento = db.query(InstagramEvent).filter(InstagramEvent.comment_id == "a").one()
    assert evento.automation_id == automacao.id
    assert evento.commenter_id == "p1"


# --------------------------------------------------------------------------- #
#  Registro do que não pode mais receber (17/09/2026)                          #
# --------------------------------------------------------------------------- #
#
# O contador do card ("comentários com a palavra-chave") conta eventos. O que o
# webhook nunca entregou não virou evento, e a aluna via 4 onde havia dezenas.
# O retroativo registra o que o pipeline TERIA registrado: expirado (janela de
# 7 dias) e duplicado (a pessoa já recebeu). Nunca registra "enviado" sem envio.


def _cenario_contador(db, conexao, monkeypatch, status="ativa"):
    automacao = _automacao(db, conexao, status=status)
    _evento(db, "ja-enviado", DM_ENVIADO, commenter_id="p9", automation_id=automacao.id)
    _usar_graph(
        monkeypatch,
        [[
            _c("ja-enviado", pessoa="p9", horas_atras=30),
            _c("elegivel", pessoa="p1", horas_atras=5),
            _c("mesma-pessoa-no-lote", pessoa="p1", horas_atras=2),
            _c("expirado", pessoa="p2", horas_atras=24 * 9),
            _c("repetido-de-quem-recebeu", pessoa="p9", horas_atras=3),
            _c("sem-palavra", pessoa="p3", texto="lindo"),
        ]],
    )
    return automacao


def _contador(db, automacao):
    return InstagramAutomationRepository(db).contadores_por_automacao(1)[automacao.id]


@pytest.mark.asyncio
async def test_envio_registra_expirados_e_repetidos_e_o_contador_sobe(db, conexao, monkeypatch, fila):
    automacao = _cenario_contador(db, conexao, monkeypatch)
    assert _contador(db, automacao) == {"comentarios": 1, "directs": 1}

    resposta = await _servico(db).enviar(1, automacao.id)

    assert resposta.enfileirados == 1
    assert resposta.registrados_expirados == 1
    assert resposta.registrados_duplicados == 1
    por_id = {e.comment_id: e for e in db.query(InstagramEvent).all()}
    assert por_id["expirado"].dm_status == "expirado"
    assert por_id["expirado"].automation_id == automacao.id
    assert por_id["expirado"].erro_codigo == retro.CODIGO_RECONCILIADO_JANELA
    assert por_id["repetido-de-quem-recebeu"].dm_status == "duplicado"
    # O repetido DENTRO do lote não é registrado: o direct da pessoa ainda nem
    # saiu, e se falhar o comentário seguinte dela tem que poder tentar.
    assert "mesma-pessoa-no-lote" not in por_id
    assert "sem-palavra" not in por_id
    assert "elegivel" not in por_id, "o elegível é do pipeline, não do registro"
    # Directs só sobem com envio de verdade.
    assert _contador(db, automacao) == {"comentarios": 3, "directs": 1}


@pytest.mark.asyncio
async def test_reconciliar_de_admin_registra_sem_enviar_e_aceita_pausada(db, conexao, monkeypatch, fila):
    automacao = _cenario_contador(db, conexao, monkeypatch, status="pausada")

    resposta = await _servico(db).reconciliar_admin(automacao.id)

    assert fila == [], "reconciliar nunca envia direct"
    assert resposta.registrados_expirados == 1
    assert resposta.registrados_duplicados == 1
    assert resposta.previa.elegiveis == 1
    assert _contador(db, automacao) == {"comentarios": 3, "directs": 1}


@pytest.mark.asyncio
async def test_reconciliar_duas_vezes_nao_conta_em_dobro(db, conexao, monkeypatch, fila):
    automacao = _cenario_contador(db, conexao, monkeypatch, status="pausada")

    await _servico(db).reconciliar_admin(automacao.id)
    segunda = await _servico(db).reconciliar_admin(automacao.id)

    assert segunda.registrados_expirados == 0
    assert segunda.registrados_duplicados == 0
    assert _contador(db, automacao) == {"comentarios": 3, "directs": 1}


@pytest.mark.asyncio
async def test_previa_de_admin_acha_automacao_de_qualquer_conta(db, conexao, monkeypatch, fila):
    automacao = _automacao(db, conexao)
    _usar_graph(monkeypatch, [[_c("a")]])

    previa = await _servico(db).previa_admin(automacao.id)

    assert previa.elegiveis == 1


@pytest.mark.asyncio
async def test_admin_com_automacao_inexistente_da_404(db, conexao, monkeypatch, fila):
    with pytest.raises(HTTPException) as exc:
        await _servico(db).previa_admin(999)
    assert exc.value.status_code == 404
