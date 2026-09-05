"""
Rodada de correções das campanhas de grupos (documento delta 04/09, migration 080).

Os invariantes que esta rodada cria e que não podem regredir:

  * **o telefone chega ao evento.** O webhook lê identidade e telefone como
    campos SEPARADOS. Colapsá-los num só foi o bug: em homologação, 49 de 49
    eventos nasceram `identificador_tipo='lid'` e a exportação saiu com a
    coluna telefone vazia em 100% das linhas;
  * **"Cheio" e "Aberto" são dois eixos.** `aberto` é a decisão da usuária;
    `cheio` é a ocupação com override manual por cima. O grupo entra na
    rotação quando está aberto E não cheio;
  * **excluir campanha é terminal e desarma a fila** — e o `/g/{slug}` dela
    passa a responder "campanha encerrada", nunca 404;
  * **duplicar não copia grupo, anúncio nem Sub ID** — os dois últimos são
    invariantes de dinheiro;
  * **Sub ID vinculado à mão soma no total sem duplicar** o do grupo.
"""
import uuid
from datetime import date

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

from fastapi import HTTPException  # noqa: E402

from app.models.campanha_grupos import (  # noqa: E402
    CAMPANHA_ENCERRADA, Campanha, CampanhaGrupo, CampanhaNumero, CampanhaSubId,
)
from app.models.campanha_link import CampanhaLink  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.whatsapp_grupos import (  # noqa: E402
    WhatsappGrupo, WhatsappGrupoInstancia, WhatsappInstancia,
)
from app.repositories.campanha_link_repository import CampanhaLinkRepository  # noqa: E402
from app.services.campanha_grupos_service import (  # noqa: E402
    CampanhaGruposService, SubIdEmUso,
)
from app.services.campanha_link_service import (  # noqa: E402
    CampanhaEncerrada, CampanhaLinkService,
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
    user = User(email=f"r80-{suf}@x.com", hashed_password="x")
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


def _vinculo(db, campanha, grupo):
    return (
        db.query(CampanhaGrupo)
        .filter(CampanhaGrupo.campanha_id == campanha.id,
                CampanhaGrupo.grupo_id == grupo.id)
        .one()
    )


# --- BLOQUEADOR: o telefone chega ao evento ---------------------------------


def test_webhook_le_telefone_e_lid_como_campos_separados():
    """
    O caso real de produção que nenhum teste cobria.

    Em grupo com endereçamento LID o payload traz o `JID` como `…@lid` **e** o
    telefone ao lado. Como `campo()` devolve o primeiro nome presente e `JID`
    vinha antes de `PhoneNumber`, o LID sempre ganhava — 49 de 49 eventos
    gravados em homologação nasceram sem telefone.
    """
    from app.api.v1.routes.whatsapp import _participante

    identidade, telefone = _participante({
        "JID": "84729130@lid",
        "PhoneNumber": "5511999998888@s.whatsapp.net",
    })
    # A IDENTIDADE continua sendo o LID: é ela que vira `identificador_hash`, a
    # chave que casa entrada com saída. Trocá-la invalidaria o pareamento de
    # todos os eventos já gravados.
    assert identidade == "84729130@lid"
    assert telefone == "5511999998888@s.whatsapp.net"


def test_webhook_sem_telefone_nao_inventa_um_a_partir_do_lid():
    """LID é id opaco e não disca — telefone None, coluna vazia. É a verdade."""
    from app.api.v1.routes.whatsapp import _participante

    assert _participante({"JID": "84729130@lid"}) == ("84729130@lid", None)


def test_classificar_prefere_o_telefone_ao_sufixo_do_jid():
    """
    `classificar` adivinhava o tipo pelo sufixo do identificador. Com LID
    endereçando o participante, isso marcava 'lid' mesmo com o número
    disponível no MESMO payload.
    """
    assert classificar("84729130@lid", "5511999998888@s.whatsapp.net") == (
        "5511999998888@s.whatsapp.net", "telefone",
    )
    assert classificar("84729130@lid", None) == ("84729130@lid", "lid")
    assert classificar("5511999998888@c.us", None) == ("5511999998888@c.us", "telefone")


# --- Cheio × Aberto ---------------------------------------------------------


def test_override_de_cheio_tira_o_grupo_da_rotacao_antes_de_lotar(db):
    """Segurar um grupo ANTES de encher é um dos dois casos que o override existe
    para resolver — e não pode exigir fechar o `aberto`, que é outra decisão."""
    user, campanha, numeros, grupos = _cenario(db, participantes=(10, 10))
    repo = CampanhaLinkRepository(db)
    assert repo.escolher_grupo(campanha.id, aleatorio=False)[0] == grupos[0].id

    _vinculo(db, campanha, grupos[0]).cheio_override = True
    db.commit()

    assert repo.escolher_grupo(campanha.id, aleatorio=False)[0] == grupos[1].id
    # E `aberto` continua intacto: quem tirou da rotação foi `cheio`.
    assert _vinculo(db, campanha, grupos[0]).aberto is True


def test_override_de_nao_cheio_destrava_grupo_com_contagem_desatualizada(db):
    """O outro caso real: o WhatsApp não atualizou a contagem e o grupo, que tem
    vaga de verdade, ficou fora da rotação."""
    user, campanha, numeros, grupos = _cenario(db, participantes=(1024, 1024))
    repo = CampanhaLinkRepository(db)
    assert repo.escolher_grupo(campanha.id, aleatorio=False) is None

    _vinculo(db, campanha, grupos[1]).cheio_override = False
    db.commit()

    assert repo.escolher_grupo(campanha.id, aleatorio=False)[0] == grupos[1].id


def test_sem_vaga_prende_o_lotado_como_cheio_e_nao_fecha_o_aberto(db):
    """
    Antes, a varredura escrevia `aberto=False` nos lotados — desfazia a escolha
    da usuária por baixo, e como "cheio" só existia derivado o grupo com
    946/900 aparecia "Aberto" na tela para sempre.
    """
    from app.services.campanha_link_service import SemVaga

    user, campanha, numeros, grupos = _cenario(db, participantes=(1024, 1024))
    campanha.reabertura_automatica = False
    link = CampanhaLinkService(db).obter_ou_criar(campanha)
    db.commit()

    with pytest.raises(SemVaga):
        CampanhaLinkService(db).rotear(link.slug, ip=None, user_agent=None, referer=None)

    for g in grupos:
        v = _vinculo(db, campanha, g)
        assert v.cheio_override is True, "grupo lotado não foi marcado como cheio"
        assert v.aberto is True, "a escolha da usuária em `aberto` foi desfeita"


def test_a_tela_recebe_cheio_e_teto_prontos_do_backend(db):
    """
    A regra de lotação é a MESMA que decide a rotação. Recalculá-la em
    JavaScript é como a tela passou a dizer "Aberto" para um grupo que o
    roteador já tinha parado de escolher.
    """
    from app.api.v1.routes.campanhas_grupos import _grupos_out

    user, campanha, numeros, grupos = _cenario(db, participantes=(946, 10))
    campanha.limite_participantes = 900
    db.commit()

    por_id = {g.grupo_id: g for g in _grupos_out(CampanhaGruposService(db), campanha)}
    assert por_id[grupos[0].id].teto == 900
    assert por_id[grupos[0].id].cheio is True
    assert por_id[grupos[0].id].cheio_override is None   # veio da ocupação
    assert por_id[grupos[1].id].cheio is False


# --- excluir ----------------------------------------------------------------


def test_excluir_encerra_a_campanha_e_ela_some_da_listagem(db):
    user, campanha, numeros, grupos = _cenario(db)
    servico = CampanhaGruposService(db)
    servico.excluir(campanha)

    assert campanha.status == CAMPANHA_ENCERRADA
    campanhas, _ = servico.listar(user.id, incluir_arquivadas=True)
    assert campanha.id not in {c.id for c in campanhas}
    # E o detalhe some junto: `obter` devolve None, que a rota vira 404.
    assert servico.obter(user.id, campanha.id) is None


def test_excluir_cancela_as_execucoes_pendentes_dos_roteiros(db):
    """
    Não existe revoke de Celery no módulo — o cancelamento é por ESTADO. Sem
    isso, a fila continua disparando para os grupos de uma campanha que a
    usuária acabou de excluir.
    """
    from app.models.roteiro import (
        EXEC_AGENDADA, EXEC_CANCELADA, Roteiro, RoteiroExecucao,
    )

    user, campanha, numeros, grupos = _cenario(db)
    roteiro = Roteiro(user_id=user.id, campanha_id=campanha.id, nome="R")
    db.add(roteiro); db.flush()
    execucao = RoteiroExecucao(roteiro_id=roteiro.id, user_id=user.id,
                               data_ancora=date.today(), status=EXEC_AGENDADA)
    db.add(execucao); db.commit()

    CampanhaGruposService(db).excluir(campanha)
    db.refresh(execucao)
    assert execucao.status == EXEC_CANCELADA


def test_link_de_campanha_excluida_responde_encerrada_e_nunca_404(db):
    """
    O anúncio que aponta para o link continua veiculando por dias depois da
    exclusão. Um 404 faz o Meta tratar o destino como quebrado, além de mostrar
    tela de erro a quem clicou.
    """
    user, campanha, numeros, grupos = _cenario(db)
    link = CampanhaLinkService(db).obter_ou_criar(campanha)
    slug = link.slug
    CampanhaGruposService(db).excluir(campanha)

    with pytest.raises(CampanhaEncerrada):
        CampanhaLinkService(db).rotear(slug, ip=None, user_agent=None, referer=None)


def test_excluir_nao_leva_os_grupos_nem_o_sub_id(db):
    """Os grupos continuam nos Números e a comissão atribuída permanece — nada
    disso pende da campanha."""
    user, campanha, numeros, grupos = _cenario(db)
    # Mapa, não lista: `query().all()` sem ORDER BY não garante ordem no
    # Postgres, e comparar listas fazia o teste falhar por sorte do plano.
    sub_ids = {g.id: g.sub_id for g in grupos}
    CampanhaGruposService(db).excluir(campanha)

    vivos = db.query(WhatsappGrupo).filter(WhatsappGrupo.user_id == user.id).all()
    assert {g.id for g in vivos} == set(sub_ids)
    assert {g.id: g.sub_id for g in vivos} == sub_ids


# --- duplicar ---------------------------------------------------------------


def test_duplicar_copia_a_configuracao_e_os_numeros_mas_nao_os_grupos(db):
    user, campanha, numeros, grupos = _cenario(db)
    campanha.limite_participantes = 900
    campanha.estrategia_entrada = "aleatoria"
    campanha.prefixo = "🔥 "
    campanha.abertura_automatica = False
    db.commit()

    servico = CampanhaGruposService(db)
    nova = servico.duplicar(campanha)

    assert nova.id != campanha.id
    assert nova.nome == f"{campanha.nome} (cópia)"
    assert nova.limite_participantes == 900
    assert nova.estrategia_entrada == "aleatoria"
    assert nova.prefixo == "🔥 "
    assert nova.abertura_automatica is False
    # Grupos NÃO: a campanha nova é para outros grupos.
    assert servico.total_de_grupos(nova) == 0
    # Números SIM: são "por onde esta campanha dispara", não vínculo exclusivo.
    assert set(servico.repo_numeros.instancia_ids(nova.id)) == {n.id for n in numeros}


def test_duplicar_gera_slug_novo_e_copia_a_previa(db):
    """O slug tem UNIQUE (063) — copiar o original quebra. A prévia e o pixel,
    que são o trabalho manual da afiliada, vêm junto."""
    user, campanha, numeros, grupos = _cenario(db)
    servico_link = CampanhaLinkService(db)
    link = servico_link.obter_ou_criar(campanha)
    servico_link.atualizar(link, {"titulo_previa": "Entra no grupo",
                                  "pixel_facebook_id": "123456"})

    nova = CampanhaGruposService(db).duplicar(campanha)
    novo_link = db.query(CampanhaLink).filter(
        CampanhaLink.campanha_id == nova.id).one()

    assert novo_link.slug != link.slug
    assert novo_link.titulo_previa == "Entra no grupo"
    assert novo_link.pixel_facebook_id == "123456"


def test_duplicar_nao_copia_sub_id_vinculado(db):
    """Sub ID em duas campanhas somaria a MESMA comissão nas duas telas."""
    user, campanha, numeros, grupos = _cenario(db)
    db.add(CampanhaSubId(campanha_id=campanha.id, sub_id="promo1"))
    db.commit()

    nova = CampanhaGruposService(db).duplicar(campanha)
    assert CampanhaGruposService(db).sub_ids(nova) == []


# --- Sub IDs da campanha ----------------------------------------------------


def test_sub_id_de_grupo_da_campanha_nao_pode_ser_vinculado_a_mao(db):
    """Ele já entra pela linha do grupo — vincular contaria duas vezes."""
    user, campanha, numeros, grupos = _cenario(db)
    servico = CampanhaGruposService(db)

    with pytest.raises(SubIdEmUso) as erro:
        servico.definir_sub_ids(campanha, [grupos[0].sub_id])
    assert grupos[0].sub_id in erro.value.motivos
    assert "grupo" in erro.value.motivos[grupos[0].sub_id]


def test_sub_id_de_outra_campanha_de_grupos_e_bloqueado(db):
    user, campanha, numeros, grupos = _cenario(db)
    outra = Campanha(user_id=user.id, nome="Outra")
    db.add(outra); db.flush()
    db.add(CampanhaSubId(campanha_id=outra.id, sub_id="promo1"))
    db.commit()

    with pytest.raises(SubIdEmUso):
        CampanhaGruposService(db).definir_sub_ids(campanha, ["promo1"])


def test_sub_id_e_normalizado_antes_de_gravar(db):
    """"WGEA" e "wgea" são o mesmo sub_id; gravar os dois somaria a comissão
    duas vezes na mesma campanha."""
    user, campanha, numeros, grupos = _cenario(db)
    servico = CampanhaGruposService(db)
    servico.definir_sub_ids(campanha, ["PROMO1", "promo1-", " promo1 "])
    assert servico.sub_ids(campanha) == ["promo1"]


def test_definir_sub_ids_substitui_o_conjunto_inteiro(db):
    user, campanha, numeros, grupos = _cenario(db)
    servico = CampanhaGruposService(db)
    servico.definir_sub_ids(campanha, ["a1", "b2"])
    assert servico.sub_ids(campanha) == ["a1", "b2"]
    servico.definir_sub_ids(campanha, ["b2"])
    assert servico.sub_ids(campanha) == ["b2"]


# --- Visão geral: o dia de hoje entra na série ------------------------------


def test_a_serie_inclui_o_dia_de_hoje_marcado_como_parcial(db):
    """
    A janela terminava no último dia FECHADO em Brasília. Campanha que começou
    hoje aparecia com Entradas e Saídas em ZERO enquanto o movimento
    acontecia — foi exatamente o que se viu em homologação, com 18 eventos
    gravados no dia e o gráfico reto no zero.
    """
    from datetime import datetime

    from app.services.admin_metrics_service import BRT, _brt_date
    from app.services.campanha_visao_geral_service import CampanhaVisaoGeralService
    from app.services.grupo_evento_service import GrupoEventoService

    user, campanha, numeros, grupos = _cenario(db)
    GrupoEventoService(db).registrar(user.id, grupos[0].jid, "join",
                                     ["5511900000123@c.us"])
    db.commit()

    hoje = _brt_date(datetime.now(BRT)).isoformat()
    resumo = CampanhaVisaoGeralService(db).resumo(campanha, dias=7)

    assert resumo["periodo"]["fim"] == hoje, "a janela ainda para em ontem"
    assert resumo["entradas"] == 1, "entrada de hoje não entrou no total"
    ponto = next(p for p in resumo["serie"] if p["data"] == hoje)
    assert ponto["entradas"] == 1
    # Marcado como parcial para a tela poder desenhá-lo diferente: um dia em
    # curso ao lado de dias inteiros parece queda.
    assert ponto["parcial"] is True
    assert all(not p["parcial"] for p in resumo["serie"] if p["data"] != hoje)


def test_estado_dos_grupos_respeita_o_override_de_cheio(db):
    """O painel conta "disponível" com a MESMA regra que o roteador aceita —
    incluindo o override manual. Recalcular a lotação aqui faria a tela dizer
    que há vaga num grupo que ela segurou à mão."""
    from app.services.campanha_visao_geral_service import CampanhaVisaoGeralService

    user, campanha, numeros, grupos = _cenario(db)
    antes = CampanhaVisaoGeralService(db).resumo(campanha, dias=7)["grupos"]
    assert antes["cheios"] == 0 and antes["disponiveis"] == 2

    _vinculo(db, campanha, grupos[0]).cheio_override = True
    db.commit()

    depois = CampanhaVisaoGeralService(db).resumo(campanha, dias=7)["grupos"]
    assert depois["cheios"] == 1
    assert depois["disponiveis"] == 1


# --- KPIs da tela de Anúncios ------------------------------------------------


def test_gasto_de_campanha_de_grupo_sai_do_lucro_e_do_roas_mas_fica_no_gasto(db):
    """
    Campanha de grupo não tem comissão atribuída — o rastreio é o link de
    entrada, não o Sub ID. Deixar o gasto dela no Lucro e no ROAS Real afunda o
    número principal da tela com um prejuízo que não existe: em homologação
    eram R$1.223 de campanha de grupo dentro de "Lucro −R$5.084,43" e
    "ROAS 0,41x".

    O card Gasto continua somando TUDO — é o número que a afiliada confere
    contra o Meta, e omitir parte dele faria a tela discordar da plataforma.
    """
    from datetime import date as _date

    import json

    from app.models.campaign import Campaign, CampaignDailyInsight
    from app.models.campanha_anuncio import CampanhaAnuncio
    from app.models.facebook_integration import FacebookIntegration
    from app.repositories.campaign_repository import CampaignRepository
    from app.services.campaign_service import CampaignService

    user, campanha, numeros, grupos = _cenario(db)
    hoje = _date.today()
    # Sem integração do Facebook a listagem devolve vazio — o filtro por conta
    # de anúncio é aplicado antes de qualquer métrica.
    db.add(FacebookIntegration(user_id=user.id, encrypted_access_token="x",
                               ad_accounts_json=json.dumps(["act_1"])))

    def _anuncio(nome, gasto):
        c = Campaign(user_id=user.id, fb_campaign_id=f"fb{uuid.uuid4().hex[:8]}",
                     name=nome, status="ACTIVE", ad_account_id="act_1")
        db.add(c); db.flush()
        db.add(CampaignDailyInsight(user_id=user.id, campaign_id=c.id, date=hoje,
                                    spend=gasto, clicks=10, impressions=100))
        return c

    # Uma campanha comum (sem Sub ID também, para isolar a variável) e uma
    # vinculada a grupo.
    _anuncio("Trafego direto", 100.0)
    de_grupo = _anuncio("Ofertas da Beatriz", 400.0)
    db.add(CampanhaAnuncio(campanha_id=campanha.id, campaign_id=de_grupo.id))
    db.commit()

    r = CampaignService(CampaignRepository(db)).list_campaigns(
        user.id, start_date=hoje, end_date=hoje,
    )
    assert r.kpis.total_spend == pytest.approx(500.0), "o card Gasto tem que somar tudo"
    # Só os R$100 da campanha comum entram no prejuízo.
    assert r.kpis.total_profit == pytest.approx(-100.0)
    assert r.kpis.avg_roas == 0.0


# --- FRONTEND_URL: o link tem que apontar para o ambiente certo --------------


def test_frontend_url_deriva_do_ambiente_quando_nao_ha_env_explicita():
    """
    O default fixo em produção falhava do pior jeito: em homologação a afiliada
    copiava um link para `marketdash.com.br/g/{slug}`, onde a rota não existe, e
    o sintoma chegava como "a página do grupo não funciona" — sem nada, em lugar
    nenhum, apontando para uma variável de ambiente.
    """
    from app.core.config import Settings

    assert Settings(ENVIRONMENT="homologation", FRONTEND_URL=None).frontend_url == (
        "https://hml.marketdash.com.br"
    )
    assert Settings(ENVIRONMENT="production", FRONTEND_URL=None).frontend_url == (
        "https://marketdash.com.br"
    )
    # Ambiente desconhecido cai em produção: é o menos errado dos dois lados —
    # link de produção num ambiente novo é visível, o contrário vaza hml.
    assert Settings(ENVIRONMENT="qualquer-coisa", FRONTEND_URL=None).frontend_url == (
        "https://marketdash.com.br"
    )


def test_frontend_url_explicita_vence_e_avisa_quando_nao_bate(caplog):
    """A env explícita continua mandando — é ela que permite domínio próprio.
    Mas apontar hml para o domínio de produção é sempre erro, e antes era
    SILENCIOSO: nada quebrava, o link só levava a lugar nenhum."""
    import logging

    from app.core.config import Settings

    with caplog.at_level(logging.WARNING):
        s = Settings(ENVIRONMENT="homologation",
                     FRONTEND_URL="https://marketdash.com.br")
    assert s.frontend_url == "https://marketdash.com.br"
    assert any("FRONTEND_URL" in r.message for r in caplog.records), (
        "incoerência entre FRONTEND_URL e ENVIRONMENT precisa gritar no boot"
    )


def test_frontend_url_coerente_nao_avisa(caplog):
    import logging

    from app.core.config import Settings

    with caplog.at_level(logging.WARNING):
        Settings(ENVIRONMENT="homologation",
                 FRONTEND_URL="https://hml.marketdash.com.br")
    assert not any("FRONTEND_URL" in r.message for r in caplog.records)


# --- busca de Sub ID: janela de 30 dias -------------------------------------


def test_busca_de_sub_id_so_conta_os_ultimos_30_dias(db):
    """
    A query varria o histórico inteiro de `dataset_rows_v2` a cada abertura do
    modal, e o tempo crescia com a conta: quem vende mais esperava mais —
    justamente quem mais usa a tela.
    """
    from datetime import date as _date, timedelta

    from app.models.dataset import Dataset
    from app.models.dataset_row import DatasetRow
    from app.repositories.campaign_repository import CampaignRepository

    user, campanha, numeros, grupos = _cenario(db)
    ds = Dataset(user_id=user.id, filename="x.csv", status="completed")
    db.add(ds); db.flush()

    def _venda(sub_id, quando, order_id):
        db.add(DatasetRow(dataset_id=ds.id, user_id=user.id, sub_id1=sub_id,
                          order_id=order_id, commission=10.0, revenue=100.0,
                          product="Produto", status="completed", date=quando))

    hoje = _date.today()
    _venda("recente", hoje, "P1")
    _venda("antigo", hoje - timedelta(days=120), "P2")
    db.commit()

    por_sub = {
        r["sub_id"]: r
        for r in CampaignRepository(db).sub_id_sales_summary(user.id, dias=30)
    }
    assert "recente" in por_sub
    assert "antigo" not in por_sub, "venda de 120 dias atrás entrou na janela de 30"

    # `dias=0` continua devolvendo o histórico — o modal de grupos usa período
    # próprio e precisa desse caminho.
    todos = {r["sub_id"] for r in CampaignRepository(db).sub_id_sales_summary(user.id, dias=0)}
    assert {"recente", "antigo"} <= todos
