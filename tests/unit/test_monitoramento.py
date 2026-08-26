"""
Monitoramento de grupos (F8).

Os invariantes que não podem regredir:
  * mensagem que não passa no filtro NÃO é persistida (o filtro roda antes);
  * nada identifica quem escreveu — nenhum JID, telefone ou hash de autor;
  * repost da mesma oferta não vira envio duplicado (dedup por constraint);
  * sessão de aluna SEM monitoramento ativo não assina `message`;
  * o grupo de ORIGEM nunca é destino da replicação.
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

from app.models.campanha_grupos import Campanha, CampanhaGrupo  # noqa: E402
from app.models.monitoramento import (  # noqa: E402
    CAPTURA_CAPTURADA, Monitoramento, MonitoramentoCaptura,
)
from app.models.user import User  # noqa: E402
from app.models.whatsapp_grupos import (  # noqa: E402
    WhatsappGrupo, WhatsappGrupoInstancia, WhatsappInstancia,
)
from app.services.monitoramento_service import (  # noqa: E402
    LimiteDeMonitoramentos, MonitoramentoInvalido, MonitoramentoService,
    com_esquema, extrair_link, extrair_links, hash_da_mensagem,
)
from app.services.whatsapp_instancia_service import eventos_desejados  # noqa: E402


@pytest.fixture
def db():
    s = Sessao()
    yield s
    s.rollback()
    s.close()


def _cenario(db, n_destinos=2):
    suf = uuid.uuid4().hex[:8]
    user = User(email=f"mon-{suf}@x.com", hashed_password="x")
    db.add(user); db.flush()
    inst = WhatsappInstancia(user_id=user.id, nome_instancia=f"mkdtst{suf}",
                             status="conectada")
    db.add(inst); db.flush()

    def _grupo(nome):
        g = WhatsappGrupo(user_id=user.id, jid=f"12036{suf}{nome}@g.us", nome=nome,
                          ativo=True, permite_envio=True, participantes=10,
                          capacidade=1024, sub_id=f"wg{suf}{nome}")
        db.add(g); db.flush()
        db.add(WhatsappGrupoInstancia(grupo_id=g.id, instancia_id=inst.id))
        return g

    origem = _grupo("orig")
    destinos = [_grupo(f"d{i}") for i in range(n_destinos)]
    campanha = Campanha(user_id=user.id, nome=f"c-{suf}")
    db.add(campanha); db.flush()
    for i, g in enumerate(destinos):
        db.add(CampanhaGrupo(campanha_id=campanha.id, grupo_id=g.id, posicao=i))
    db.commit()
    return user, inst, origem, destinos, campanha


# --- filtro antes de persistir ---------------------------------------------


def test_mensagem_sem_link_nao_e_capturada(db):
    """`somente_com_link` existe para conversa de grupo não virar linha no
    banco. O filtro roda ANTES de gravar — não é gravar-e-depois-filtrar."""
    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id,
                  ativo=True)
    db.commit()

    passa, link = svc.interessa(m, "bom dia gente, alguém viu o jogo ontem?")
    assert passa is False and link is None
    assert db.query(MonitoramentoCaptura).filter(
        MonitoramentoCaptura.monitoramento_id == m.id).count() == 0


def test_palavra_chave_filtra_oferta_de_outra_categoria(db):
    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id,
                  ativo=True, palavras_chave=["fone", "headset"])
    db.commit()

    assert svc.interessa(m, "Air Fryer 5L https://shopee.com.br/x")[0] is False
    assert svc.interessa(m, "FONE bluetooth https://shopee.com.br/y")[0] is True


def test_captura_nao_guarda_nada_de_quem_escreveu(db):
    """A tabela não tem coluna de autor. Este teste existe para que adicionar
    uma passe a ser uma decisão consciente, não um descuido."""
    colunas = {c.name for c in MonitoramentoCaptura.__table__.columns}
    proibidas = {"autor", "remetente", "from_jid", "participante",
                 "telefone", "numero", "autor_hash", "identificador_hash"}
    assert not (colunas & proibidas), f"coluna de autor apareceu: {colunas & proibidas}"


# --- dedup ------------------------------------------------------------------


def test_repost_da_mesma_oferta_nao_vira_captura_nova(db):
    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id, ativo=True)
    db.commit()

    texto = "🔥 Air Fryer https://shopee.com.br/produto-123"
    assert svc.capturar(m, texto, "https://shopee.com.br/produto-123") is not None
    db.commit()
    # Mesma oferta reposta com espaçamento diferente — é a mesma oferta.
    assert svc.capturar(m, "🔥  Air   Fryer  https://shopee.com.br/produto-123",
                        "https://shopee.com.br/produto-123") is None


def test_hash_normaliza_espaco_e_caixa():
    assert hash_da_mensagem("Oferta  X") == hash_da_mensagem("oferta x")
    assert hash_da_mensagem("Oferta X") != hash_da_mensagem("Oferta Y")


@pytest.mark.parametrize("texto,esperado", [
    ("olha https://shopee.com.br/abc agora", "https://shopee.com.br/abc"),
    # CRU, sem `https://` postiço: é esta forma que precisa ser encontrada e
    # trocada no texto original.
    ("shopee.com.br/xyz", "shopee.com.br/xyz"),
    ("veja (https://s.shopee.com.br/9z).", "https://s.shopee.com.br/9z"),
    ("sem link nenhum", None),
])
def test_extracao_de_link(texto, esperado):
    assert extrair_link(texto) == esperado


def test_extrai_todos_os_links_na_forma_crua():
    """Mensagem com dois produtos: converter só o primeiro deixaria o segundo
    link do concorrente sair intacto para os grupos dela."""
    links = extrair_links("Promo shopee.com.br/a e tambem https://shopee.com.br/b")
    assert links == ["shopee.com.br/a", "https://shopee.com.br/b"]


def test_com_esquema_normaliza_so_para_resolver_o_marketplace():
    assert com_esquema("shopee.com.br/x") == "https://shopee.com.br/x"
    assert com_esquema("https://shopee.com.br/x") == "https://shopee.com.br/x"


def test_regex_nao_explode_com_entrada_adversarial():
    """`(?:www\.|[a-z0-9-]+\.)+` dava backtracking exponencial: "www."×22
    levava 1,1s e ×30, minutos. O texto vem de um grupo de TERCEIRO — qualquer
    membro poderia travar o webhook."""
    import time

    t = time.perf_counter()
    extrair_links("www." * 40 + "b")
    assert (time.perf_counter() - t) < 0.5, "regex voltou a fazer backtracking"


# --- destino ----------------------------------------------------------------


def test_origem_nunca_e_destino_da_replicacao(db):
    """Replicar de volta para a origem é o monitoramento virando eco — e, num
    grupo de terceiro, a afiliada anunciando dentro do grupo do concorrente."""
    user, _i, origem, destinos, campanha = _cenario(db)
    db.add(CampanhaGrupo(campanha_id=campanha.id, grupo_id=origem.id, posicao=9))
    db.commit()
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id, ativo=True)
    db.commit()

    alvos = svc.grupos_de_destino(m)
    assert origem.id not in alvos
    assert set(alvos) == {g.id for g in destinos}


def test_sem_destino_configurado_nao_replica_para_lugar_nenhum(db):
    user, _i, origem, _d, _c = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, ativo=True)
    db.commit()
    assert svc.grupos_de_destino(m) == []


def test_texto_para_envio_troca_link_colado_sem_https(db):
    """
    O caso que quase foi para produção: o dono do grupo cola SEM esquema, o
    link era salvo normalizado, o `replace` não casava com nada — e a mensagem
    saía para os grupos dela com o link do CONCORRENTE, marcada "replicada".
    """
    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id, ativo=True)
    db.commit()
    texto = "🔥 AIR FRYER\nCorre: shopee.com.br/produto-do-concorrente"
    captura = svc.capturar(m, texto, extrair_link(texto))
    db.commit()

    final = svc.texto_para_envio(
        captura, {"shopee.com.br/produto-do-concorrente": "https://s.shopee.com.br/MEU"})
    assert "https://s.shopee.com.br/MEU" in final
    assert "produto-do-concorrente" not in final


def test_link_prefixo_de_outro_nao_corrompe_a_url(db):
    """Com `/AbC` e `/AbCdEf` no mesmo texto, trocar o curto primeiro deixaria
    o longo pela metade e enviaria uma URL quebrada."""
    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id, ativo=True)
    db.commit()
    texto = "OFERTA https://s.shopee.com.br/AbC e tambem https://s.shopee.com.br/AbCdEf"
    captura = svc.capturar(m, texto, extrair_link(texto))
    db.commit()

    final = svc.texto_para_envio(captura, {
        "https://s.shopee.com.br/AbC": "https://s.shopee.com.br/UM",
        "https://s.shopee.com.br/AbCdEf": "https://s.shopee.com.br/DOIS",
    })
    assert final.endswith("https://s.shopee.com.br/DOIS")
    assert "UMdEf" not in final


# --- ownership e limite -----------------------------------------------------


def test_grupo_de_outra_usuaria_nao_pode_ser_monitorado(db):
    """Id é sequencial: sem a checagem de dono dava para monitorar grupo alheio."""
    user, _i, _o, _d, _c = _cenario(db)
    _outra, _i2, origem_alheia, _d2, _c2 = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    with pytest.raises(MonitoramentoInvalido):
        svc.criar(user.id, "M", origem_alheia.id)


def test_limite_do_plano_barra_o_quarto(db):
    user, _i, origem, _d, _c = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=2)
    svc.criar(user.id, "A", origem.id); db.commit()
    svc.criar(user.id, "B", origem.id); db.commit()
    with pytest.raises(LimiteDeMonitoramentos):
        svc.criar(user.id, "C", origem.id)


# --- eventos da sessão ------------------------------------------------------


def test_sessao_so_assina_message_com_monitoramento_ativo():
    """É o que impede o conteúdo dos grupos de chegar ao backend sem que a
    afiliada tenha pedido."""
    assert "message" not in eventos_desejados(False)
    assert eventos_desejados(False) == ["session.status", "group.v2.participants"]
    assert "message" in eventos_desejados(True)


def test_monitoramento_inativo_nao_faz_a_sessao_precisar_de_message(db):
    user, inst, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id, ativo=False)
    db.commit()
    assert svc.sessoes_que_precisam_de_message(user.id).get(inst.id) is False

    m2 = svc.criar(user.id, "M2", origem.id, destino_campanha_id=campanha.id,
                   ativo=True)
    db.commit()
    assert svc.sessoes_que_precisam_de_message(user.id)[inst.id] is True

    m2.ativo = False
    db.add(m2); db.commit()
    assert svc.sessoes_que_precisam_de_message(user.id)[inst.id] is False


def test_capturar_nasce_como_capturada_e_nao_replicada(db):
    """`replicar_automaticamente` nasce desligado: mandar para os grupos dela um
    texto que outra pessoa escreveu, sem ninguém ler, é o pior default."""
    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id, ativo=True)
    db.commit()
    assert m.replicar_automaticamente is False
    c = svc.capturar(m, "Oferta https://shopee.com.br/a", "https://shopee.com.br/a")
    assert c.status == CAPTURA_CAPTURADA
    assert c.roteiro_id is None


def test_grupo_de_destino_de_outra_usuaria_e_recusado(db):
    """
    Origem E destino vêm como id cru do cliente. Sem checar dono no destino, a
    replicação apontaria para dentro da conta de outra pessoa — o motor não
    enviaria (não há instância desta usuária lá), mas o nome do grupo alheio
    apareceria na tela de progresso.
    """
    user, _i, origem, _d, _c = _cenario(db)
    _outra, _i2, _o2, destinos_alheios, campanha_alheia = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)

    with pytest.raises(MonitoramentoInvalido):
        svc.criar(user.id, "M", origem.id,
                  destino_grupo_ids=[destinos_alheios[0].id])

    with pytest.raises(MonitoramentoInvalido):
        svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha_alheia.id)


def test_destino_com_um_id_alheio_no_meio_de_ids_validos_e_recusado(db):
    """A checagem é do CONJUNTO: um id alheio escondido entre válidos não pode
    passar."""
    user, _i, origem, destinos, _c = _cenario(db)
    _outra, _i2, _o2, alheios, _c2 = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)

    with pytest.raises(MonitoramentoInvalido):
        svc.criar(user.id, "M", origem.id,
                  destino_grupo_ids=[destinos[0].id, alheios[0].id])


def test_claim_da_captura_so_deixa_um_worker_replicar(db):
    """
    Dois workers pegando a mesma captura mandariam a MESMA oferta duas vezes
    para os grupos dela. O claim é uma instrução atômica, não
    SELECT-depois-UPDATE.
    """
    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id, ativo=True)
    db.commit()
    captura = svc.capturar(m, "Oferta https://shopee.com.br/a", "https://shopee.com.br/a")
    db.commit()

    assert svc.reivindicar(captura.id) is True
    assert svc.reivindicar(captura.id) is False, "segundo worker replicaria de novo"

    # Devolver à fila torna a captura reivindicável outra vez (o caminho de
    # monitoramento desligado no meio).
    db.refresh(captura)
    svc.devolver_para_fila(captura)
    db.commit()
    assert svc.reivindicar(captura.id) is True


def test_captura_em_erro_nao_e_reivindicavel(db):
    """Erro é estado terminal até a afiliada agir: reprocessar sozinho mandaria
    de novo uma oferta que já falhou por um motivo que ninguém corrigiu."""
    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id, ativo=True)
    db.commit()
    captura = svc.capturar(m, "Oferta https://shopee.com.br/b", "https://shopee.com.br/b")
    svc.marcar_erro(captura, "conversão falhou")
    db.commit()

    assert svc.reivindicar(captura.id) is False


def test_repost_em_um_monitoramento_nao_desfaz_a_captura_do_outro(db):
    """
    O webhook percorre TODOS os monitoramentos ativos do grupo. Se o segundo já
    tem a mensagem (repost) e o tratamento fizer rollback da transação inteira,
    a captura do primeiro — que era nova — desaparece sem erro nenhum.
    """
    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m1 = svc.criar(user.id, "M1", origem.id, destino_campanha_id=campanha.id, ativo=True)
    m2 = svc.criar(user.id, "M2", origem.id, destino_campanha_id=campanha.id, ativo=True)
    db.commit()

    texto = "Oferta https://shopee.com.br/repost"
    # m2 já viu esta mensagem antes.
    svc.capturar(m2, texto, "https://shopee.com.br/repost")
    db.commit()

    # Agora a mensagem chega: nova para m1, repost para m2.
    nova = svc.capturar(m1, texto, "https://shopee.com.br/repost")
    assert nova is not None
    assert svc.capturar(m2, texto, "https://shopee.com.br/repost") is None
    db.commit()

    assert db.query(MonitoramentoCaptura).filter(
        MonitoramentoCaptura.monitoramento_id == m1.id).count() == 1, \
        "a captura nova foi desfeita pelo repost do outro monitoramento"


# --- caminho completo: webhook → filtro → captura ---------------------------


def test_webhook_de_grupo_monitorado_captura_e_enfileira(db, monkeypatch):
    """
    Fio inteiro com banco real: mensagem de grupo chega pela sessão da aluna,
    passa no filtro, vira captura e — só com `replicar_automaticamente` — pede
    a replicação.
    """
    from app.api.v1.routes import whatsapp as rota

    user, inst, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id,
                  ativo=True, replicar_automaticamente=True)
    db.commit()

    enfileiradas = []
    monkeypatch.setattr(
        "app.services.whatsapp_instancia_service.identidade_do_banco",
        lambda: inst.nome_instancia[3:7] + "restodaref",
    )
    monkeypatch.setattr("app.api.v1.routes.whatsapp.WhatsappInstanciaRepository",
                        lambda _db: _RepoDeUmaInstancia(inst))
    monkeypatch.setattr("app.api.v1.routes.whatsapp.settings.WAHA_SESSAO_RESUMO",
                        "mkd-resumo", raising=False)

    class _Task:
        def apply_async(self, args=None, priority=None):
            enfileiradas.append((args, priority))

    import app.tasks.monitoramento_tasks as tasks
    monkeypatch.setattr(tasks, "replicar_captura", _Task())

    rota._tratar_mensagem(db, inst.nome_instancia, {
        "from": origem.jid, "body": "🔥 Air Fryer https://shopee.com.br/af-123",
        "fromMe": False,
    })

    capturas = db.query(MonitoramentoCaptura).filter(
        MonitoramentoCaptura.monitoramento_id == m.id).all()
    assert len(capturas) == 1
    assert capturas[0].link_original == "https://shopee.com.br/af-123"
    assert capturas[0].texto_original.startswith("🔥 Air Fryer")
    # priority 0 (interativo) — NUNCA um valor do meio, que some em silêncio.
    assert enfileiradas == [([capturas[0].id], 0)]


def test_webhook_sem_link_nao_grava_nem_enfileira(db, monkeypatch):
    from app.api.v1.routes import whatsapp as rota

    user, inst, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id,
                  ativo=True, replicar_automaticamente=True)
    db.commit()

    monkeypatch.setattr(
        "app.services.whatsapp_instancia_service.identidade_do_banco",
        lambda: inst.nome_instancia[3:7] + "restodaref",
    )
    monkeypatch.setattr("app.api.v1.routes.whatsapp.WhatsappInstanciaRepository",
                        lambda _db: _RepoDeUmaInstancia(inst))
    monkeypatch.setattr("app.api.v1.routes.whatsapp.settings.WAHA_SESSAO_RESUMO",
                        "mkd-resumo", raising=False)

    rota._tratar_mensagem(db, inst.nome_instancia, {
        "from": origem.jid, "body": "alguém aí?", "fromMe": False,
    })
    assert db.query(MonitoramentoCaptura).filter(
        MonitoramentoCaptura.monitoramento_id == m.id).count() == 0


def test_webhook_com_monitoramento_inativo_nao_captura(db, monkeypatch):
    """Mesmo que o evento chegue (sessão ainda desalinhada), monitoramento
    desligado não grava nada."""
    from app.api.v1.routes import whatsapp as rota

    user, inst, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id,
                  ativo=False)
    db.commit()

    monkeypatch.setattr(
        "app.services.whatsapp_instancia_service.identidade_do_banco",
        lambda: inst.nome_instancia[3:7] + "restodaref",
    )
    monkeypatch.setattr("app.api.v1.routes.whatsapp.WhatsappInstanciaRepository",
                        lambda _db: _RepoDeUmaInstancia(inst))
    monkeypatch.setattr("app.api.v1.routes.whatsapp.settings.WAHA_SESSAO_RESUMO",
                        "mkd-resumo", raising=False)

    rota._tratar_mensagem(db, inst.nome_instancia, {
        "from": origem.jid, "body": "Oferta https://shopee.com.br/x", "fromMe": False,
    })
    assert db.query(MonitoramentoCaptura).filter(
        MonitoramentoCaptura.monitoramento_id == m.id).count() == 0


class _RepoDeUmaInstancia:
    def __init__(self, instancia):
        self.instancia = instancia

    def por_nome(self, nome):
        return self.instancia if nome == self.instancia.nome_instancia else None


def test_uma_sessao_escuta_o_grupo_nao_todas(db):
    """
    Com dois números no mesmo grupo, deixar os dois assinando `message`
    dobraria o conteúdo de terceiro que chega ao backend sem ganho — a dedup
    por hash descartaria a segunda cópia de qualquer forma.
    """
    user, inst, origem, _d, campanha = _cenario(db)
    segunda = WhatsappInstancia(user_id=user.id,
                                nome_instancia=f"mkdtst{uuid.uuid4().hex[:8]}",
                                status="conectada")
    db.add(segunda); db.flush()
    db.add(WhatsappGrupoInstancia(grupo_id=origem.id, instancia_id=segunda.id))
    db.commit()

    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id, ativo=True)
    db.commit()

    assert m.instancia_id in (inst.id, segunda.id)
    precisa = svc.sessoes_que_precisam_de_message(user.id)
    assert sum(1 for v in precisa.values() if v) == 1, \
        "mais de uma sessão passaria a receber conteúdo do grupo"


def test_sessao_desconectada_nao_e_preferida_como_ouvinte(db):
    user, inst, origem, _d, campanha = _cenario(db)
    inst.status = "desconectada"
    db.add(inst)
    conectada = WhatsappInstancia(user_id=user.id,
                                  nome_instancia=f"mkdtst{uuid.uuid4().hex[:8]}",
                                  status="conectada")
    db.add(conectada); db.flush()
    db.add(WhatsappGrupoInstancia(grupo_id=origem.id, instancia_id=conectada.id))
    db.commit()

    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id)
    db.commit()
    assert m.instancia_id == conectada.id


def test_payloads_das_rotas_batem_com_os_schemas(db):
    """
    O `response_model` só valida na fronteira HTTP e os testes chamam a função
    direto. Na F7 um `Dict[str, ...]` contra chave int só apareceu em produção
    de mentira — validar aqui é o que evita repetir.
    """
    from app.api.v1.routes.monitoramentos import _out, capturas as rota_capturas
    from app.schemas.monitoramentos import CapturasOut, MonitoramentoOut

    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id,
                  ativo=True, palavras_chave=["fone"])
    db.commit()
    svc.capturar(m, "FONE https://shopee.com.br/f", "https://shopee.com.br/f")
    db.commit()

    saida = MonitoramentoOut.model_validate(_out(db, m))
    assert saida.total_capturas == 1
    assert saida.grupo_origem == origem.nome

    CapturasOut.model_validate(rota_capturas(m=m, db=db))


def test_reabrir_devolve_captura_em_erro_para_a_fila(db):
    """Indisponibilidade passageira da Shopee não pode matar a oferta em
    definitivo: o status trava em `erro` e o repost cai na dedup, então ela
    nunca mais viraria captura nova."""
    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id, ativo=True)
    db.commit()
    captura = svc.capturar(m, "Oferta https://shopee.com.br/z", "https://shopee.com.br/z")
    svc.marcar_erro(captura, "Shopee fora do ar")
    db.commit()
    assert svc.reivindicar(captura.id) is False

    svc.reabrir(captura)
    db.commit()
    assert captura.motivo is None
    assert svc.reivindicar(captura.id) is True


def test_destravar_devolve_captura_presa_em_replicando(db):
    """`task_acks_late` reentrega a task se o worker morrer, mas o claim já foi
    feito e a reentrega não reivindica de novo — a captura ficaria invisível
    para sempre, nem replicada nem em erro."""
    from sqlalchemy import text as _sql

    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id, ativo=True)
    db.commit()
    captura = svc.capturar(m, "Oferta https://shopee.com.br/w", "https://shopee.com.br/w")
    db.commit()
    assert svc.reivindicar(captura.id) is True

    # Recente demais: não destrava ESTA (um worker pode estar trabalhando nela).
    # A asserção é sobre a captura, não sobre a contagem: o banco de teste é
    # compartilhado e acumula linhas de outras execuções.
    svc.destravar_replicando(minutos=30)
    db.refresh(captura)
    assert captura.status == "replicando"

    db.execute(_sql("UPDATE monitoramento_capturas SET criado_em = NOW() - INTERVAL "
                    "'2 hours' WHERE id = :id"), {"id": captura.id})
    db.commit()

    svc.destravar_replicando(minutos=30)
    db.refresh(captura)
    assert captura.status == "capturada"
    assert svc.reivindicar(captura.id) is True


def test_patch_com_null_explicito_nao_derruba_a_rota(db):
    """`exclude_unset` mantém a chave quando o cliente manda `null`, e
    `setattr(m, "nome", None)` numa coluna NOT NULL virava 500 no commit."""
    from types import SimpleNamespace

    from app.api.v1.routes.monitoramentos import atualizar
    from app.schemas.monitoramentos import MonitoramentoAtualizar

    user, _i, origem, _d, campanha = _cenario(db)
    svc = MonitoramentoService(db, plan_limit_monitoramentos=3)
    m = svc.criar(user.id, "M", origem.id, destino_campanha_id=campanha.id)
    db.commit()

    payload = MonitoramentoAtualizar.model_validate({"nome": None, "ativo": None,
                                                     "somente_com_link": False})
    saida = atualizar(payload=payload, request=SimpleNamespace(),
                      m=m, current_user=user, db=db)
    assert saida.nome == "M"              # null = "não mexer"
    assert saida.ativo is False
    assert saida.somente_com_link is False
