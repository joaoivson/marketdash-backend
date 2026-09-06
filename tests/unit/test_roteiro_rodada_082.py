"""
Rodada de Roteiros (06/09/2026) contra Postgres real.

O invariante que dá nome ao arquivo: **salvar um roteiro agendado não pode
apagar a fila.** Em 06/09, às 12:04:39, um `PUT /passos` deletou e recriou os
passos; `roteiro_mensagens.passo_id` é `ON DELETE CASCADE`, então o CASCADE
levou junto a mensagem que sairia às 12:05. A execução virou `concluida` com
`total = 0` e nada avisou ninguém — e no mesmo minuto o chip da listagem ainda
dizia "Rascunho", o que fez o mesmo roteiro ser agendado três vezes.

Os outros invariantes:
  * passo já enviado não se edita, não se move e não se exclui;
  * alteração reagenda SÓ as pendentes — o que já saiu não é tocado;
  * uma execução ativa por roteiro;
  * passo no passado bloqueia salvar E agendar, apontando quais;
  * passo com N blocos sai em sequência, com pausa entre eles, e RETOMA do
    bloco que falhou em vez de repetir os anteriores no grupo;
  * duplicar copia datas e blocos, e nasce sem status.
"""
import uuid
from datetime import date, datetime, time as time_t, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

PG_URL = "postgresql://dashads_user:dashads_password@localhost:5434/dashads_db"

try:
    _probe = create_engine(PG_URL, pool_pre_ping=True)
    with _probe.connect() as _c:
        _c.execute(text("SELECT 1"))
    PG_OK = True
except Exception:
    PG_OK = False

pytestmark = pytest.mark.skipif(not PG_OK, reason="Postgres local (5434) indisponível")

if PG_OK:
    from app.db.base import Base
    import app.models  # noqa: F401  (registra tudo no metadata)
    ENGINE = create_engine(PG_URL)
    Base.metadata.create_all(ENGINE)
    with ENGINE.begin() as _conn:
        # `create_all` cria tabela nova (passo_blocos) mas NÃO altera tabela
        # existente — o mesmo gotcha de produção que a 082 documenta.
        for _alter in (
            "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS whatsapp_envio_config JSONB",
            "ALTER TABLE whatsapp_grupos ADD COLUMN IF NOT EXISTS "
            "ativado BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS "
            "envio_pausado BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS pausado_em TIMESTAMPTZ",
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS proxy_id INTEGER",
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS proxy_fixado_em TIMESTAMPTZ",
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS "
            "proxy_trocas INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS servidor_id INTEGER",
            "ALTER TABLE roteiro_passos ADD COLUMN IF NOT EXISTS offset_segundos INTEGER",
            "ALTER TABLE roteiro_passos ADD COLUMN IF NOT EXISTS offset_unidade VARCHAR(10)",
            "ALTER TABLE roteiro_passos ADD COLUMN IF NOT EXISTS "
            "acao_descontinuada BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE roteiro_mensagens ADD COLUMN IF NOT EXISTS "
            "blocos_enviados INTEGER NOT NULL DEFAULT 0",
        ):
            _conn.execute(text(_alter))
    Sessao = sessionmaker(bind=ENGINE)

from app.core.config import settings  # noqa: E402
from app.models.roteiro import (  # noqa: E402
    EXEC_AGENDADA, EXEC_CONCLUIDA, EXEC_ENVIANDO, MSG_ENVIADA, MSG_FALHOU,
    MSG_PENDENTE, MSG_PULADA, PassoBloco, Roteiro, RoteiroExecucao,
    RoteiroMensagem, RoteiroPasso,
)
from app.models.subscription import Subscription  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_settings import UserSettings  # noqa: E402
from app.models.whatsapp_grupos import (  # noqa: E402
    WhatsappGrupo, WhatsappGrupoInstancia, WhatsappInstancia,
)
from app.schemas.roteiros import BlocoIn, PassoIn  # noqa: E402
from app.services.roteiro_envio_service import RoteiroEnvioService  # noqa: E402
from app.services.roteiro_service import (  # noqa: E402
    ExecucaoJaAtiva, PassoJaEnviado, PassosNoPassado, RoteiroService,
)
from app.services.waha_client import ErroWhatsapp  # noqa: E402

JANELA_24H = {"ativo": True, "dias": {str(i): {"ativo": True,
              "inicio": "00:00", "fim": "23:59"} for i in range(7)}}

HOJE = None   # resolvido por teste — o dia civil BRT importa


@pytest.fixture
def db():
    sessao = Sessao()
    yield sessao
    sessao.rollback()
    sessao.close()


@pytest.fixture(autouse=True)
def teto_global_fora_do_caminho(monkeypatch):
    """Banco de teste compartilhado acumula `roteiro_mensagens` do dia inteiro;
    passado o teto, TODOS os testes falhariam com '0 enviadas'."""
    monkeypatch.setattr(settings, "WHATSAPP_CAMPANHA_TETO_GLOBAL_DIA", 10 ** 9)
    monkeypatch.setattr(settings, "WHATSAPP_BLOCO_PAUSA_MIN_S", 0.0)
    monkeypatch.setattr(settings, "WHATSAPP_BLOCO_PAUSA_MAX_S", 0.0)


class _FakeWaha:
    def __init__(self, erro_no_envio=None):
        self.enviadas = []
        self.renomeados = []
        self.descricoes = []
        self.imagens = []
        #: n-ésimo envio (1-based) que deve falhar.
        self.erro_no_envio = erro_no_envio
        self._n = 0

    def _talvez_falhar(self):
        self._n += 1
        if self.erro_no_envio == self._n:
            raise ErroWhatsapp("timeout", "fake")

    def enviar_texto(self, chat_id, texto):
        self._talvez_falhar()
        self.enviadas.append(("texto", chat_id, texto))
        return {"ok": True}

    def enviar_imagem(self, chat_id, url, legenda=""):
        self._talvez_falhar()
        self.enviadas.append(("imagem", chat_id, url, legenda))
        return {"ok": True}

    def renomear_grupo(self, jid, nome):
        self.renomeados.append((jid, nome))

    def alterar_descricao(self, jid, descricao):
        self.descricoes.append((jid, descricao))

    def alterar_imagem(self, jid, url):
        self.imagens.append((jid, url))


def _servico(db, cliente, dormir=None):
    return RoteiroEnvioService(db, dormir=dormir or (lambda s: None),
                               cliente_factory=lambda nome: cliente,
                               short_link_factory=lambda u, url, sid: "https://s.ee/x")


def _amanha():
    from app.services.janela_envio_service import BRT
    return (datetime.now(BRT) + timedelta(days=1)).date()


def _base(db, n_grupos=1):
    """Usuária MAX, um número conectado, N grupos aptos, e um roteiro vazio."""
    suf = uuid.uuid4().hex[:8]
    user = User(email=f"r82-{suf}@x.com", hashed_password="x")
    db.add(user); db.flush()
    db.add(Subscription(user_id=user.id, plan="max", is_active=True))
    db.add(UserSettings(user_id=user.id, whatsapp_envio_config=JANELA_24H))
    inst = WhatsappInstancia(user_id=user.id, nome_instancia=f"mkd82{suf}",
                             status="conectada")
    db.add(inst); db.flush()

    grupos = []
    for i in range(n_grupos):
        g = WhatsappGrupo(user_id=user.id, jid=f"12036{i}{suf}@g.us",
                          nome=f"G{i}", ativo=True, ativado=True,
                          permite_envio=True, sou_admin=True)
        db.add(g); db.flush()
        db.add(WhatsappGrupoInstancia(grupo_id=g.id, instancia_id=inst.id,
                                      sou_admin=True))
        grupos.append(g)

    roteiro = Roteiro(user_id=user.id, nome=f"Lançamento {suf}")
    db.add(roteiro); db.flush()
    db.commit()
    return user, roteiro, grupos


def _passo_in(ordem, *, id=None, hora=time_t(23, 30), data=None,
              blocos=(("texto", "oi"),), offset=None, unidade="minutos",
              tipo="mensagem", acao=None, parametro=None):
    relativo = offset is not None
    return PassoIn(
        id=id, ordem=ordem,
        tipo_tempo="relativo" if relativo else "ancora",
        hora_fixa=None if relativo else hora,
        data_fixa=None if relativo else (data or _amanha()),
        offset_valor=offset, offset_unidade=unidade if relativo else None,
        tipo_conteudo=tipo,
        blocos=[BlocoIn(tipo=t, conteudo=c) for t, c in blocos]
        if tipo == "mensagem" else [],
        acao=acao, acao_parametro=parametro,
    )


# --- 🔴 O bug de 06/09: salvar apagava a fila ---------------------------------

def test_salvar_roteiro_agendado_nao_apaga_as_mensagens_pendentes(db):
    """A regressão que custou o lançamento de 06/09.

    Passo 1 saiu às 12:00. Às 12:04:39 ela salvou o roteiro para acrescentar um
    passo — e o `DELETE` dos passos levou junto, por CASCADE, a mensagem do
    passo 2 agendada para 12:05. Vinte e um segundos depois o tick achou zero
    pendentes e concluiu a execução com `total = 0`.
    """
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [
        _passo_in(1, hora=time_t(23, 30)),
        _passo_in(2, offset=5),
    ])
    execucao, _ = servico.agendar(roteiro)
    ids_antes = {m.id for m in db.query(RoteiroMensagem)
                 .filter(RoteiroMensagem.execucao_id == execucao.id)}
    assert len(ids_antes) == 2

    # O passo 1 sai. Só depois ela edita, para acrescentar o passo 3.
    primeira = (db.query(RoteiroMensagem)
                .filter(RoteiroMensagem.execucao_id == execucao.id)
                .order_by(RoteiroMensagem.agendado_para).first())
    primeira.status = MSG_ENVIADA
    primeira.enviado_em = datetime.now(timezone.utc)
    db.commit()

    passos = servico.repo.passos(roteiro.id)
    servico.definir_passos(roteiro, [
        _passo_in(1, id=passos[0].id, hora=time_t(23, 30)),
        _passo_in(2, id=passos[1].id, offset=5),
        _passo_in(3, offset=5),                       # o passo novo
    ])

    db.expire_all()
    vivas = (db.query(RoteiroMensagem)
             .filter(RoteiroMensagem.execucao_id == execucao.id).all())
    # A do passo 1 (enviada) e a do passo 2 (pendente) SOBREVIVEM; a do 3 nasce.
    assert ids_antes.issubset({m.id for m in vivas})
    assert len(vivas) == 3
    assert sorted(m.status for m in vivas) == [MSG_ENVIADA, MSG_PENDENTE, MSG_PENDENTE]
    assert db.query(RoteiroExecucao).get(execucao.id).status == EXEC_AGENDADA


def test_passo_novo_depois_do_agendamento_entra_na_fila_com_horario_certo(db):
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [_passo_in(1, hora=time_t(23, 0))])
    execucao, _ = servico.agendar(roteiro)

    passos = servico.repo.passos(roteiro.id)
    servico.definir_passos(roteiro, [
        _passo_in(1, id=passos[0].id, hora=time_t(23, 0)),
        _passo_in(2, offset=30),
    ])
    db.expire_all()
    linhas = sorted(
        db.query(RoteiroMensagem).filter(RoteiroMensagem.execucao_id == execucao.id),
        key=lambda m: m.agendado_para)
    assert len(linhas) == 2
    assert (linhas[1].agendado_para - linhas[0].agendado_para) == timedelta(minutes=30)


def test_editar_passo_pendente_empurra_o_resto_da_cadeia(db):
    """Empurrar o lançamento quando algo atrasa: editar o passo 1 recalcula o 2."""
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [
        _passo_in(1, hora=time_t(20, 0)),
        _passo_in(2, offset=10),
    ])
    execucao, _ = servico.agendar(roteiro)
    passos = servico.repo.passos(roteiro.id)

    servico.definir_passos(roteiro, [
        _passo_in(1, id=passos[0].id, hora=time_t(21, 0)),   # +1h
        _passo_in(2, id=passos[1].id, offset=10),
    ])
    db.expire_all()
    linhas = sorted(
        db.query(RoteiroMensagem).filter(RoteiroMensagem.execucao_id == execucao.id),
        key=lambda m: m.agendado_para)
    assert linhas[0].agendado_para.astimezone(
        __import__("app.services.janela_envio_service", fromlist=["BRT"]).BRT
    ).hour == 21
    assert (linhas[1].agendado_para - linhas[0].agendado_para) == timedelta(minutes=10)


# --- 🔴 Editar o que já saiu ---------------------------------------------------

def test_editar_passo_ja_enviado_e_recusado(db):
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [_passo_in(1, hora=time_t(23, 30)),
                                     _passo_in(2, offset=5)])
    execucao, _ = servico.agendar(roteiro)
    primeira = (db.query(RoteiroMensagem)
                .filter(RoteiroMensagem.execucao_id == execucao.id)
                .order_by(RoteiroMensagem.agendado_para).first())
    primeira.status = MSG_ENVIADA
    db.commit()

    passos = servico.repo.passos(roteiro.id)
    with pytest.raises(PassoJaEnviado) as erro:
        servico.definir_passos(roteiro, [
            _passo_in(1, id=passos[0].id, hora=time_t(23, 30),
                      blocos=(("texto", "TEXTO NOVO"),)),
            _passo_in(2, id=passos[1].id, offset=5),
        ])
    assert erro.value.ordens == [1]


def test_excluir_e_mover_passo_ja_enviado_sao_recusados(db):
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [_passo_in(1, hora=time_t(23, 30)),
                                     _passo_in(2, offset=5)])
    execucao, _ = servico.agendar(roteiro)
    primeira = (db.query(RoteiroMensagem)
                .filter(RoteiroMensagem.execucao_id == execucao.id)
                .order_by(RoteiroMensagem.agendado_para).first())
    primeira.status = MSG_ENVIADA
    db.commit()
    passos = servico.repo.passos(roteiro.id)

    with pytest.raises(PassoJaEnviado):        # excluir
        servico.definir_passos(roteiro, [_passo_in(1, id=passos[1].id, offset=5)])
    db.rollback()

    with pytest.raises(PassoJaEnviado):        # mover para o fim
        servico.definir_passos(roteiro, [
            _passo_in(1, id=passos[1].id, hora=time_t(23, 40)),
            _passo_in(2, id=passos[0].id, offset=5),
        ])


# --- 🔴 Agendar duas vezes -----------------------------------------------------

def test_agendar_duas_vezes_e_recusado(db):
    """Em 06/09 o mesmo roteiro foi agendado três vezes em 16 segundos: o chip
    continuava "Rascunho" e o botão "Agendar" continuava na linha."""
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [_passo_in(1)])
    execucao, _ = servico.agendar(roteiro)

    with pytest.raises(ExecucaoJaAtiva) as erro:
        servico.agendar(roteiro)
    assert erro.value.execucao_id == execucao.id

    # E o roteiro para de mentir "rascunho".
    db.expire_all()
    assert db.query(Roteiro).get(roteiro.id).status == "pronto"
    assert servico.execucao_ativa(roteiro.id).id == execucao.id


# --- 🟢 Passo no passado -------------------------------------------------------

def test_passo_no_passado_bloqueia_salvar_e_aponta_quais(db):
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    with pytest.raises(PassosNoPassado) as erro:
        servico.definir_passos(db.query(Roteiro).get(roteiro.id), [
            _passo_in(1, hora=time_t(9, 0), data=date(2020, 1, 1)),
            _passo_in(2, offset=5),
        ])
    assert erro.value.ordens == [1, 2]      # o relativo herda o passado do pai
    db.rollback()
    assert servico.repo.passos(roteiro.id) == []   # nada foi gravado pela metade


def test_ajustar_datas_em_bloco_tira_o_roteiro_do_passado(db):
    """O que torna duplicar barato: em vez de reagendar 22 mensagens, ela troca
    as 4 ou 5 datas fixas e o resto recalcula pelo offset."""
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [_passo_in(1), _passo_in(2, offset=5)])

    copia = servico.duplicar(roteiro)
    passos = servico.repo.passos(copia.id)
    # Simula o lançamento passado: a cópia carrega a data antiga.
    passos[0].data_fixa = date(2020, 1, 1)
    db.commit()
    with pytest.raises(PassosNoPassado):
        servico.agendar(copia)
    db.rollback()

    nova_data = _amanha() + timedelta(days=7)
    servico.ajustar_datas(copia, {passos[0].id: (nova_data, None)})
    db.expire_all()
    execucao, _ = servico.agendar(copia)
    assert execucao is not None and execucao.status == EXEC_AGENDADA


# --- 🟢 Blocos -----------------------------------------------------------------

def test_passo_com_quatro_blocos_sai_em_sequencia_com_pausa_entre_eles(db):
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [_passo_in(1, blocos=(
        ("imagem", "https://img/1.jpg"), ("imagem", "https://img/2.jpg"),
        ("imagem", "https://img/3.jpg"), ("texto", "corre que acaba"),
    ))])
    execucao, _ = servico.agendar(roteiro)
    _adiantar(db, execucao)

    cliente = _FakeWaha()
    pausas = []
    r = _servico(db, cliente, dormir=pausas.append).processar_fatia(execucao.id)

    assert r["enviadas"] == 1        # UMA entrega ao grupo…
    assert [e[0] for e in cliente.enviadas] == [
        "imagem", "imagem", "imagem", "texto"]   # …em quatro blocos, na ordem
    assert len(pausas) == 3          # 2-5s entre blocos, nunca antes do 1º


def test_falha_no_terceiro_bloco_nao_repete_os_dois_primeiros_no_reenvio(db):
    """Mensagem duplicada em grupo é o erro que ela vê e que o WhatsApp pune."""
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [_passo_in(1, blocos=(
        ("texto", "um"), ("texto", "dois"), ("texto", "tres"),
    ))])
    execucao, _ = servico.agendar(roteiro)
    _adiantar(db, execucao)

    cliente = _FakeWaha(erro_no_envio=3)
    _servico(db, cliente).processar_fatia(execucao.id)
    db.expire_all()
    linha = db.query(RoteiroMensagem).filter(
        RoteiroMensagem.execucao_id == execucao.id).one()
    assert linha.status == MSG_FALHOU and linha.blocos_enviados == 2
    assert [e[2] for e in cliente.enviadas] == ["um", "dois"]

    # Reenvio manual: retoma do bloco 3.
    servico.reenviar(roteiro, db.query(RoteiroExecucao).get(execucao.id),
                     linha.passo_id, [grupo.id])
    db.expire_all()
    execucao2 = db.query(RoteiroExecucao).get(execucao.id)
    execucao2.status = EXEC_ENVIANDO
    db.commit()
    cliente2 = _FakeWaha()
    _servico(db, cliente2).processar_fatia(execucao.id)
    assert [e[2] for e in cliente2.enviadas] == ["tres"]   # NUNCA "um"/"dois"


def test_prefixo_e_sufixo_da_campanha_entram_uma_vez_so(db):
    from app.models.campanha_grupos import Campanha, CampanhaGrupo

    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    campanha = Campanha(user_id=user.id, nome="c", prefixo="🔥 OFERTA",
                        sufixo="— link na bio")
    db.add(campanha); db.flush()
    db.add(CampanhaGrupo(campanha_id=campanha.id, grupo_id=grupo.id))
    roteiro.campanha_id = campanha.id
    db.commit()

    servico.definir_passos(roteiro, [_passo_in(1, blocos=(
        ("texto", "um"), ("texto", "dois"), ("texto", "tres")))])
    execucao, _ = servico.agendar(roteiro)
    _adiantar(db, execucao)
    cliente = _FakeWaha()
    _servico(db, cliente).processar_fatia(execucao.id)

    textos = [e[2] for e in cliente.enviadas]
    assert textos[0].startswith("🔥 OFERTA") and textos[-1].endswith("— link na bio")
    # Repetir a assinatura em três mensagens seguidas é padrão de robô.
    assert sum(t.count("🔥 OFERTA") for t in textos) == 1
    assert sum(t.count("— link na bio") for t in textos) == 1


# --- 🟢 Status por passo -------------------------------------------------------

def test_status_do_passo_separa_concluido_com_falhas_de_falhou(db):
    servico = RoteiroService(db)
    user, roteiro, grupos = _base(db, n_grupos=3)
    servico.definir_passos(roteiro, [_passo_in(1), _passo_in(2, offset=5)])
    execucao, _ = servico.agendar(roteiro)
    passos = servico.repo.passos(roteiro.id)

    linhas = db.query(RoteiroMensagem).filter(
        RoteiroMensagem.execucao_id == execucao.id).all()
    do_1 = [m for m in linhas if m.passo_id == passos[0].id]
    do_2 = [m for m in linhas if m.passo_id == passos[1].id]
    for m in do_1:
        m.status = MSG_ENVIADA
    do_1[0].status, do_1[0].erro_motivo = MSG_FALHOU, "sem_admin"
    for m in do_2:
        m.status, m.erro_motivo = MSG_PULADA, "grupo_desativado"
    db.commit()

    por_passo = servico.status_dos_passos(roteiro)["passos"]
    assert por_passo[passos[0].id]["status"] == "concluido_com_falhas"
    assert por_passo[passos[0].id]["falhas"][0]["motivo"] == "Você não é admin deste grupo"
    assert por_passo[passos[1].id]["status"] == "falhou"
    assert {f["motivo"] for f in por_passo[passos[1].id]["falhas"]} == {
        "Grupo desativado por você"}


def test_duplicar_nasce_sem_status_e_com_blocos(db):
    """O roteiro é template: o mesmo vai rodar no próximo lançamento. Sem isso
    ela reusa o roteiro do lançamento passado e ele aparece todo verde antes de
    mandar qualquer coisa."""
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [_passo_in(1, blocos=(
        ("imagem", "https://img/a.jpg"), ("texto", "vai")))])
    execucao, _ = servico.agendar(roteiro)
    for m in db.query(RoteiroMensagem).filter(
            RoteiroMensagem.execucao_id == execucao.id):
        m.status = MSG_ENVIADA
    db.commit()
    assert servico.status_dos_passos(roteiro)["passos"]     # o original TEM status

    copia = servico.duplicar(roteiro)
    assert servico.status_dos_passos(copia) == {"execucao": None, "passos": {}}
    passo_copia = servico.repo.passos(copia.id)[0]
    assert passo_copia.data_fixa is not None                # a data vem junto
    assert [(b.tipo, b.conteudo) for b in servico.repo.blocos(passo_copia.id)] == [
        ("imagem", "https://img/a.jpg"), ("texto", "vai")]


# --- 🟢 Ações novas ------------------------------------------------------------

@pytest.mark.parametrize("acao,parametro,atributo", [
    ("renomear_grupo", "ABERTO 🔓", "renomeados"),
    ("alterar_descricao", "Carrinho aberto até domingo", "descricoes"),
    ("alterar_imagem", "https://img/capa.jpg", "imagens"),
])
def test_acoes_do_grupo_chamam_o_waha(db, acao, parametro, atributo):
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [
        _passo_in(1, tipo="acao_grupo", acao=acao, parametro=parametro)])
    execucao, _ = servico.agendar(roteiro)
    _adiantar(db, execucao)

    cliente = _FakeWaha()
    r = _servico(db, cliente).processar_fatia(execucao.id)
    assert r["enviadas"] == 1
    assert getattr(cliente, atributo) == [(grupo.jid, parametro)]


def test_abrir_e_fechar_entrada_nao_sao_mais_aceitas_ao_salvar(db):
    from pydantic import ValidationError
    for acao in ("abrir_entrada", "fechar_entrada"):
        with pytest.raises(ValidationError) as erro:
            _passo_in(1, tipo="acao_grupo", acao=acao, parametro="x")
        assert "aba Grupos" in str(erro.value)


def test_acao_nao_aceita_blocos(db):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PassoIn(ordem=1, tipo_tempo="ancora", hora_fixa=time_t(9, 0),
                data_fixa=_amanha(), tipo_conteudo="acao_grupo",
                acao="renomear_grupo", acao_parametro="x",
                blocos=[BlocoIn(tipo="texto", conteudo="oi")])


# --- 🟢 Offset com unidade -----------------------------------------------------

def test_offset_em_horas_e_segundos_vai_e_volta_na_unidade_digitada(db):
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [
        _passo_in(1, hora=time_t(20, 0)),
        _passo_in(2, offset=2, unidade="horas"),
        _passo_in(3, offset=90, unidade="segundos"),
    ])
    passos = servico.repo.passos(roteiro.id)
    assert (passos[1].offset_segundos, passos[1].offset_unidade) == (7200, "horas")
    assert (passos[2].offset_segundos, passos[2].offset_unidade) == (90, "segundos")

    from app.services.roteiro_service import segundos_para_offset
    # 90s tem que voltar "+90 segundos", nunca "+1,5 min".
    assert segundos_para_offset(90, "segundos") == (90, "segundos")
    assert segundos_para_offset(7200, "horas") == (2, "horas")


def _adiantar(db, execucao):
    """Traz a execução para AGORA e a coloca em `enviando` — os testes de motor
    não esperam o tick de 5 minutos."""
    db.query(RoteiroMensagem).filter(
        RoteiroMensagem.execucao_id == execucao.id,
        RoteiroMensagem.status == MSG_PENDENTE,
    ).update({"agendado_para": datetime.now(timezone.utc) - timedelta(seconds=60)},
             synchronize_session=False)
    e = db.query(RoteiroExecucao).get(execucao.id)
    e.status = EXEC_ENVIANDO
    e.iniciado_em = datetime.now(timezone.utc)
    db.commit()


# --- Regressões achadas na auditoria da própria rodada -------------------------

def test_editar_roteiro_continua_possivel_depois_que_o_passo_1_saiu(db):
    """A trava de passado NÃO pode contar o passo que já foi entregue.

    Contá-lo congelava o roteiro na segunda hora do lançamento: passo 1 sai às
    12:00, e às 12:10 qualquer `salvar` era recusado com `PassosNoPassado`
    apontando justo o passo que a regra nem deixa editar. Ou seja: a
    funcionalidade que esta rodada existe para entregar ficava impossível.

    Os testes anteriores não pegavam porque o helper agenda tudo para AMANHÃ —
    aqui o passo 1 fica mesmo no passado, como fica na vida real.
    """
    from app.services.janela_envio_service import BRT

    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [_passo_in(1, hora=time_t(23, 50)),
                                     _passo_in(2, offset=30)])
    execucao, _ = servico.agendar(roteiro)
    passos = servico.repo.passos(roteiro.id)

    # O passo 1 sai — e o relógio anda: ele passa a estar no PASSADO. É a forma
    # real de um lançamento no meio do caminho: âncoras já entregues atrás,
    # âncoras futuras à frente.
    ontem = (datetime.now(BRT) - timedelta(days=1)).date()
    passos[0].data_fixa = ontem
    passos[1].tipo_tempo = "ancora"          # o passo 2 vira âncora, amanhã
    passos[1].hora_fixa = time_t(10, 0)
    passos[1].data_fixa = _amanha()
    passos[1].offset_segundos = None
    passos[1].offset_unidade = None
    db.commit()
    primeira = (db.query(RoteiroMensagem)
                .filter(RoteiroMensagem.execucao_id == execucao.id,
                        RoteiroMensagem.passo_id == passos[0].id).one())
    primeira.status = MSG_ENVIADA
    primeira.enviado_em = datetime.now(timezone.utc)
    db.commit()

    # Acrescentar um passo no fim continua funcionando — antes, o passo 1 (que
    # ela nem pode editar) fazia o salvar inteiro ser recusado.
    servico.definir_passos(roteiro, [
        _passo_in(1, id=passos[0].id, hora=time_t(23, 50), data=ontem),
        _passo_in(2, id=passos[1].id, hora=time_t(10, 0), data=_amanha()),
        _passo_in(3, offset=10),
    ])
    db.expire_all()
    assert len(servico.repo.passos(roteiro.id)) == 3
    assert db.query(RoteiroExecucao).get(execucao.id).status == EXEC_AGENDADA

    # E o passo AINDA PENDENTE que cair no passado continua sendo recusado —
    # apontando SÓ ele, nunca o que já foi entregue.
    with pytest.raises(PassosNoPassado) as erro:
        servico.definir_passos(roteiro, [
            _passo_in(1, id=passos[0].id, hora=time_t(23, 50), data=ontem),
            _passo_in(2, id=passos[1].id, hora=time_t(9, 0), data=ontem),
        ])
    assert erro.value.ordens == [2]


def test_passo_com_linha_pendente_nao_tem_status(db):
    """"Antes de rodar, o passo NÃO tem status." Linhas `pulado` nascem na
    MATERIALIZAÇÃO (grupo desativado, sem admin), horas antes de disparar — sem
    a guarda o passo aparecia "Falhou" no instante do agendamento."""
    servico = RoteiroService(db)
    user, roteiro, grupos = _base(db, n_grupos=2)
    grupos[1].ativado = False           # nasce `pulado` já no agendar
    db.commit()
    servico.definir_passos(roteiro, [_passo_in(1)])
    servico.agendar(roteiro)

    assert servico.status_dos_passos(roteiro)["passos"] == {}


def test_excluir_passo_com_historico_de_execucao_concluida_e_recusado(db):
    """O CASCADE apagaria as linhas `enviada` de lançamentos passados. Apagar o
    passo é decisão dela; reescrever o que já aconteceu no grupo, não."""
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [_passo_in(1), _passo_in(2, offset=5)])
    execucao, _ = servico.agendar(roteiro)
    linhas = db.query(RoteiroMensagem).filter(
        RoteiroMensagem.execucao_id == execucao.id).all()
    for m in linhas:
        m.status = MSG_ENVIADA
    execucao.status = EXEC_CONCLUIDA     # a execução TERMINOU
    db.commit()

    passos = servico.repo.passos(roteiro.id)
    with pytest.raises(PassoJaEnviado):
        servico.definir_passos(roteiro, [_passo_in(1, id=passos[0].id)])


def test_bloco_sem_conteudo_no_banco_nao_derruba_a_leitura(db):
    """A 082 converte passo de texto SEM texto num bloco de `conteudo` NULL.
    `BlocoOut` herdando o validator de escrita respondia 500 no GET."""
    from app.api.v1.routes.roteiros import _bloco_out

    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [_passo_in(1)])
    passo = servico.repo.passos(roteiro.id)[0]
    bloco = servico.repo.blocos(passo.id)[0]
    bloco.conteudo = None                # como a migration deixaria
    db.commit()

    saida = _bloco_out(bloco)            # não pode levantar
    assert saida.conteudo is None and saida.tipo == "texto"


def test_passo_de_oferta_nao_aceita_acao(db):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PassoIn(ordem=1, tipo_tempo="ancora", hora_fixa=time_t(9, 0),
                data_fixa=_amanha(), tipo_conteudo="oferta",
                oferta_url="https://x.com", acao="renomear_grupo")


def test_trocar_os_blocos_zera_a_retomada_por_bloco(db):
    """`blocos_enviados` é POSICIONAL. Uma linha que parou no bloco 2 de uma
    lista antiga retomaria do "bloco 3" de uma lista nova — outro conteúdo."""
    servico = RoteiroService(db)
    user, roteiro, (grupo,) = _base(db)
    servico.definir_passos(roteiro, [_passo_in(1, blocos=(
        ("texto", "um"), ("texto", "dois"), ("texto", "tres")))])
    execucao, _ = servico.agendar(roteiro)
    linha = db.query(RoteiroMensagem).filter(
        RoteiroMensagem.execucao_id == execucao.id).one()
    linha.status = MSG_FALHOU
    linha.blocos_enviados = 2
    db.commit()

    passo = servico.repo.passos(roteiro.id)[0]
    servico.definir_passos(roteiro, [_passo_in(1, id=passo.id, blocos=(
        ("texto", "OUTRO um"), ("texto", "OUTRO dois")))])
    db.expire_all()
    assert db.query(RoteiroMensagem).get(linha.id).blocos_enviados == 0
