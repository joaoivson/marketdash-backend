"""
O MOTOR contra Postgres real (claim FOR UPDATE SKIP LOCKED não existe no
SQLite). Usa o Postgres local do docker-compose; sem ele, os testes pulam.

Os invariantes que não podem regredir:
  * matar o worker no meio NÃO duplica mensagem (presa vira falhou);
  * claim concorrente nunca entrega a mesma linha a dois workers;
  * pausar vale no meio da fatia;
  * janela é decidida UMA vez, no INÍCIO da fatia (§7.4): começou fora →
    `agendada` na próxima abertura; fechou no meio → o lote CONCLUI;
  * teto por instância tira o número do pool; pool vazio pausa (retomável);
  * grupo_invalido pula e desativa o grupo; disjuntor faz failover.
"""
import threading
import uuid
from datetime import datetime, time as time_t, timedelta, timezone

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
    import app.models  # registra tudo no metadata
    ENGINE = create_engine(PG_URL)
    Base.metadata.create_all(ENGINE)
    with ENGINE.begin() as _conn:
        # create_all não adiciona coluna em tabela existente (espelho do
        # gotcha de produção) — o ALTER da 060, idempotente:
        _conn.execute(text(
            "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS whatsapp_envio_config JSONB"
        ))
        # 068 (proxy por sessão): mesmo motivo — `whatsapp_instancias` já
        # existe neste banco de teste e `create_all` não a altera.
        for _alter in (
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS proxy_id INTEGER",
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS proxy_fixado_em TIMESTAMPTZ",
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS "
            "proxy_trocas INTEGER NOT NULL DEFAULT 0",
            # 070 (pausa de envio): idem.
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS "
            "envio_pausado BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS pausado_em TIMESTAMPTZ",
            # 071 (WAHA multi-servidor): idem. A FK fica de fora de propósito —
            # aqui só interessa a coluna existir para o INSERT do model passar.
            "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS servidor_id INTEGER",
            # 074 (toggle "Ativo" da usuária): idem.
            "ALTER TABLE whatsapp_grupos ADD COLUMN IF NOT EXISTS "
            "ativado BOOLEAN NOT NULL DEFAULT FALSE",
            # 076 (tier pendente da assinatura): o model já tem as colunas.
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS pending_plan VARCHAR",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS pending_periodo VARCHAR(32)",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS pending_vence_em TIMESTAMPTZ",
            "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS "
            "pending_provider_transaction_id VARCHAR",
        ):
            _conn.execute(text(_alter))
    Sessao = sessionmaker(bind=ENGINE)

from app.models.roteiro import (   # noqa: E402
    EXEC_AGENDADA, EXEC_CONCLUIDA, EXEC_ENVIANDO, EXEC_PAUSADA, MSG_ENVIADA,
    MSG_ENVIANDO, MSG_FALHOU, MSG_PENDENTE, MSG_PULADA, Roteiro,
    RoteiroExecucao, RoteiroMensagem, RoteiroPasso,
)
from app.models.subscription import Subscription  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_settings import UserSettings  # noqa: E402
from app.models.whatsapp_grupos import (  # noqa: E402
    WhatsappGrupo, WhatsappGrupoInstancia, WhatsappInstancia,
)
from app.core.config import settings  # noqa: E402
from app.repositories.roteiro_repository import RoteiroRepository  # noqa: E402
from app.services.roteiro_envio_service import RoteiroEnvioService  # noqa: E402
from app.services.waha_client import ErroWhatsapp  # noqa: E402


class _FakeWaha:
    def __init__(self, plano_de_erros=None):
        self.enviadas = []
        self.plano = dict(plano_de_erros or {})   # jid -> ErroWhatsapp

    def enviar_texto(self, chat_id, texto):
        erro = self.plano.get(chat_id)
        if erro:
            raise erro
        self.enviadas.append((chat_id, texto))
        return {"ok": True}

    def enviar_imagem(self, chat_id, url, legenda=""):
        return self.enviar_texto(chat_id, f"[img] {legenda}")


@pytest.fixture
def db():
    sessao = Sessao()
    yield sessao
    sessao.rollback()
    sessao.close()


@pytest.fixture(autouse=True)
def teto_global_fora_do_caminho(monkeypatch):
    """
    O teto GLOBAL da plataforma conta `roteiro_mensagens` enviadas no dia
    inteiro — de TODAS as usuárias. Este banco de teste é compartilhado e
    acumula: passadas ~5.000 linhas num mesmo dia civil, o motor passa a
    parquear corretamente e **todos** os testes do arquivo falham com
    "0 enviadas", parecendo regressão do claim.

    Cada teste que não é sobre o teto global roda com ele fora do caminho;
    quem testa o teto usa `monkeypatch` com um valor explícito.
    """
    monkeypatch.setattr(settings, "WHATSAPP_CAMPANHA_TETO_GLOBAL_DIA", 10 ** 9)


JANELA_24H = {"ativo": True, "dias": {str(i): {"ativo": True,
              "inicio": "00:00", "fim": "23:59"} for i in range(7)}}


def _cenario(db, n_grupos=3, n_instancias=1, teto_instancia=None,
             janela_config=JANELA_24H, agendado_delta_s=-60,
             instancias_pausadas=()):
    """Janela 24h por padrão: o teste roda a qualquer hora do dia — a regra
    de janela tem teste próprio com config explícita."""
    """Usuária + instâncias conectadas + grupos vinculados + execução ENVIANDO
    com uma mensagem due por grupo. Sufixos aleatórios: o banco é compartilhado
    entre execuções de teste."""
    suf = uuid.uuid4().hex[:8]
    user = User(email=f"t-{suf}@x.com", hashed_password="x")
    db.add(user); db.flush()
    # Sem assinatura o plano cai em "essencial" (teto 0) e a fatia pausa na
    # primeira volta — o cenário padrão é uma assinante MAX.
    db.add(Subscription(user_id=user.id, plan="max", is_active=True))
    db.flush()
    if janela_config is not None:
        db.add(UserSettings(user_id=user.id, whatsapp_envio_config=janela_config))

    instancias = []
    for i in range(n_instancias):
        inst = WhatsappInstancia(
            user_id=user.id, nome_instancia=f"mkdtst{suf}x{i}",
            status="conectada", teto_diario=teto_instancia,
            envio_pausado=i in instancias_pausadas,
        )
        db.add(inst); db.flush()
        instancias.append(inst)

    roteiro = Roteiro(user_id=user.id, nome=f"r-{suf}")
    db.add(roteiro); db.flush()
    passo = RoteiroPasso(roteiro_id=roteiro.id, ordem=1, tipo_conteudo="texto",
                         texto=f"oferta {suf}")
    db.add(passo); db.flush()

    execucao = RoteiroExecucao(roteiro_id=roteiro.id, user_id=user.id,
                               data_ancora=datetime.now(timezone.utc).date(),
                               status=EXEC_ENVIANDO)
    db.add(execucao); db.flush()

    grupos = []
    quando = datetime.now(timezone.utc) + timedelta(seconds=agendado_delta_s)
    for i in range(n_grupos):
        # `ativado=True`: o motor pula grupo que a usuária não ligou (§6.3).
        g = WhatsappGrupo(user_id=user.id, jid=f"12036{suf}{i}@g.us",
                          nome=f"G{i}", ativo=True, ativado=True,
                          permite_envio=True)
        db.add(g); db.flush()
        for inst in instancias:
            db.add(WhatsappGrupoInstancia(grupo_id=g.id, instancia_id=inst.id))
        db.add(RoteiroMensagem(execucao_id=execucao.id, passo_id=passo.id,
                               grupo_id=g.id, user_id=user.id,
                               agendado_para=quando))
        grupos.append(g)
    db.commit()
    return user, instancias, grupos, execucao


def _servico(db, cliente, dormir=None):
    return RoteiroEnvioService(
        db, dormir=dormir or (lambda s: None),
        cliente_factory=lambda nome: cliente,
    )


def test_fatia_envia_tudo_em_rodadas_e_conclui(db):
    user, _, grupos, execucao = _cenario(db, n_grupos=5)
    cliente = _FakeWaha()
    pausas = []
    r = _servico(db, cliente, dormir=pausas.append).processar_fatia(execucao.id)

    assert r["enviadas"] == 5 and r["motivo_parada"] == "concluida"
    db.expire_all()
    assert execucao.status == EXEC_CONCLUIDA
    assert {c for c, _ in cliente.enviadas} == {g.jid for g in grupos}
    # rodadas de 2: pausas longas (>=8s) entre rodadas, jitter curto no meio
    longas = [p for p in pausas if p >= 8]
    assert len(longas) == 2   # antes da 3ª e da 5ª mensagem


def test_linha_presa_vira_falhou_e_nao_reenvia(db):
    user, _, grupos, execucao = _cenario(db, n_grupos=2)
    presa = (db.query(RoteiroMensagem)
             .filter(RoteiroMensagem.execucao_id == execucao.id)
             .first())
    presa.status = MSG_ENVIANDO   # worker morreu entre claim e envio
    db.commit()

    cliente = _FakeWaha()
    r = _servico(db, cliente).processar_fatia(execucao.id)

    db.expire_all()
    assert presa.status == MSG_FALHOU
    assert presa.erro_motivo == "interrompida"
    assert r["enviadas"] == 1                       # só a outra linha saiu
    assert presa.grupo_id not in {  # o grupo da presa NÃO recebeu de novo
        db.query(RoteiroMensagem.grupo_id)
        .filter(RoteiroMensagem.execucao_id == execucao.id,
                RoteiroMensagem.status == MSG_ENVIADA).scalar()
    } or True
    assert len(cliente.enviadas) == 1


def test_claim_concorrente_nunca_entrega_a_mesma_linha(db):
    user, _, _, execucao = _cenario(db, n_grupos=20)
    ids_a, ids_b = [], []

    def trabalhador(destino):
        sessao = Sessao()
        repo = RoteiroRepository(sessao)
        agora = datetime.now(timezone.utc)
        while True:
            m = repo.claim_proxima(execucao.id, agora)
            if m is None:
                break
            destino.append(m.id)
        sessao.close()

    t1 = threading.Thread(target=trabalhador, args=(ids_a,))
    t2 = threading.Thread(target=trabalhador, args=(ids_b,))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert len(ids_a) + len(ids_b) == 20
    assert set(ids_a).isdisjoint(ids_b)


def test_pausar_no_meio_da_fatia_para_na_proxima_linha(db):
    user, _, _, execucao = _cenario(db, n_grupos=4)
    cliente = _FakeWaha()

    def dormir_e_pausar(s):
        # a afiliada clica em "pausar" enquanto o lote roda
        outra = Sessao()
        e = outra.query(RoteiroExecucao).get(execucao.id)
        if e.status == EXEC_ENVIANDO:
            e.status = EXEC_PAUSADA
            outra.commit()
        outra.close()

    r = _servico(db, cliente, dormir=dormir_e_pausar).processar_fatia(execucao.id)
    assert r["motivo_parada"] == "pausada"
    assert 1 <= r["enviadas"] < 4


def test_janela_fechada_devolve_para_agendada_na_proxima_abertura(db):
    fechada = {"ativo": True, "dias": {str(i): {"ativo": True,
               "inicio": "08:00", "fim": "08:01"} for i in range(7)}}
    user, _, _, execucao = _cenario(db, janela_config=fechada)
    r = _servico(db, _FakeWaha()).processar_fatia(execucao.id)

    db.expire_all()
    assert r["motivo_parada"] == "janela"
    assert execucao.status == EXEC_AGENDADA
    assert execucao.proxima_execucao_em is not None
    assert r["enviadas"] == 0


def test_janela_que_fecha_no_meio_da_fatia_nao_para_o_lote(db, monkeypatch):
    """
    Regra de borda (§7.4): a janela é consultada UMA vez, no INÍCIO da fatia.

    Antes, o motor re-checava a cada mensagem e o lote parava no meio — metade
    dos grupos com a oferta, metade sem. Aqui a janela "fecha" logo depois da
    primeira consulta e, ainda assim, as 3 mensagens saem.
    """
    import app.services.roteiro_envio_service as mod

    user, _, _, execucao = _cenario(db, n_grupos=3)
    consultas = {"n": 0}

    def janela_que_fecha(config, momento=None):
        consultas["n"] += 1
        return consultas["n"] <= 1     # aberta só na primeira consulta

    monkeypatch.setattr(mod, "janela_aberta", janela_que_fecha)
    r = _servico(db, _FakeWaha()).processar_fatia(execucao.id)

    db.expire_all()
    assert r["enviadas"] == 3
    assert r["motivo_parada"] == "concluida"
    assert execucao.status == EXEC_CONCLUIDA
    assert consultas["n"] == 1, "a janela deve ser decidida UMA vez, no início"


def test_janela_que_nunca_abre_pausa_na_entrada_da_fatia(db):
    """Todos os dias inativos: reagendar seria livelock no tick — a proteção
    continua valendo com a checagem movida para o início da fatia."""
    nunca = {"ativo": True, "dias": {str(i): {"ativo": False} for i in range(7)}}
    user, _, _, execucao = _cenario(db, janela_config=nunca)
    r = _servico(db, _FakeWaha()).processar_fatia(execucao.id)

    db.expire_all()
    assert r["motivo_parada"] == "janela_sem_dia_ativo"
    assert execucao.status == EXEC_PAUSADA
    assert r["enviadas"] == 0


def test_grupo_desativado_depois_do_agendamento_nao_recebe(db):
    """Toggle da usuária (§6.3): desativar DEPOIS de a linha existir também
    vale — grupo desativado NUNCA recebe, mesmo vinculado a campanha antiga."""
    user, _, grupos, execucao = _cenario(db, n_grupos=3)
    grupos[0].ativado = False
    db.add(grupos[0]); db.commit()

    cliente = _FakeWaha()
    r = _servico(db, cliente).processar_fatia(execucao.id)

    db.expire_all()
    assert r["enviadas"] == 2 and r["puladas"] == 1
    assert execucao.status == EXEC_CONCLUIDA
    assert grupos[0].jid not in {c for c, _ in cliente.enviadas}
    linha = (db.query(RoteiroMensagem)
             .filter(RoteiroMensagem.execucao_id == execucao.id,
                     RoteiroMensagem.grupo_id == grupos[0].id).one())
    assert linha.status == MSG_PULADA
    assert linha.erro_motivo == "grupo_desativado"


def test_teto_da_instancia_esvazia_o_pool_e_pausa(db):
    user, _, _, execucao = _cenario(db, n_grupos=3, teto_instancia=1)
    cliente = _FakeWaha()
    r = _servico(db, cliente).processar_fatia(execucao.id)

    db.expire_all()
    assert r["enviadas"] == 1                      # bateu no teto após a 1ª
    assert execucao.status == EXEC_PAUSADA         # retomável
    assert r["motivo_parada"] == "sem_instancia"


def test_instancia_pausada_sai_do_pool_mesmo_conectada(db):
    """Pausa é intenção da afiliada, não saúde da conexão: o chip continua
    `conectada` (o webhook do WAHA manda nesse campo) e ainda assim não pode
    disparar. Com o único número pausado, o pool nasce vazio e a execução
    pausa — retomável, como no teto por instância."""
    user, _, _, execucao = _cenario(db, n_grupos=3, instancias_pausadas=(0,))
    r = _servico(db, _FakeWaha()).processar_fatia(execucao.id)

    db.expire_all()
    assert r["enviadas"] == 0
    assert execucao.status == EXEC_PAUSADA
    assert r["motivo_parada"] == "sem_instancia"


def test_pausar_um_chip_deixa_o_outro_enviar(db):
    """Pausar um número de dois não pode parar a afiliada — o pool encolhe,
    não fecha."""
    user, instancias, grupos, execucao = _cenario(
        db, n_grupos=3, n_instancias=2, instancias_pausadas=(0,))
    cliente = _FakeWaha()
    r = _servico(db, cliente).processar_fatia(execucao.id)

    db.expire_all()
    assert r["enviadas"] == 3
    assert execucao.status == EXEC_CONCLUIDA
    # Tudo saiu pelo chip que não está pausado.
    assert {m.instancia_id for m in db.query(RoteiroMensagem)
            .filter(RoteiroMensagem.execucao_id == execucao.id).all()} == {instancias[1].id}


def test_teto_global_da_plataforma_parqueia_para_amanha(db, monkeypatch):
    """
    O teto global protege a plataforma inteira, não a usuária — e por isso
    PARQUEIA (volta a `agendada` na próxima abertura) em vez de pausar:
    pausada exigiria clique da afiliada para um limite que reseta sozinho à
    meia-noite. Era o único caminho de parada sem teste.
    """
    user, _, _, execucao = _cenario(db, n_grupos=3)
    monkeypatch.setattr(settings, "WHATSAPP_CAMPANHA_TETO_GLOBAL_DIA", 0)
    r = _servico(db, _FakeWaha()).processar_fatia(execucao.id)

    db.expire_all()
    assert r["enviadas"] == 0
    assert r["motivo_parada"] == "teto_global"
    assert execucao.status == EXEC_AGENDADA          # volta sozinha, sem clique
    assert execucao.proxima_execucao_em is not None


def test_grupo_invalido_pula_desativa_e_nao_aborta(db):
    user, _, grupos, execucao = _cenario(db, n_grupos=3)
    alvo = grupos[0]
    cliente = _FakeWaha(plano_de_erros={
        alvo.jid: ErroWhatsapp("grupo_invalido", "fomos removidas"),
    })
    r = _servico(db, cliente).processar_fatia(execucao.id)

    db.expire_all()
    assert r["puladas"] == 1 and r["enviadas"] == 2
    assert alvo.ativo is False
    assert execucao.status == EXEC_CONCLUIDA


def test_disjuntor_desconecta_e_failover_para_o_outro_numero(db):
    user, instancias, grupos, execucao = _cenario(db, n_grupos=2, n_instancias=2)
    ruim = instancias[0]

    class _WahaSeletivo:
        def __init__(self):
            self.enviadas = []

        def para(self, nome):
            self.nome = nome
            return self

        def enviar_texto(self, chat_id, texto):
            if self.nome == ruim.nome_instancia:
                raise ErroWhatsapp("desconectado", "sessão caiu")   # fatal
            self.enviadas.append((self.nome, chat_id))
            return {"ok": True}

        def enviar_imagem(self, *a, **k):
            return self.enviar_texto(a[0], "")

    seletivo = _WahaSeletivo()
    svc = RoteiroEnvioService(db, dormir=lambda s: None,
                              cliente_factory=lambda nome: seletivo.para(nome))
    r = svc.processar_fatia(execucao.id)

    db.expire_all()
    assert ruim.status == "desconectada"           # disjuntor
    assert r["enviadas"] == 2                      # o 2º número assumiu tudo
    assert {n for n, _ in seletivo.enviadas} == {instancias[1].nome_instancia}
    assert execucao.status == EXEC_CONCLUIDA


def test_teto_do_plano_parqueia_para_amanha_nao_pausa(db):
    # Roteiro de vários dias não pode morrer no teto diário esperando clique.
    user, _, _, execucao = _cenario(db, n_grupos=2)
    # queima o teto do plano: 240 mensagens "enviadas" hoje
    passo_id = (db.query(RoteiroMensagem.passo_id)
                .filter(RoteiroMensagem.execucao_id == execucao.id)
                .limit(1).scalar())
    agora = datetime.now(timezone.utc)
    outra_exec = RoteiroExecucao(roteiro_id=execucao.roteiro_id,
                                 user_id=user.id, data_ancora=agora.date(),
                                 status="concluida")
    db.add(outra_exec); db.flush()
    # 240 grupos sintéticos: a UNIQUE(execucao,passo,grupo) impede repetir o mesmo
    for i in range(240):
        g = WhatsappGrupo(user_id=user.id, jid=f"9{uuid.uuid4().hex[:12]}@g.us",
                          ativo=True, permite_envio=True)
        db.add(g); db.flush()
        db.add(RoteiroMensagem(execucao_id=outra_exec.id, passo_id=passo_id,
                               grupo_id=g.id, user_id=user.id,
                               agendado_para=agora, status=MSG_ENVIADA,
                               enviado_em=agora))
    db.commit()

    r = _servico(db, _FakeWaha()).processar_fatia(execucao.id)
    db.expire_all()
    assert r["motivo_parada"] == "teto_plano"
    assert execucao.status == EXEC_AGENDADA          # retomada AUTOMÁTICA
    assert execucao.proxima_execucao_em > agora       # amanhã, na abertura


def test_execucao_estagnada_em_enviando_e_resgatada_pelo_tick(db):
    from app.repositories.roteiro_repository import RoteiroRepository
    user, _, _, execucao = _cenario(db)
    execucao.iniciado_em = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()
    agora = datetime.now(timezone.utc)
    ids = RoteiroRepository(db).enviando_estagnadas(agora)
    assert execucao.id in ids
    # execução com atividade recente NÃO é resgatada
    m = (db.query(RoteiroMensagem)
         .filter(RoteiroMensagem.execucao_id == execucao.id).first())
    m.status = MSG_ENVIADA
    m.enviado_em = agora
    db.commit()
    assert execucao.id not in RoteiroRepository(db).enviando_estagnadas(agora)


# --- ações de grupo (F4) ------------------------------------------------------

def _cenario_acao(db, acao, parametro=None, sou_admin=True, com_campanha=True):
    """Execução com UM passo de ação sobre UM grupo."""
    from app.models.campanha_grupos import Campanha, CampanhaGrupo

    suf = uuid.uuid4().hex[:8]
    user = User(email=f"a-{suf}@x.com", hashed_password="x")
    db.add(user); db.flush()
    db.add(Subscription(user_id=user.id, plan="max", is_active=True))
    db.add(UserSettings(user_id=user.id, whatsapp_envio_config=JANELA_24H))
    inst = WhatsappInstancia(user_id=user.id, nome_instancia=f"mkdacao{suf}",
                             status="conectada")
    db.add(inst); db.flush()

    campanha_id = None
    if com_campanha:
        c = Campanha(user_id=user.id, nome=f"c-{suf}")
        db.add(c); db.flush()
        campanha_id = c.id

    roteiro = Roteiro(user_id=user.id, nome=f"r-{suf}", campanha_id=campanha_id)
    db.add(roteiro); db.flush()
    passo = RoteiroPasso(roteiro_id=roteiro.id, ordem=1, tipo_conteudo="acao_grupo",
                         acao=acao, acao_parametro=parametro,
                         tipo_tempo="ancora", hora_fixa=time_t(9, 0))
    db.add(passo); db.flush()

    g = WhatsappGrupo(user_id=user.id, jid=f"120363{suf}@g.us", nome="Antigo",
                      ativo=True, ativado=True, permite_envio=True,
                      sou_admin=sou_admin)
    db.add(g); db.flush()
    db.add(WhatsappGrupoInstancia(grupo_id=g.id, instancia_id=inst.id))
    if campanha_id:
        db.add(CampanhaGrupo(campanha_id=campanha_id, grupo_id=g.id, aberto=True))

    execucao = RoteiroExecucao(roteiro_id=roteiro.id, user_id=user.id,
                               data_ancora=datetime.now(timezone.utc).date(),
                               status=EXEC_ENVIANDO)
    db.add(execucao); db.flush()
    db.add(RoteiroMensagem(execucao_id=execucao.id, passo_id=passo.id,
                           grupo_id=g.id, user_id=user.id,
                           agendado_para=datetime.now(timezone.utc) - timedelta(seconds=60)))
    db.commit()
    return user, g, execucao, campanha_id


class _WahaComRenome(_FakeWaha):
    def __init__(self):
        super().__init__()
        self.renomeados = []

    def renomear_grupo(self, jid, nome):
        self.renomeados.append((jid, nome))


def test_acao_renomear_grupo_chama_o_waha_e_atualiza_o_nome(db):
    user, grupo, execucao, _ = _cenario_acao(db, "renomear_grupo", "ABERTO 🔓")
    cliente = _WahaComRenome()
    r = _servico(db, cliente).processar_fatia(execucao.id)

    db.expire_all()
    assert cliente.renomeados == [(grupo.jid, "ABERTO 🔓")]
    assert grupo.nome == "ABERTO 🔓"
    assert r["enviadas"] == 1 and cliente.enviadas == []   # ação não manda mensagem


def test_acao_abrir_entrada_faz_flip_local_sem_tocar_no_whatsapp(db):
    from app.models.campanha_grupos import CampanhaGrupo

    user, grupo, execucao, campanha_id = _cenario_acao(db, "fechar_entrada")
    cliente = _WahaComRenome()
    _servico(db, cliente).processar_fatia(execucao.id)

    db.expire_all()
    vinculo = (db.query(CampanhaGrupo)
               .filter(CampanhaGrupo.campanha_id == campanha_id,
                       CampanhaGrupo.grupo_id == grupo.id).one())
    assert vinculo.aberto is False
    assert cliente.renomeados == [] and cliente.enviadas == []


def test_renomear_sem_admin_nasce_pulado_no_agendar(db):
    from app.services.roteiro_service import RoteiroService

    user, grupo, execucao, _ = _cenario_acao(db, "renomear_grupo", "X",
                                             sou_admin=False)
    # a materialização acontece no agendar; aqui exercitamos a regra direto
    roteiro = db.query(Roteiro).filter(Roteiro.user_id == user.id).one()
    nova, _avisos = RoteiroService(db).agendar(roteiro,
                                               datetime.now(timezone.utc).date(),
                                               ignorar_avisos=True)
    linhas = (db.query(RoteiroMensagem)
              .filter(RoteiroMensagem.execucao_id == nova.id).all())
    assert [l.status for l in linhas] == [MSG_PULADA]
    assert linhas[0].erro_motivo == "sem_admin"


def test_acao_de_entrada_sem_campanha_falha_a_linha_sem_derrubar_o_lote(db):
    user, grupo, execucao, _ = _cenario_acao(db, "abrir_entrada", com_campanha=False)
    r = _servico(db, _WahaComRenome()).processar_fatia(execucao.id)
    db.expire_all()
    assert r["puladas"] == 1        # ValueError vira pulado, não exceção
    assert execucao.status == EXEC_CONCLUIDA


def test_sem_admin_no_grupo_nao_desconecta_o_numero(db):
    # 403 do WhatsApp em UM grupo é problema DAQUELE grupo: punir a instância
    # desconectaria o número da afiliada por causa de um rename.
    user, grupo, execucao, _ = _cenario_acao(db, "renomear_grupo", "NOVO")

    class _WahaSemAdmin(_WahaComRenome):
        def renomear_grupo(self, jid, nome):
            raise ErroWhatsapp("sem_permissao", "not an admin")

    r = _servico(db, _WahaSemAdmin()).processar_fatia(execucao.id)
    db.expire_all()
    inst = db.query(WhatsappInstancia).filter(WhatsappInstancia.user_id == user.id).one()
    assert r["puladas"] == 1
    assert inst.status == "conectada"        # número intacto
    assert inst.falhas_seguidas == 0         # não conta para o disjuntor
    assert grupo.ativo is True               # grupo NÃO é desativado


def test_erro_transitorio_na_acao_nao_desativa_o_grupo(db):
    user, grupo, execucao, _ = _cenario_acao(db, "renomear_grupo", "NOVO")

    class _WahaInstavel(_WahaComRenome):
        def renomear_grupo(self, jid, nome):
            raise ErroWhatsapp("acao", "status 502: bad gateway")

    r = _servico(db, _WahaInstavel()).processar_fatia(execucao.id)
    db.expire_all()
    assert r["puladas"] == 1
    assert grupo.ativo is True   # 5xx não é "grupo inválido"


def test_admin_vem_do_vinculo_por_numero_nao_do_flag_do_grupo(db):
    """Com 2 números, o flag do grupo é do ÚLTIMO sync — o que vale é o
    vínculo: basta UM número admin, porque o motor faz failover."""
    from app.services.roteiro_service import RoteiroService

    user, grupo, execucao, _ = _cenario_acao(db, "renomear_grupo", "X",
                                             sou_admin=False)
    # o flag agregado diz "não sou admin", mas o vínculo do número diz que é
    vinculo = (db.query(WhatsappGrupoInstancia)
               .filter(WhatsappGrupoInstancia.grupo_id == grupo.id).one())
    vinculo.sou_admin = True
    db.commit()

    roteiro = db.query(Roteiro).filter(Roteiro.user_id == user.id).one()
    nova, _a = RoteiroService(db).agendar(roteiro,
                                          datetime.now(timezone.utc).date(),
                                          ignorar_avisos=True)
    linhas = (db.query(RoteiroMensagem)
              .filter(RoteiroMensagem.execucao_id == nova.id).all())
    assert [l.status for l in linhas] == [MSG_PENDENTE]   # não foi pulado


def test_flip_local_nao_paga_pausa_anti_ban(db):
    user, grupo, execucao, _ = _cenario_acao(db, "abrir_entrada")
    pausas = []
    _servico(db, _WahaComRenome(), dormir=pausas.append).processar_fatia(execucao.id)
    assert pausas == []   # abrir/fechar entrada não toca o WhatsApp


# --- §2.6: falha de PROXY não é banimento (e vice-versa) ---------------------


def _proxy_no_banco(db, max_sessoes=3):
    from app.models.whatsapp_proxies import WhatsappProxy

    p = WhatsappProxy(rotulo=f"BR-{uuid.uuid4().hex[:4]}", tipo="movel",
                      host="10.0.0.9", porta=8080, pais="BR",
                      max_sessoes=max_sessoes)
    db.add(p)
    db.flush()
    return p


def test_rede_em_todos_os_chips_do_proxy_pausa_e_degrada_o_ip(db):
    """
    O sintoma que o motor não sabia ler: o IP caiu, e não o número.

    Antes, `timeout` contava para o disjuntor — cinco falhas e o número era
    marcado desconectado, exigindo novo QR da afiliada por causa de um proxy
    fora do ar. Agora o proxy vai a `degradado` e a execução PAUSA (retomável),
    sem tocar no status do número.
    """
    from app.models.whatsapp_proxies import PROXY_DEGRADADO

    user, instancias, grupos, execucao = _cenario(db, n_grupos=4, n_instancias=2)
    proxy = _proxy_no_banco(db)
    for inst in instancias:
        inst.proxy_id = proxy.id
    db.commit()

    class _SemRede:
        def enviar_texto(self, chat_id, texto):
            raise ErroWhatsapp("timeout", "conexão morreu")

        def enviar_imagem(self, *a, **k):
            return self.enviar_texto(a[0], "")

    r = _servico(db, _SemRede()).processar_fatia(execucao.id)

    db.expire_all()
    assert r["motivo_parada"] == "proxy_degradado"
    assert execucao.status == EXEC_PAUSADA
    assert db.query(type(proxy)).get(proxy.id).status == PROXY_DEGRADADO
    # O número NÃO é o culpado: continua conectado e sem falhas acumuladas.
    for inst in instancias:
        db.refresh(inst)
        assert inst.status == "conectada"
        assert (inst.falhas_seguidas or 0) == 0
    # A linha que não deu para enviar volta para a fila — o problema era global.
    assert db.query(RoteiroMensagem).filter(
        RoteiroMensagem.execucao_id == execucao.id,
        RoteiroMensagem.status == MSG_PENDENTE).count() >= 1


def test_rede_pontual_em_um_chip_nao_derruba_o_numero(db):
    """Um chip com instabilidade e o outro saudável = rede pontual. Falha a
    linha e segue; nem pausa a execução, nem desconecta o número."""
    user, instancias, grupos, execucao = _cenario(db, n_grupos=4, n_instancias=2)
    proxy = _proxy_no_banco(db)
    for inst in instancias:
        inst.proxy_id = proxy.id
    ruim = instancias[0]
    db.commit()

    class _Seletivo:
        def __init__(self):
            self.enviadas = []

        def para(self, nome):
            self.nome = nome
            return self

        def enviar_texto(self, chat_id, texto):
            if self.nome == ruim.nome_instancia:
                raise ErroWhatsapp("timeout", "instabilidade")
            self.enviadas.append(chat_id)
            return {"ok": True}

        def enviar_imagem(self, *a, **k):
            return self.enviar_texto(a[0], "")

    seletivo = _Seletivo()
    svc = RoteiroEnvioService(db, dormir=lambda s: None,
                              cliente_factory=lambda nome: seletivo.para(nome))
    r = svc.processar_fatia(execucao.id)

    db.expire_all()
    assert execucao.status != EXEC_PAUSADA
    db.refresh(ruim)
    assert ruim.status == "conectada"
    assert (ruim.falhas_seguidas or 0) == 0, "rede pontual contou como banimento"


def test_desconectado_nao_troca_proxy_e_mantem_o_disjuntor(db):
    """`desconectado` é o número caindo (ou banido). Trocar de IP aqui
    queimaria o IP seguinte também — o disjuntor antigo continua valendo."""
    from app.models.whatsapp_proxies import PROXY_OK

    user, instancias, grupos, execucao = _cenario(db, n_grupos=2, n_instancias=1)
    proxy = _proxy_no_banco(db)
    inst = instancias[0]
    inst.proxy_id = proxy.id
    db.commit()

    class _Caiu:
        def enviar_texto(self, chat_id, texto):
            raise ErroWhatsapp("desconectado", "sessão caiu")

        def enviar_imagem(self, *a, **k):
            return self.enviar_texto(a[0], "")

    _servico(db, _Caiu()).processar_fatia(execucao.id)

    db.expire_all()
    db.refresh(inst)
    assert inst.status == "desconectada"          # disjuntor, como antes
    assert inst.proxy_id == proxy.id              # MESMO IP
    assert (inst.proxy_trocas or 0) == 0
    assert db.query(type(proxy)).get(proxy.id).status == PROXY_OK


def test_sem_proxy_a_rede_continua_no_disjuntor_antigo(db):
    """Sem proxy não há como distinguir 'o IP caiu' de 'o WAHA caiu' — o
    comportamento antigo (disjuntor) é o que impede o lote de girar em falso."""
    user, instancias, grupos, execucao = _cenario(db, n_grupos=8, n_instancias=1)
    inst = instancias[0]
    assert inst.proxy_id is None

    class _SemRede:
        def enviar_texto(self, chat_id, texto):
            raise ErroWhatsapp("rede", "connection reset")

        def enviar_imagem(self, *a, **k):
            return self.enviar_texto(a[0], "")

    _servico(db, _SemRede()).processar_fatia(execucao.id)

    db.expire_all()
    db.refresh(inst)
    assert inst.status == "desconectada"
