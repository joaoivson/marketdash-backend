"""Status de pedido (DatasetRow.status) — fonte única, usada por todo cálculo
de KPI/comissão do backend (kpi_service, campaign_repository) e espelhada no
frontend (`src/shared/lib/kpi.ts::KPI_STATUSES`). Qualquer status novo que a
Shopee mandar (ex.: "UNPAID", que escapa da normalização PT do import — ver
`shared/lib/utils.ts`) precisa entrar aqui OU ser filtrado de propósito nos
dois lados, senão a mesma comissão soma valores diferentes em telas
diferentes (Dashboard vs Campanhas).

STATUS_CANCELADO: pedido cancelado não conta na contagem de pedidos, mas a
comissão da linha continua entrando na soma (a venda existiu).

STATUS_DO_KPI: allowlist de status que entram nos totais de KPI/comissão —
"UNPAID" (pedido sem pagamento confirmado pelo comprador) fica de fora de
propósito: a comissão não é real enquanto o pagamento não é confirmado.
"""

STATUS_CANCELADO = frozenset({"cancelado", "cancelled"})

STATUS_DO_KPI = frozenset({
    "pendente", "concluído", "concluido", "cancelado",
    "pending", "completed", "cancelled",
})
