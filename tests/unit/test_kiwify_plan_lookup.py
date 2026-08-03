"""
Identificação do plano no webhook Kiwify.

Bug real (02/08/2026): cliente nova pagou o Pro Mensal, o painel admin mostrou a
ativação e ela nunca recebeu o e-mail de acesso — o usuário sequer foi criado.

Causa: `_lookup_plan_product` procurava por `Product.product_id`, que na Kiwify é
o UUID de UM único produto ("MarketDash") compartilhado por todos os planos. Quem
distingue Essencial/Pro e mensal/trimestral/anual é o slug do checkout, que vem em
`checkout_link` — e é assim que kiwify_plan_products está chaveada. A busca nunca
casava, e o handler dava `return` ANTES de criar o usuário.
"""
from app.api.v1.routes.kiwify import _plan_lookup_keys, _lookup_plan_product

# Payload real do evento 11 em produção (recortado)
PAYLOAD_RUBIANE = {
    "order_id": "8041a236-3a93-44aa-a9de-253a1ba7f667",
    "order_status": "paid",
    "webhook_event_type": "order_approved",
    "checkout_link": "u12boOS",
    "Product": {
        "product_id": "69e4e540-327f-11f1-835f-fdbfa4ffa967",
        "product_name": "MarketDash",
    },
    "Customer": {"email": "rubianekrisca@hotmail.com", "full_name": "rubiane de melo carvalho"},
    "Subscription": {
        "status": "active",
        "plan": {"id": "993f1ce9-81bd-43e6-99cd-f676de741707", "name": "PRO - Mensal"},
    },
}


class _FakeQuery:
    def __init__(self, tabela, key=None):
        self.tabela = tabela
        self.key = key

    def filter(self, criterio):
        # o critério é `KiwifyPlanProduct.product_id == <valor>`
        valor = criterio.right.value
        return _FakeQuery(self.tabela, valor)

    def first(self):
        return self.tabela.get(self.key)


class _FakeDb:
    """Simula kiwify_plan_products chaveada por slug de checkout (como em produção)."""

    TABELA = {
        "u12boOS": type("Row", (), {"plano": "pro", "periodo": "mensal"})(),
        "uMRfGkI": type("Row", (), {"plano": "essencial", "periodo": "mensal"})(),
    }

    def query(self, _modelo):
        return _FakeQuery(self.TABELA)


# --- chaves candidatas -----------------------------------------------------------


def test_checkout_link_vem_primeiro():
    """O slug identifica o plano; o UUID do produto é o mesmo pra todos."""
    chaves = _plan_lookup_keys(PAYLOAD_RUBIANE)
    assert chaves[0] == "u12boOS"


def test_uuid_do_produto_nao_e_suficiente_sozinho():
    chaves = _plan_lookup_keys(PAYLOAD_RUBIANE)
    uuid_produto = "69e4e540-327f-11f1-835f-fdbfa4ffa967"
    assert uuid_produto in chaves
    assert chaves.index("u12boOS") < chaves.index(uuid_produto)


def test_inclui_id_do_plano_da_assinatura():
    assert "993f1ce9-81bd-43e6-99cd-f676de741707" in _plan_lookup_keys(PAYLOAD_RUBIANE)


def test_checkout_link_como_url_completa():
    payload = dict(PAYLOAD_RUBIANE, checkout_link="https://pay.kiwify.com.br/u12boOS/")
    assert "u12boOS" in _plan_lookup_keys(payload)


def test_payload_com_wrapper_order_tambem_funciona():
    # _extract_order desembrulha antes; aqui garantimos que o dict interno serve
    chaves = _plan_lookup_keys(PAYLOAD_RUBIANE)
    assert chaves


def test_payload_vazio_nao_quebra():
    assert _plan_lookup_keys({}) == []
    assert _plan_lookup_keys({"Product": None, "Subscription": "nao-e-dict"}) == []


# --- lookup ----------------------------------------------------------------------


def test_encontra_pro_mensal_pelo_checkout_link():
    """O caso da Rubiane: antes retornava None e derrubava a compra."""
    row = _lookup_plan_product(_FakeDb(), _plan_lookup_keys(PAYLOAD_RUBIANE))
    assert row is not None
    assert (row.plano, row.periodo) == ("pro", "mensal")


def test_encontra_essencial_mensal():
    payload = dict(PAYLOAD_RUBIANE, checkout_link="uMRfGkI")
    row = _lookup_plan_product(_FakeDb(), _plan_lookup_keys(payload))
    assert (row.plano, row.periodo) == ("essencial", "mensal")


def test_slug_desconhecido_retorna_none():
    payload = dict(PAYLOAD_RUBIANE, checkout_link="slugNovoNaoCadastrado")
    payload["Subscription"] = {"status": "active", "plan": {"id": "outro"}}
    payload["Product"] = {"product_id": "outro-uuid"}
    assert _lookup_plan_product(_FakeDb(), _plan_lookup_keys(payload)) is None


def test_sem_chaves_retorna_none():
    assert _lookup_plan_product(_FakeDb(), []) is None
