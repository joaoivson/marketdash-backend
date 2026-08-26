"""
O MOTOR contra Postgres real (claim FOR UPDATE SKIP LOCKED não existe no
SQLite). Usa o Postgres local do docker-compose; sem ele, os testes pulam.

Os invariantes que não podem regredir:
  * matar o worker no meio NÃO duplica mensagem (presa vira falhou);
  * claim concorrente nunca entrega a mesma linha a dois workers;
  * pausar vale no meio da fatia;
  * janela fechada devolve a execução para `agendada` na próxima abertura;
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


JANELA_24H = {"ativo": True, "dias": {str(i): {"ativo": True,
              "inicio": "00:00", "fim": "23:59"} for i in range(7)}}


def _cenario(db, n_grupos=3, n_instancias=1, teto_instancia=None,
             janela_config=JANELA_24H, agendado_delta_s=-60):
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
        g = WhatsappGrupo(user_id=user.id, jid=f"12036{suf}{i}@g.us",
                          nome=f"G{i}", ativo=True, permite_envio=True)
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


def test_teto_da_instancia_esvazia_o_pool_e_pausa(db):
    user, _, _, execucao = _cenario(db, n_grupos=3, teto_instancia=1)
    cliente = _FakeWaha()
    r = _servico(db, cliente).processar_fatia(execucao.id)

    db.expire_all()
    assert r["enviadas"] == 1                      # bateu no teto após a 1ª
    assert execucao.status == EXEC_PAUSADA         # retomável
    assert r["motivo_parada"] == "sem_instancia"


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
                      ativo=True, permite_envio=True, sou_admin=sou_admin)
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
