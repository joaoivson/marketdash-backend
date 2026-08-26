"""
`ShopeeIntegrationService` recebe o REPOSITORY, não a Session.

Passar a Session dava `AttributeError: 'Session' object has no attribute
'get_by_user_id'` **dentro de um `except Exception`**: no motor de envio a linha
virava `pulado` com erro "short_link", e no monitoramento a captura virava
`erro`. Ou seja, TODO passo de oferta e TODA replicação falhavam em produção,
em silêncio — e nenhum teste pegava, porque os testes do motor injetam
`short_link_factory` e não chegam a construir o service.

Este teste constrói o service pelo mesmo caminho do código de produção.
"""
import inspect

import pytest

from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository
from app.services.shopee_integration_service import ShopeeIntegrationService


def test_construtor_espera_repository_e_nao_session():
    assinatura = inspect.signature(ShopeeIntegrationService.__init__)
    anotacao = assinatura.parameters["repo"].annotation
    assert anotacao in (ShopeeIntegrationRepository, "ShopeeIntegrationRepository")


@pytest.mark.parametrize("modulo,trecho", [
    ("app/services/roteiro_envio_service.py", "ShopeeIntegrationRepository(self.db)"),
    ("app/tasks/monitoramento_tasks.py", "ShopeeIntegrationRepository(db)"),
])
def test_call_sites_constroem_com_repository(modulo, trecho):
    fonte = open(modulo, encoding="utf-8").read()
    assert "ShopeeIntegrationService(" in fonte
    assert trecho in fonte, f"{modulo} constrói o service sem o repository"
    assert "ShopeeIntegrationService(self.db)" not in fonte
    assert "ShopeeIntegrationService(db)" not in fonte
