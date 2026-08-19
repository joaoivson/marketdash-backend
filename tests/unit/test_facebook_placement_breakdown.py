"""Normalização do `publisher_platform` e rateio de comissão por plataforma.

A Meta lista os valores de placement na doc de Placement Targeting, mas NÃO publica
o enum de retorno na referência de Ads Insights. Se um valor novo aparecer (como
`threads` apareceu), descartar a linha faria o gasto por plataforma parar de fechar
com o gasto total da campanha — por isso o desconhecido é mantido, não jogado fora.
"""

from app.services.facebook_marketing_client import (
    UNKNOWN_PUBLISHER_PLATFORM,
    normalize_publisher_platform,
)


class TestNormalizePublisherPlatform:
    def test_valores_conhecidos_passam_em_minusculo(self):
        assert normalize_publisher_platform("instagram") == "instagram"
        assert normalize_publisher_platform("FACEBOOK") == "facebook"
        assert normalize_publisher_platform(" Audience_Network ") == "audience_network"
        assert normalize_publisher_platform("threads") == "threads"
        assert normalize_publisher_platform("messenger") == "messenger"

    def test_valor_novo_da_meta_vira_desconhecido_e_nao_e_descartado(self):
        # Placement que ainda não existe hoje. O gasto tem que continuar somando.
        assert normalize_publisher_platform("plataforma_nova_2027") == UNKNOWN_PUBLISHER_PLATFORM

    def test_vazio_e_none_viram_desconhecido(self):
        assert normalize_publisher_platform(None) == UNKNOWN_PUBLISHER_PLATFORM
        assert normalize_publisher_platform("") == UNKNOWN_PUBLISHER_PLATFORM
        assert normalize_publisher_platform("   ") == UNKNOWN_PUBLISHER_PLATFORM
