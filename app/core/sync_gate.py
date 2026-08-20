"""Quem pode disparar sincronização em homologação.

Homologação bate na API real da Shopee e da Meta com as mesmas credenciais e
o mesmo rate limit de produção. Com várias contas de teste sincronizando —
pelo botão ou pelo cron diário, que varre todo mundo — o ruído atrapalha a
validação e consome cota à toa.

Por isso, **em homologação**, sincronização é privilégio de uma lista curta.
Em produção e em dev local a função devolve True para todo mundo: o gate não
muda nada fora de homologação.
"""

from app.core.ambiente import is_homologacao

# Luiz Fernando de Oliveira — quem valida as rodadas em homologação.
EMAILS_LIBERADOS_EM_HOMOLOGACAO = frozenset(
    {
        "lfernandooliveira@outlook.com",
    }
)

MOTIVO_BLOQUEIO = (
    "Sincronização em homologação está liberada apenas para a conta do "
    "Luiz Fernando."
)


def sync_liberado_para(email: str | None) -> bool:
    """True se esta conta pode sincronizar no ambiente atual."""
    if not is_homologacao():
        return True
    return (email or "").strip().lower() in EMAILS_LIBERADOS_EM_HOMOLOGACAO
