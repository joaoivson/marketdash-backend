"""Regressão do bug de perda de dado: sync Shopee não pode mais apagar a janela inteira
antes de reinserir (delete-and-replace). A escrita passa a ser puramente aditiva
(upsert por row_hash), com uma guarda de observação (nunca bloqueio) pra sinalizar
fetches que trouxeram visivelmente menos que o esperado."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BRT = timezone(timedelta(hours=-3))


def _fake_response(nodes):
    return {
        "data": {
            "conversionReport": {
                "pageInfo": {"hasNextPage": False, "scrollId": None},
                "nodes": nodes,
            }
        }
    }


def _fake_node(purchase_dt, order_id="order1", item_id="item1", commission=10.0, revenue=100.0,
               net_commission=None):
    """`net_commission=None` mantém total == líquida (afiliado sem RM vinculada).
    Passe um valor menor para simular afiliado com Fee de gestão da RM."""
    return {
        "purchaseTime": int(purchase_dt.timestamp()),
        "conversionId": f"conv-{order_id}-{item_id}",
        "conversionStatus": "COMPLETED",
        "estimatedTotalCommission": commission,
        "netCommission": commission if net_commission is None else net_commission,
        "utmContent": "",
        "orders": [
            {
                "orderId": order_id,
                "orderStatus": "COMPLETED",
                "items": [
                    {
                        "itemId": item_id,
                        "itemName": "Produto Teste",
                        "itemPrice": revenue,
                        "actualAmount": revenue,
                        "qty": 1,
                        "itemCommission": commission,
                        "shopName": "Loja",
                        "fraudStatus": "NORMAL",
                        "channelType": "SHOPEE_FEED",
                        "attributionType": "ORDERED_IN_SAME_SHOP",
                        "globalCategoryLv1Name": "Casa",
                        "globalCategoryLv2Name": "",
                        "globalCategoryLv3Name": "",
                    }
                ],
            }
        ],
    }


def _fake_repo_with_integration():
    # AppID da Shopee é NUMÉRICO e o cliente valida isso antes de chamar a API —
    # um valor textual aqui faz o teste morrer em ShopeePermanentError, não no
    # comportamento de upsert que ele quer provar.
    fake_integration = MagicMock(is_active=True, app_id="18300000000",
                                 encrypted_password=b"fake")
    repo_mock = MagicMock()
    repo_mock.get_by_user_id.return_value = fake_integration
    return repo_mock


@pytest.mark.asyncio
async def test_sync_commissions_returns_tuple_when_integration_inactive():
    """Regressão do bug real em produção (25-28/07/2026): sync_commissions retorna uma
    TUPLA (total, is_suspected_partial, details) e sync_user desempacota em 3. O caminho
    de saída antecipada (sem integração / integração inativa) devolvia `0` puro, o que
    estourava 'cannot unpack non-iterable int object' e derrubava o sync do usuário —
    inclusive o sync manual, que nunca completava."""
    from app.services import shopee_integration_service as svc_mod

    repo_mock = MagicMock()
    repo_mock.get_by_user_id.return_value = None  # sem integração

    service = svc_mod.ShopeeIntegrationService(repo_mock)
    result = await service.sync_commissions(user_id=1, db=MagicMock(), days_back=7)

    assert result == (0, False, {})
    total, is_partial, details = result  # não pode estourar
    assert total == 0

    # idem quando existe integração mas está inativa
    repo_mock.get_by_user_id.return_value = MagicMock(is_active=False)
    total, is_partial, details = await service.sync_commissions(
        user_id=1, db=MagicMock(), days_back=7
    )
    assert (total, is_partial, details) == (0, False, {})


@pytest.mark.asyncio
async def test_sync_user_survives_inactive_integration_end_to_end():
    """Fecha o ciclo do bug acima: sync_user (quem desempacota) não pode estourar."""
    from app.services import shopee_integration_service as svc_mod

    lock_conn = MagicMock()
    lock_conn.execute.return_value.scalar.return_value = True
    bind = MagicMock()
    bind.connect.return_value.execution_options.return_value = lock_conn
    db = MagicMock()
    db.get_bind.return_value = bind

    repo_mock = MagicMock()
    repo_mock.get_by_user_id.return_value = None

    fake_run_repo = MagicMock()
    fake_run_repo.create.return_value = 7

    with patch("app.repositories.sync_run_repository.SyncRunRepository", return_value=fake_run_repo):
        service = svc_mod.ShopeeIntegrationService(repo_mock)
        result = await service.sync_user(user_id=1, db=db, days_back=7, trigger="manual")

    assert result == 0
    fake_run_repo.mark_success.assert_called_once()
    fake_run_repo.mark_failed.assert_not_called()


@pytest.mark.asyncio
async def test_sync_commissions_never_deletes_the_window():
    """O único delete que pode sobrar é o de dedup contra OUTROS datasets (seletivo por
    order_id) — se o antigo delete-da-janela-inteira (por data) ainda existisse, o
    contador de deletes seria 2, não 1."""
    from app.repositories.dataset_row_repository import DatasetRowRepository
    from app.services import shopee_integration_service as svc_mod

    now_brt = datetime.now(BRT)
    node = _fake_node(now_brt - timedelta(hours=2))
    fake_dataset = MagicMock(id=555)
    db = MagicMock()

    with patch.object(
        svc_mod, "_get_or_create_shopee_dataset", return_value=fake_dataset
    ), patch(
        "app.services.shopee_graphql_client.execute_graphql",
        new_callable=AsyncMock,
        return_value=_fake_response([node]),
    ), patch.object(
        svc_mod, "decrypt_value", return_value="fake-password"
    ), patch.object(
        DatasetRowRepository, "count_by_date", return_value={}
    ), patch.object(
        DatasetRowRepository, "bulk_create"
    ) as bulk_create_mock:
        service = svc_mod.ShopeeIntegrationService(_fake_repo_with_integration())
        total, is_suspected_partial, details = await service.sync_commissions(
            user_id=1, db=db, days_back=1,
        )

    assert total == 1
    assert is_suspected_partial is False
    assert details == {}
    assert db.query.return_value.filter.return_value.delete.call_count == 1
    bulk_create_mock.assert_called_once()
    (rows_arg,), kwargs = bulk_create_mock.call_args
    assert len(rows_arg) == 1
    assert rows_arg[0].order_id == "order1"
    assert kwargs.get("commit") is False


@pytest.mark.asyncio
async def test_sync_commissions_flags_suspected_partial_without_blocking_write():
    """Fetch atual trouxe bem menos que o que já existia pra uma data 'assentada'
    (3+ dias) -> guarda sinaliza is_suspected_partial, mas a escrita (bulk_create)
    acontece do mesmo jeito — nunca bloqueia."""
    from app.repositories.dataset_row_repository import DatasetRowRepository
    from app.services import shopee_integration_service as svc_mod

    now_brt = datetime.now(BRT)
    old_date = (now_brt - timedelta(days=4)).date()
    node = _fake_node(now_brt - timedelta(days=4))
    fake_dataset = MagicMock(id=555)
    db = MagicMock()

    with patch.object(
        svc_mod, "_get_or_create_shopee_dataset", return_value=fake_dataset
    ), patch(
        "app.services.shopee_graphql_client.execute_graphql",
        new_callable=AsyncMock,
        return_value=_fake_response([node]),
    ), patch.object(
        svc_mod, "decrypt_value", return_value="fake-password"
    ), patch.object(
        DatasetRowRepository, "count_by_date", return_value={old_date: 20}
    ), patch.object(
        DatasetRowRepository, "bulk_create"
    ) as bulk_create_mock:
        service = svc_mod.ShopeeIntegrationService(_fake_repo_with_integration())
        total, is_suspected_partial, details = await service.sync_commissions(
            user_id=1, db=db, days_back=7,
        )

    assert total == 1
    assert is_suspected_partial is True
    suspicious = details["suspected_partial_dates"][str(old_date)]
    assert suspicious == {"previous": 20, "fetched_now": 1}
    bulk_create_mock.assert_called_once()  # guarda nunca bloqueia a escrita


@pytest.mark.asyncio
async def test_guard_ignores_first_day_of_window():
    """O primeiro dia da janela é parcial por construção (a busca começa em
    now - days_back, no meio do dia), então não pode acusar fetch parcial —
    senão TODO sync manual marcaria alerta (visto em produção 28/07: sync de 7
    dias acusou 21/07 com 683 -> 318, sendo que 21/07 é a borda da janela)."""
    from app.repositories.dataset_row_repository import DatasetRowRepository
    from app.services import shopee_integration_service as svc_mod

    now_brt = datetime.now(BRT)
    boundary_day = (now_brt - timedelta(days=7)).date()
    node = _fake_node(now_brt - timedelta(days=7) + timedelta(hours=1))
    fake_dataset = MagicMock(id=555)

    with patch.object(
        svc_mod, "_get_or_create_shopee_dataset", return_value=fake_dataset
    ), patch(
        "app.services.shopee_graphql_client.execute_graphql",
        new_callable=AsyncMock,
        return_value=_fake_response([node]),
    ), patch.object(
        svc_mod, "decrypt_value", return_value="fake-password"
    ), patch.object(
        DatasetRowRepository, "count_by_date", return_value={boundary_day: 683}
    ), patch.object(
        DatasetRowRepository, "bulk_create"
    ):
        service = svc_mod.ShopeeIntegrationService(_fake_repo_with_integration())
        _, is_suspected_partial, details = await service.sync_commissions(
            user_id=1, db=MagicMock(), days_back=7,
        )

    assert is_suspected_partial is False
    assert details == {}


@pytest.mark.asyncio
async def test_sync_user_marks_lock_collision_instead_of_running():
    """Advisory lock já ocupado (outro sync rodando pro mesmo usuário) -> sync_runs
    recebe status skipped_lock (não uma execução 'zero comissões' de verdade), e
    sync_commissions nunca chega a ser chamado."""
    from app.services import shopee_integration_service as svc_mod

    lock_conn = MagicMock()
    lock_conn.execute.return_value.scalar.return_value = False  # lock NÃO adquirido

    bind = MagicMock()
    bind.connect.return_value.execution_options.return_value = lock_conn

    db = MagicMock()
    db.get_bind.return_value = bind

    fake_run_repo = MagicMock()
    fake_run_repo.create.return_value = 42

    with patch(
        "app.repositories.sync_run_repository.SyncRunRepository", return_value=fake_run_repo
    ), patch.object(
        svc_mod.ShopeeIntegrationService, "sync_commissions", new_callable=AsyncMock
    ) as sync_commissions_mock:
        service = svc_mod.ShopeeIntegrationService(repo=MagicMock())
        result = await service.sync_user(user_id=1, db=db, days_back=7, trigger="manual")

    assert result == 0
    fake_run_repo.mark_skipped_lock.assert_called_once_with(42)
    sync_commissions_mock.assert_not_called()


@pytest.mark.asyncio
async def test_grava_comissao_liquida_do_afiliado_e_nao_a_total():
    """Regressão do bug real (26/08/2026, aluno com RM vinculada): o sync gravava
    `estimatedTotalCommission` ("Comissão total do pedido") no lugar de
    `netCommission` ("Comissão líquida do afiliado"), inflando a comissão na tela
    pelo Fee de gestão da RM — 902,17 em vez de 830,00 num único dia.

    Os dois campos precisam ter valores DIFERENTES aqui: com total == líquida (o
    caso de quem não tem rede/hub) o teste passaria com o campo errado, que foi
    exatamente o que deixou o bug passar despercebido.
    """
    from app.repositories.dataset_row_repository import DatasetRowRepository
    from app.services import shopee_integration_service as svc_mod

    now_brt = datetime.now(BRT)
    # 10,00 de comissão total; 9,20 líquida (Fee de gestão da RM de 8%).
    node = _fake_node(now_brt - timedelta(hours=2), commission=10.0, net_commission=9.20)
    fake_dataset = MagicMock(id=555)

    with patch.object(
        svc_mod, "_get_or_create_shopee_dataset", return_value=fake_dataset
    ), patch(
        "app.services.shopee_graphql_client.execute_graphql",
        new_callable=AsyncMock,
        return_value=_fake_response([node]),
    ), patch.object(
        svc_mod, "decrypt_value", return_value="fake-password"
    ), patch.object(
        DatasetRowRepository, "count_by_date", return_value={}
    ), patch.object(
        DatasetRowRepository, "bulk_create"
    ) as bulk_create_mock:
        service = svc_mod.ShopeeIntegrationService(_fake_repo_with_integration())
        await service.sync_commissions(user_id=1, db=MagicMock(), days_back=1)

    (rows_arg,), _ = bulk_create_mock.call_args
    assert len(rows_arg) == 1
    assert float(rows_arg[0].commission) == pytest.approx(9.20), (
        "gravou a comissão TOTAL do pedido em vez da líquida do afiliado"
    )
