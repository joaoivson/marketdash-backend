"""Status cancelado — pedido não conta na contagem, mas a comissão da linha
continua entrando na soma (a venda existiu). Fonte de verdade original:
app/services/kpi_service.py.
"""

STATUS_CANCELADO = frozenset({"cancelado", "cancelled"})
