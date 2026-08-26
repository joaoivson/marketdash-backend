"""
F5: busca de ofertas (productOfferV2) e integrações de marketplace.

Dois invariantes: sem termo a tela abre a VITRINE (medido em 26/08/2026 contra
a API real — o comentário antigo dizia que vinha vazio, e por isso a tela abria
sem nada), e a credencial é sempre da aluna: a comissão segue quem assina.
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.integracao import Integracao
from app.models.shopee_integration import ShopeeIntegration
from app.services.integracao_service import (
    EscolhaNecessaria, IntegracaoNaoEncontrada, IntegracaoService,
    ProvedorInvalido, provedor_da_url,
)
from app.services.oferta_service import BuscaInvalida, OfertaService, _normalizar

USUARIA, OUTRA = 1, 2


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Integracao.__table__.create(engine)
    ShopeeIntegration.__table__.create(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


# --- integrações -------------------------------------------------------------

@pytest.mark.parametrize("url,esperado", [
    ("https://shopee.com.br/produto-i.123.456", "shopee"),
    ("https://br.shopee.com.br/x", "shopee"),
    ("https://s.shopee.com.br/abc", "shopee"),
    ("https://www.amazon.com.br/x", None),
    ("", None),
    ("não é url", None),
])
def test_provedor_vem_da_url_do_produto(url, esperado):
    assert provedor_da_url(url) == esperado


def test_salvar_grava_nas_duas_tabelas_durante_a_migracao(db):
    svc = IntegracaoService(db)
    svc.salvar(USUARIA, "shopee", "Principal", "18191340007", "segredo")

    assert db.query(Integracao).count() == 1
    antiga = db.query(ShopeeIntegration).one()      # dupla escrita (deploy A)
    assert antiga.app_id == "18191340007"
    # e a credencial volta decifrada
    cred = svc.credenciais_de(db.query(Integracao).one())
    assert cred == {"app_id": "18191340007", "senha": "segredo"}


def test_credencial_do_backfill_tambem_e_lida(db):
    """Formato do backfill da 062: JSON cru com o campo interno já cifrado."""
    from app.core.encryption import encrypt_value

    db.add(Integracao(user_id=USUARIA, provedor="shopee", label="principal",
                      credenciais=json.dumps({
                          "app_id": "999",
                          "encrypted_password": encrypt_value("senha-antiga"),
                      })))
    db.commit()
    cred = IntegracaoService(db).credenciais_de(db.query(Integracao).one())
    assert cred == {"app_id": "999", "senha": "senha-antiga"}


def test_app_id_nao_numerico_e_recusado_antes_de_falhar_opaco(db):
    with pytest.raises(ProvedorInvalido):
        IntegracaoService(db).salvar(USUARIA, "shopee", "x", "ABC123", "s")


def test_marketplace_sem_api_e_recusado(db):
    # Mercado Livre/SHEIN não têm API de afiliado aberta — prometer vira ticket.
    with pytest.raises(ProvedorInvalido):
        IntegracaoService(db).salvar(USUARIA, "mercado_livre", "x", "123", "s")


def test_duas_contas_ativas_exigem_escolha(db):
    svc = IntegracaoService(db)
    svc.salvar(USUARIA, "shopee", "Principal", "111", "a")
    svc.salvar(USUARIA, "shopee", "Backup", "222", "b")
    with pytest.raises(EscolhaNecessaria) as e:
        svc.resolver(USUARIA, "shopee")
    assert sorted(e.value.labels) == ["Backup", "Principal"]

    # desativando uma, a resolução volta a ser automática
    backup = svc.repo.por_label(USUARIA, "shopee", "Backup")
    svc.alternar(backup, False)
    assert svc.resolver(USUARIA, "shopee").label == "Principal"


def test_sem_conta_conectada_diz_isso(db):
    with pytest.raises(IntegracaoNaoEncontrada):
        IntegracaoService(db).resolver(USUARIA, "shopee")


def test_remover_ultima_conta_limpa_a_tabela_antiga(db):
    svc = IntegracaoService(db)
    svc.salvar(USUARIA, "shopee", "Principal", "111", "a")
    svc.salvar(USUARIA, "shopee", "Backup", "222", "b")

    svc.remover(svc.repo.por_label(USUARIA, "shopee", "Backup"))
    assert db.query(ShopeeIntegration).count() == 1   # ainda há outra conta
    svc.remover(svc.repo.por_label(USUARIA, "shopee", "Principal"))
    assert db.query(ShopeeIntegration).count() == 0   # era a última


def test_nao_vaza_integracao_de_outra_usuaria(db):
    svc = IntegracaoService(db)
    i = svc.salvar(USUARIA, "shopee", "Principal", "111", "a")
    assert svc.repo.por_id(OUTRA, i.id) is None


# --- busca de ofertas --------------------------------------------------------

class _ServicoFake(OfertaService):
    def __init__(self, db, resposta):
        super().__init__(db)
        self.resposta = resposta
        self.queries = []

    async def _executar(self, user_id, query, integracao_id):
        self.queries.append(query)
        return self.resposta


def _node(**extra):
    base = {"itemId": 1, "productName": "Fone", "imageUrl": "http://img",
            "priceMin": 49.9, "priceMax": 99.9, "priceDiscountRate": 0.5,
            "commissionRate": 0.08, "commission": 4.0, "sales": 320,
            "shopName": "Loja X", "productLink": "https://shopee.com.br/i.1.2",
            "ratingStar": 4.8}
    base.update(extra)
    return base


def _resposta(nodes, tem_proxima=False):
    return {"data": {"productOfferV2": {
        "nodes": nodes,
        "pageInfo": {"page": 1, "limit": 20, "hasNextPage": tem_proxima},
    }}}


@pytest.mark.asyncio
async def test_busca_normaliza_taxa_em_porcentagem(db):
    svc = _ServicoFake(db, _resposta([_node()]))
    r = await svc.buscar(USUARIA, keyword="fone")
    oferta = r["ofertas"][0]
    assert oferta["comissao_pct"] == pytest.approx(8.0)     # 0.08 → 8%
    assert oferta["desconto_pct"] == pytest.approx(50.0)    # 0.5 → 50%
    assert oferta["preco"] == pytest.approx(49.9)
    assert oferta["url"].startswith("https://shopee.com.br/")


@pytest.mark.asyncio
@pytest.mark.parametrize("termo", ["x", " a "])
async def test_termo_curto_demais_e_recusado_com_motivo(db, termo):
    """Uma letra é engano de digitação, não pedido de vitrine."""
    svc = _ServicoFake(db, _resposta([]))
    with pytest.raises(BuscaInvalida):
        await svc.buscar(USUARIA, keyword=termo)
    assert svc.queries == []          # nem chega a chamar a Shopee


@pytest.mark.asyncio
@pytest.mark.parametrize("termo", [None, "", "   "])
async def test_sem_termo_nenhum_abre_a_VITRINE_em_vez_de_recusar(db, termo):
    """
    Medido contra a API real em 26/08/2026: `productOfferV2` com keyword vazia
    devolve a vitrine da conta. O comentário antigo dizia que vinha vazio, e por
    isso a tela abria com um campo de busca e nada mais.

    A distinção é por CONTEÚDO, não por `is None`: a tela manda `q=""`, e
    depender de o parâmetro sumir da query string fazia a vitrine funcionar por
    acidente da serialização.
    """
    svc = _ServicoFake(db, _resposta([_node()]))
    r = await svc.buscar(USUARIA, keyword=termo)
    assert r["vitrine"] is True
    assert r["termo_usado"] == ""
    assert len(svc.queries) == 1      # chamou a Shopee, sem termo
    assert len(r["ofertas"]) == 1


@pytest.mark.asyncio
async def test_mais_vendidos_ordena_de_fato_por_vendas(db):
    """
    O `sortType: 2` da Shopee é RANKING, não ordenação — medido contra a API
    real, "fone" devolveu 17253, 11876, 5440, 12209… Prometer "mais vendidos"
    na tela só é verdade se a gente ordenar.
    """
    nodes = [_node(item_id=1, sales=10), _node(item_id=2, sales=900),
             _node(item_id=3, sales=50)]
    svc = _ServicoFake(db, _resposta(nodes))
    r = await svc.buscar(USUARIA, keyword="fone", ordenacao="mais_vendidos")
    assert [o["vendas"] for o in r["ofertas"]] == [900, 50, 10]


@pytest.mark.asyncio
async def test_categoria_vira_keyword_porque_a_api_exige(db):
    svc = _ServicoFake(db, _resposta([_node()]))
    await svc.buscar(USUARIA, categoria="Celulares")
    assert '"Celulares"' in svc.queries[0]


@pytest.mark.asyncio
async def test_filtros_da_pagina_sao_aplicados_aqui(db):
    # A API não filtra por comissão/preço/desconto — a tela diz que o filtro
    # vale sobre a página retornada.
    nodes = [
        _node(itemId=1, commissionRate=0.02),                    # comissão baixa
        _node(itemId=2, commissionRate=0.10, priceMin=500.0),    # cara
        _node(itemId=3, commissionRate=0.10, priceMin=30.0),     # passa
    ]
    svc = _ServicoFake(db, _resposta(nodes))
    r = await svc.buscar(USUARIA, keyword="fone", comissao_minima=5, preco_max=100)
    assert [o["item_id"] for o in r["ofertas"]] == ["3"]
    assert r["total_na_pagina"] == 3      # honesto: 3 vieram, 1 passou


@pytest.mark.asyncio
async def test_retorno_vazio_nao_quebra(db):
    svc = _ServicoFake(db, {"data": {"productOfferV2": None}})
    r = await svc.buscar(USUARIA, keyword="nada")
    assert r["ofertas"] == [] and r["tem_proxima"] is False


def test_node_incompleto_nao_derruba_a_normalizacao():
    o = _normalizar({"itemId": 9, "productName": "Só nome"})
    assert o["preco"] == 0.0 and o["comissao_pct"] == 0.0 and o["url"] == ""
