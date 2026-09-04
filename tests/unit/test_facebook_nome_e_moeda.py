"""
Nome e moeda da conta de anúncio — a regressão que fez a lista voltar a
exibir `act_266908603365617` cru.

O metadado só nascia no momento da SELEÇÃO: quem conectou antes da coluna
existir (ou reconectou depois, o que reescreve a integração) ficava com id cru
para sempre, porque o `/status` — de propósito — não chama a Graph API.

Dois contratos são testados aqui:

  1. a leitura tolera os DOIS formatos gravados na mesma coluna (o de agora,
     com moeda, e o original só-nome). Reescrever tudo exigiria migration de
     dado, e uma leitura estrita apagaria o nome de quem já estava conectada;
  2. a resolução faz MERGE. Conta que saiu da Graph (deixou de ser
     compartilhada com o app) continua selecionada — substituir o dict inteiro
     apagaria o nome dela e ela voltaria a aparecer como `act_...`.
"""
import json

import pytest

from app.models.facebook_integration import FacebookIntegration
from app.schemas.facebook_integration import FacebookAdAccountRef


def _integracao(names_json=None, ids=("act_1",), legado_id=None, legado_nome=None):
    i = FacebookIntegration()
    # Campos obrigatórios do response — o serviço valida o modelo inteiro.
    i.id, i.user_id, i.is_active = 1, 1, True
    i.ad_accounts_json = json.dumps(list(ids))
    i.ad_accounts_names_json = names_json
    i.ad_account_id = legado_id
    i.ad_account_name = legado_nome
    return i


def test_le_formato_antigo_so_com_nome():
    i = _integracao(json.dumps({"act_1": "Ivson Alves"}))
    assert i.account_meta_dict() == {"act_1": {"name": "Ivson Alves", "currency": None}}
    assert i.account_names_dict() == {"act_1": "Ivson Alves"}


def test_le_formato_novo_com_moeda():
    i = _integracao(json.dumps({"act_1": {"name": "Ivson Alves", "currency": "BRL"}}))
    assert i.account_meta_dict() == {"act_1": {"name": "Ivson Alves", "currency": "BRL"}}


def test_json_corrompido_nao_derruba_a_tela():
    """A coluna é lida no `/status`, que abre a tela de Configurações. Uma
    exceção aqui trocaria a lista por um erro em vez de por ids crus."""
    for lixo in ("{não é json", json.dumps(["act_1"]), json.dumps("texto")):
        assert _integracao(lixo).account_meta_dict() == {}


def test_entrada_sem_nome_e_sem_moeda_nao_vira_chave():
    """Ausência no dict é o que significa 'desconhecido' para o `/status` —
    uma chave com valores nulos mentiria dizendo que o nome foi resolvido."""
    i = _integracao(json.dumps({"act_1": {"name": None, "currency": None}}))
    assert i.account_meta_dict() == {}


def test_selecao_persiste_nome_e_moeda(monkeypatch):
    from app.services.facebook_integration_service import FacebookIntegrationService

    integracao = _integracao(None, ids=())
    gravado = {}

    class _Repo:
        def get_by_user_id(self, user_id):
            return integracao

        def set_ad_accounts(self, user_id, ids, names=None):
            gravado["ids"] = ids
            gravado["names"] = names
            integracao.ad_accounts_json = json.dumps(ids)
            if names is not None:
                integracao.ad_accounts_names_json = json.dumps(names)
            return integracao

    class _Db:
        def commit(self):
            pass

        def refresh(self, _):
            pass

    servico = FacebookIntegrationService.__new__(FacebookIntegrationService)
    servico.repo = _Repo()
    servico.db = _Db()
    monkeypatch.setattr(servico, "resolve_connection_state", lambda uid: "conectado")

    servico.select_ad_accounts(
        1,
        ["266908603365617"],
        [FacebookAdAccountRef(id="266908603365617", name="Ivson Alves", currency="BRL")],
    )

    # Normaliza para "act_" — a Graph só aceita o id completo nas chamadas.
    assert gravado["ids"] == ["act_266908603365617"]
    assert gravado["names"] == {
        "act_266908603365617": {"name": "Ivson Alves", "currency": "BRL"}
    }


def test_status_cai_no_nome_legado_quando_o_dict_esta_vazio():
    """Seleção antiga (uma conta só, na coluna legada) não pode virar id cru
    enquanto o nome estiver ali do lado."""
    from app.services.facebook_integration_service import FacebookIntegrationService

    i = _integracao(None, ids=("act_9",), legado_id="act_9", legado_nome="Conta Velha")

    resp = FacebookIntegrationService._to_response(i)
    assert [(c.id, c.name) for c in resp.ad_accounts] == [("act_9", "Conta Velha")]


@pytest.mark.asyncio
async def test_resolver_nomes_faz_merge_e_nao_apaga_conta_fora_da_graph(monkeypatch):
    from app.schemas.facebook_integration import FacebookAdAccount
    from app.services.facebook_integration_service import FacebookIntegrationService

    # act_2 continua selecionada, mas sumiu da Graph (deixou de ser
    # compartilhada com o app) — o nome dela já está gravado.
    integracao = _integracao(
        json.dumps({"act_2": {"name": "Antiga", "currency": "BRL"}}),
        ids=("act_1", "act_2"),
    )
    gravado = {}

    class _Repo:
        def get_by_user_id(self, user_id):
            return integracao

        def set_ad_accounts(self, user_id, ids, names=None):
            gravado["ids"] = ids
            gravado["names"] = names
            return integracao

    class _Db:
        def commit(self):
            pass

    servico = FacebookIntegrationService.__new__(FacebookIntegrationService)
    servico.repo = _Repo()
    servico.db = _Db()

    async def _lista(_user_id):
        return [
            FacebookAdAccount(
                account_id="1", name="Ivson Alves", currency="BRL", id="act_1"
            )
        ]

    monkeypatch.setattr(servico, "list_ad_accounts", _lista)
    monkeypatch.setattr(servico, "get_status", lambda uid: None)

    await servico.resolver_nomes_das_selecionadas(1)

    assert gravado["names"] == {
        "act_1": {"name": "Ivson Alves", "currency": "BRL"},
        "act_2": {"name": "Antiga", "currency": "BRL"},
    }
    assert gravado["ids"] == ["act_1", "act_2"]


@pytest.mark.asyncio
async def test_resolver_nomes_sem_selecao_nao_chama_a_graph(monkeypatch):
    """Sem conta selecionada não há nome a resolver — bater na Graph seria
    custo puro, e é justamente o custo que tiramos do caminho da tela."""
    from app.services.facebook_integration_service import FacebookIntegrationService

    integracao = _integracao(None, ids=())
    chamou = {"graph": False}

    class _Repo:
        def get_by_user_id(self, user_id):
            return integracao

    async def _lista(_user_id):
        chamou["graph"] = True
        return []

    servico = FacebookIntegrationService.__new__(FacebookIntegrationService)
    servico.repo = _Repo()
    servico.db = None
    monkeypatch.setattr(servico, "list_ad_accounts", _lista)
    monkeypatch.setattr(servico, "get_status", lambda uid: "status")

    assert await servico.resolver_nomes_das_selecionadas(1) == "status"
    assert chamou["graph"] is False
