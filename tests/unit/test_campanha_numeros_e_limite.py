"""
Rodada de correções das campanhas de grupos (documento delta 03/09).

Os invariantes que esta rodada cria e que não podem regredir:

  * o limite de participantes da campanha tira o grupo da rotação ANTES da
    capacidade do WhatsApp — e o contador da tela usa a MESMA regra;
  * remover um número que ainda tem grupos na campanha é bloqueado, e o erro
    diz quais grupos travam;
  * grupo que não pertence a nenhum número da campanha não entra no vínculo —
    é o bug que faz o envio falhar em silêncio;
  * o identificador do participante distingue telefone de LID.
"""
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

PG_URL = "postgresql://dashads_user:dashads_password@localhost:5434/dashads_db"
try:
    _p = create_engine(PG_URL, pool_pre_ping=True)
    with _p.connect() as _c:
        _c.execute(text("SELECT 1"))
    PG_OK = True
except Exception:
    PG_OK = False

pytestmark = pytest.mark.skipif(not PG_OK, reason="Postgres local (5434) indisponível")

if PG_OK:
    from app.db.base import Base
    import app.models  # noqa: F401
    ENGINE = create_engine(PG_URL)
    Base.metadata.create_all(ENGINE)
    Sessao = sessionmaker(bind=ENGINE)

from app.models.campanha_grupos import Campanha, CampanhaGrupo, CampanhaNumero  # noqa: E402
from app.models.subscription import Subscription  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.whatsapp_grupos import (  # noqa: E402
    WhatsappGrupo, WhatsappGrupoInstancia, WhatsappInstancia,
)
from app.repositories.campanha_link_repository import CampanhaLinkRepository  # noqa: E402
from app.services.campanha_grupos_service import (  # noqa: E402
    CampanhaGruposService, GrupoForaDosNumeros, NumeroEmUso,
)
from app.services.grupo_evento_service import classificar  # noqa: E402


@pytest.fixture
def db():
    s = Sessao()
    yield s
    s.rollback()
    s.close()


def _cenario(db, participantes=(10, 10), capacidade=1024):
    """Uma campanha, dois números, um grupo por número."""
    suf = uuid.uuid4().hex[:8]
    user = User(email=f"n-{suf}@x.com", hashed_password="x")
    db.add(user); db.flush()
    campanha = Campanha(user_id=user.id, nome=f"c-{suf}")
    db.add(campanha); db.flush()

    numeros, grupos = [], []
    for i in range(2):
        inst = WhatsappInstancia(user_id=user.id, nome_instancia=f"mkd{suf}u{user.id}x{i}",
                                 nome_exibicao=f"Chip {i}", numero=f"551199990{i:03d}",
                                 status="conectada")
        db.add(inst); db.flush()
        g = WhatsappGrupo(user_id=user.id, jid=f"12036{suf}{i}@g.us", nome=f"G{i}",
                          ativo=True, ativado=True, permite_envio=True, sou_admin=True,
                          participantes=participantes[i], capacidade=capacidade,
                          sub_id=f"wg{suf}{i}",
                          link_convite=f"https://chat.whatsapp.com/{suf}{i}")
        db.add(g); db.flush()
        db.add(WhatsappGrupoInstancia(grupo_id=g.id, instancia_id=inst.id, sou_admin=True))
        db.add(CampanhaGrupo(campanha_id=campanha.id, grupo_id=g.id, posicao=i))
        db.add(CampanhaNumero(campanha_id=campanha.id, instancia_id=inst.id))
        numeros.append(inst)
        grupos.append(g)
    db.commit()
    return user, campanha, numeros, grupos


# --- §3.4: limite de participantes ------------------------------------------


def test_limite_da_campanha_tira_o_grupo_da_rotacao_antes_da_capacidade(db):
    """
    O grupo tem 950 de 1024 (há vaga pelo WhatsApp), mas a campanha manda
    encher só até 900. O roteador tem que pular para o próximo.
    """
    user, campanha, numeros, grupos = _cenario(db, participantes=(950, 10))
    repo = CampanhaLinkRepository(db)

    # Sem limite: o grupo 0 (posição 0) é o escolhido.
    assert repo.escolher_grupo(campanha.id, aleatorio=False)[0] == grupos[0].id

    campanha.limite_participantes = 900
    db.commit()
    # Com limite: o 0 saiu da rotação, sobra o 1.
    assert repo.escolher_grupo(campanha.id, aleatorio=False)[0] == grupos[1].id


def test_capacidade_continua_sendo_o_teto_absoluto(db):
    """Limite MAIOR que a capacidade não faz o grupo lotado voltar à rotação."""
    user, campanha, numeros, grupos = _cenario(db, participantes=(1024, 10),
                                               capacidade=1024)
    campanha.limite_participantes = 1024
    db.commit()
    escolhido = CampanhaLinkRepository(db).escolher_grupo(campanha.id, aleatorio=False)
    assert escolhido[0] == grupos[1].id, "grupo na capacidade não pode receber entrada"


def test_lotados_abertos_enxerga_o_limite_da_campanha(db):
    """A varredura que fecha os lotados usa a mesma regra da escolha."""
    user, campanha, numeros, grupos = _cenario(db, participantes=(950, 10))
    repo = CampanhaLinkRepository(db)
    assert repo.lotados_abertos(campanha.id) == []

    campanha.limite_participantes = 900
    db.commit()
    lotados = [v.grupo_id for v in repo.lotados_abertos(campanha.id)]
    assert lotados == [grupos[0].id]


def test_contador_da_visao_geral_usa_a_mesma_regra_de_lotacao(db):
    """
    O "cheios" da tela não pode divergir da rotação: dizer "há vaga" num grupo
    que o roteador já não escolhe é o pior tipo de número errado.
    """
    from app.services.campanha_visao_geral_service import CampanhaVisaoGeralService

    user, campanha, numeros, grupos = _cenario(db, participantes=(950, 10))
    campanha.limite_participantes = 900
    db.commit()

    estado = CampanhaVisaoGeralService(db).resumo(campanha, dias=7)["grupos"]
    assert estado["total"] == 2
    assert estado["cheios"] == 1
    assert estado["disponiveis"] == 1


# --- §2.4: remoção de número com grupos -------------------------------------


def test_remover_numero_com_grupos_e_bloqueado_e_nomeia_os_grupos(db):
    user, campanha, numeros, grupos = _cenario(db)
    svc = CampanhaGruposService(db)

    with pytest.raises(NumeroEmUso) as erro:
        svc.definir_numeros(campanha, [numeros[1].id])   # tira o 0, que tem G0

    travando = erro.value.grupos_por_numero
    assert "Chip 0" in travando
    assert travando["Chip 0"] == ["G0"]
    # E nada foi alterado: bloquear tem que ser atômico.
    assert set(svc.repo_numeros.instancia_ids(campanha.id)) == {n.id for n in numeros}


def test_remover_numero_sem_grupos_na_campanha_passa(db):
    user, campanha, numeros, grupos = _cenario(db)
    svc = CampanhaGruposService(db)
    # Tira o grupo do número 1 da campanha; aí o número pode sair.
    db.query(CampanhaGrupo).filter(
        CampanhaGrupo.campanha_id == campanha.id,
        CampanhaGrupo.grupo_id == grupos[1].id).delete()
    db.commit()

    svc.definir_numeros(campanha, [numeros[0].id])
    assert svc.repo_numeros.instancia_ids(campanha.id) == [numeros[0].id]


# --- §2.3: escopo dos grupos pelos números ----------------------------------


def test_grupo_fora_dos_numeros_da_campanha_nao_entra(db):
    """
    É o bug que a aba Números existe para matar: grupo do número A numa
    campanha que dispara pelo B faz o envio falhar, em silêncio.
    """
    user, campanha, numeros, grupos = _cenario(db)
    svc = CampanhaGruposService(db)
    # A campanha passa a usar SÓ o número 0 — e G1 é do número 1.
    db.query(CampanhaGrupo).filter(
        CampanhaGrupo.campanha_id == campanha.id,
        CampanhaGrupo.grupo_id == grupos[1].id).delete()
    db.commit()
    svc.definir_numeros(campanha, [numeros[0].id])

    with pytest.raises(GrupoForaDosNumeros) as erro:
        svc.definir_grupos(campanha, [(grupos[0].id, 0, True), (grupos[1].id, 1, True)])
    assert erro.value.nomes == ["G1"]


def test_campanha_sem_numero_escolhido_nao_restringe(db):
    """Quem ainda não configurou a aba Números não fica travada."""
    user, campanha, numeros, grupos = _cenario(db)
    svc = CampanhaGruposService(db)
    db.query(CampanhaNumero).filter(
        CampanhaNumero.campanha_id == campanha.id).delete()
    db.commit()

    svc.definir_grupos(campanha, [(grupos[0].id, 0, True), (grupos[1].id, 1, True)])
    assert svc.total_de_grupos(campanha) == 2


# --- §3.7: identificador do participante ------------------------------------


@pytest.mark.parametrize("jid,esperado", [
    ("5511988887777@c.us", "telefone"),
    ("5511988887777@s.whatsapp.net", "telefone"),
    ("84729130@lid", "lid"),
    ("84729130@LID", "lid"),
])
def test_classificar_distingue_telefone_de_lid(jid, esperado):
    cru, tipo = classificar(jid)
    assert cru == jid
    assert tipo == esperado


def test_evento_grava_o_identificador_junto_do_hash(db):
    """O hash continua — é ele que casa entrada com saída."""
    from app.models.campanha_link import GrupoEvento
    from app.services.grupo_evento_service import GrupoEventoService

    user, campanha, numeros, grupos = _cenario(db)
    GrupoEventoService(db).registrar(user.id, grupos[0].jid, "join",
                                     ["5511977776666@c.us"])
    db.commit()

    ev = (db.query(GrupoEvento)
          .filter(GrupoEvento.grupo_id == grupos[0].id)
          .order_by(GrupoEvento.id.desc()).first())
    assert ev.identificador == "5511977776666@c.us"
    assert ev.identificador_tipo == "telefone"
    assert len(ev.identificador_hash) == 64


# --- a rota, não só o service ------------------------------------------------


def test_rota_criar_nao_le_campo_que_o_schema_nao_tem(db):
    """
    Regressão: `descricao` saiu de `CampanhaCriar` (§1.1) e a rota continuou
    lendo `payload.descricao`.

    O Pydantic v2 não guarda campo que não declarou, então o acesso levantava
    `AttributeError` — que não é `ValueError` nem `LimiteDeCampanhas`, os dois
    únicos `except` da rota. Resultado: 500 em TODA criação de campanha, com a
    suíte verde, porque os testes chamavam o service direto e pulavam a rota.
    """
    from app.api.v1.routes.campanhas_grupos import criar
    from app.schemas.campanhas_grupos import CampanhaCriar

    suf = uuid.uuid4().hex[:8]
    user = User(email=f"rota-{suf}@x.com", hashed_password="x")
    db.add(user); db.flush()
    # Campanhas de grupos são MAX-only: sem isto a rota devolve 403 e o teste
    # nem chega na linha que tinha o defeito.
    db.add(Subscription(user_id=user.id, plan="max", is_active=True))
    db.commit()

    out = criar(payload=CampanhaCriar(nome=f"Campanha {suf}"),
                current_user=user, db=db)
    assert out.nome == f"Campanha {suf}"
    assert out.total_grupos == 0
    # O limite continua sendo o da campanha, não o do grupo.
    assert out.limite_participantes is None


# --- regressões achadas na revisão pré-commit --------------------------------


def test_numero_removido_da_conta_nao_estoura_keyerror(db):
    """
    `remover()` faz soft delete (status='removida') e NÃO limpa
    whatsapp_grupo_instancias — a linha continua em campanha_numeros.

    Indexar `minhas[iid]` com essa instância dava KeyError, que vira 500 e
    deixa a aba Números intransitável para a campanha inteira.
    """
    user, campanha, numeros, grupos = _cenario(db)
    numeros[0].status = "removida"
    db.commit()

    svc = CampanhaGruposService(db)
    # O grupo do chip removido continua na campanha: tem que BLOQUEAR (409),
    # não estourar. O rótulo cai no genérico, já que a instância sumiu da lista.
    with pytest.raises(NumeroEmUso) as erro:
        svc.definir_numeros(campanha, [numeros[1].id])
    assert any("G0" in nomes for nomes in erro.value.grupos_por_numero.values())


def test_desmarcar_chip_cujo_grupo_outro_chip_serve_e_permitido(db):
    """
    O bloqueio é por grupo ÓRFÃO, não por presença.

    Dois chips no MESMO grupo é o cenário de quem vai aquecer um número: tirar
    um deles não deixa grupo nenhum sem número, e bloquear isso obrigava a
    esvaziar a campanha antes.
    """
    user, campanha, numeros, grupos = _cenario(db)
    # Ambos os chips passam a alcançar o grupo 0; o grupo 1 sai da campanha.
    db.add(WhatsappGrupoInstancia(grupo_id=grupos[0].id,
                                  instancia_id=numeros[1].id, sou_admin=True))
    db.query(CampanhaGrupo).filter(
        CampanhaGrupo.campanha_id == campanha.id,
        CampanhaGrupo.grupo_id == grupos[1].id).delete()
    db.commit()

    svc = CampanhaGruposService(db)
    svc.definir_numeros(campanha, [numeros[1].id])   # não pode levantar
    assert svc.repo_numeros.instancia_ids(campanha.id) == [numeros[1].id]


def test_disponiveis_nao_conta_grupo_que_o_roteador_recusa(db):
    """
    "Disponível" precisa significar o que `escolher_grupo` aceita: aberto, com
    vaga, ativo e COM link de convite. Contar só aberto+vaga fazia o painel
    dizer "há vaga" enquanto quem clicava no link via "vagas esgotadas".
    """
    from app.repositories.campanha_link_repository import CampanhaLinkRepository
    from app.services.campanha_visao_geral_service import CampanhaVisaoGeralService

    user, campanha, numeros, grupos = _cenario(db)
    grupos[0].ativo = False              # sumiu do WhatsApp
    grupos[1].link_convite = None        # sem convite
    db.commit()

    estado = CampanhaVisaoGeralService(db).resumo(campanha, dias=7)["grupos"]
    assert estado["total"] == 2
    assert estado["disponiveis"] == 0, "nenhum dos dois pode receber entrada"
    # E a tela concorda com o roteador, que é o ponto.
    assert CampanhaLinkRepository(db).escolher_grupo(campanha.id, aleatorio=False) is None


def test_participantes_usa_o_contador_vivo_nao_o_snapshot_de_ontem(db):
    """
    O snapshot é cópia congelada do mesmo campo, gravada 1×/dia. Preferi-lo
    fazia a Visão geral mostrar o número de ontem enquanto as abas Grupos e
    Resultados mostravam o de hoje — duas telas da mesma campanha divergindo.
    """
    from datetime import date, timedelta

    from app.models.campanha_link import GrupoSnapshot
    from app.services.campanha_visao_geral_service import CampanhaVisaoGeralService

    user, campanha, numeros, grupos = _cenario(db, participantes=(380, 0))
    db.add(GrupoSnapshot(grupo_id=grupos[0].id, data=date.today() - timedelta(days=1),
                         participantes=300, admins=1))
    db.commit()

    resumo = CampanhaVisaoGeralService(db).resumo(campanha, dias=7)
    assert resumo["participantes"] == 380, "o snapshot de ontem não pode vencer"
