"""
Resultados por grupo (F7) — onde a corrente vira número.

Uma linha por grupo, juntando as quatro fontes que as fases anteriores
construíram:

  participantes  grupo_snapshots (retrato diário)        F6
  entradas/saídas/ficaram  grupo_eventos                  F6
  mensagens      roteiro_mensagens (status enviado)       F3
  cliques        custom_link_events do link do grupo      F1
  pedidos/comissão  dataset_rows_v2 com sub_id1 = wg…     F1 + sync

**Comissão líquida usa a fórmula do KpiService** — nunca as colunas `cost` e
`profit` de dataset_rows_v2, que estão mortas. Se este número divergir do
Dashboard, a afiliada perde a confiança nos dois.

**Lucro por pessoa** é a métrica de destaque (spec §11): é a única que
responde "vale pagar R$14 para colocar mais uma pessoa aqui dentro?".
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.campanha_link import CampanhaLink
from app.models.custom_link_event import CustomLinkEvent
from app.models.dataset_row import DatasetRow
from app.models.roteiro import MSG_ENVIADA, RoteiroMensagem
from app.repositories.campanha_link_repository import CampanhaLinkRepository
from app.services.janela_envio_service import BRT
from app.utils.order_status import STATUS_CANCELADO, STATUS_DO_KPI
from app.services.kpi_service import KpiService, normalizar_sub_id

logger = logging.getLogger(__name__)


def _duas_casas(v: float) -> float:
    return round(float(v or 0.0) + 0.0, 2)


def _intervalo_brt(inicio: date, fim: date):
    """
    (inicio, fim_exclusivo) em UTC do intervalo de dias civis BRT.

    `func.date(coluna_timestamptz)` trunca no fuso da SESSÃO do Postgres, não
    em BRT — uma venda das 22h de Brasília cai no dia seguinte e o total do
    período muda conforme a hora em que a tela é aberta.
    """
    ini = datetime.combine(inicio, time.min, tzinfo=BRT).astimezone(timezone.utc)
    fim_ex = datetime.combine(fim + timedelta(days=1), time.min,
                              tzinfo=BRT).astimezone(timezone.utc)
    return ini, fim_ex


class CampanhaResultadoService:
    def __init__(self, db: Session):
        self.db = db
        self.repo_link = CampanhaLinkRepository(db)

    def por_grupo(self, user_id: int, campanha, inicio: date, fim: date) -> Dict:
        from app.services.campanha_grupos_service import CampanhaGruposService

        pares = CampanhaGruposService(self.db).grupos_da_campanha(campanha)
        grupos = [g for _v, g in pares]
        if not grupos:
            return {"linhas": [], "totais": self._totais_vazios()}

        grupo_ids = [g.id for g in grupos]
        sub_ids = {g.sub_id for g in grupos if g.sub_id}

        ini_utc, fim_utc = _intervalo_brt(inicio, fim)
        eventos = self.repo_link.eventos_por_grupo(grupo_ids, ini_utc, fim_utc)
        mensagens = self._mensagens_por_grupo(user_id, grupo_ids, inicio, fim)
        cliques = self._cliques_por_grupo(grupos, inicio, fim)
        comissao = self._comissao_por_sub_id(user_id, sub_ids, inicio, fim)
        gasto_por_grupo = self._gasto_atribuido(user_id, campanha, grupo_ids, eventos,
                                                inicio, fim)

        linhas = []
        for g in grupos:
            ev = eventos.get(g.id, {})
            dados_comissao = comissao.get(normalizar_sub_id(g.sub_id), {})
            liquida = dados_comissao.get("comissao_liquida", 0.0)
            gasto = gasto_por_grupo.get(g.id, 0.0)
            participantes = g.participantes or 0
            lucro = _duas_casas(liquida - gasto)
            linhas.append({
                "grupo_id": g.id,
                "grupo": g.nome,
                "sub_id": g.sub_id,
                "participantes": participantes,
                "entradas": ev.get("entradas", 0),
                "saidas": ev.get("saidas", 0),
                "ficaram": ev.get("ficaram", 0),
                "evasao_pct": _duas_casas(
                    (ev.get("saidas", 0) / ev["entradas"] * 100) if ev.get("entradas") else 0.0
                ),
                "mensagens": mensagens.get(g.id, 0),
                "cliques": cliques.get(g.id, 0),
                "pedidos": dados_comissao.get("pedidos", 0),
                "comissao_liquida": _duas_casas(liquida),
                "gasto_atribuido": _duas_casas(gasto),
                "lucro": lucro,
                # A métrica que decide o investimento. Sem participante ela NÃO
                # existe — e 0,00 seria uma afirmação diferente ("cada pessoa
                # rende zero"), que é o mesmo colapso null-vs-zero que o resto
                # do módulo evita em `leads` e `cpl`.
                "lucro_por_pessoa": (_duas_casas(lucro / participantes)
                                     if participantes else None),
            })

        linhas.sort(key=lambda l: l["lucro"], reverse=True)
        return {"linhas": linhas, "totais": self._totais(linhas)}

    # --- fontes -------------------------------------------------------------

    def _mensagens_por_grupo(self, user_id: int, grupo_ids: List[int],
                             inicio: date, fim: date) -> Dict[int, int]:
        ini_utc, fim_utc = _intervalo_brt(inicio, fim)
        linhas = (
            self.db.query(RoteiroMensagem.grupo_id, func.count(RoteiroMensagem.id))
            .filter(RoteiroMensagem.user_id == user_id,
                    RoteiroMensagem.grupo_id.in_(grupo_ids),
                    RoteiroMensagem.status == MSG_ENVIADA,
                    RoteiroMensagem.enviado_em >= ini_utc,
                    RoteiroMensagem.enviado_em < fim_utc)
            .group_by(RoteiroMensagem.grupo_id)
            .all()
        )
        return {gid: int(n) for gid, n in linhas}

    def _cliques_por_grupo(self, grupos, inicio: date, fim: date) -> Dict[int, int]:
        """Cliques na OFERTA (custom_link do grupo) — não confundir com os
        cliques no link de ENTRADA, que medem outra coisa."""
        por_link = {g.custom_link_id: g.id for g in grupos if g.custom_link_id}
        if not por_link:
            return {}
        ini_utc, fim_utc = _intervalo_brt(inicio, fim)
        linhas = (
            self.db.query(CustomLinkEvent.custom_link_id, func.count(CustomLinkEvent.id))
            .filter(CustomLinkEvent.custom_link_id.in_(list(por_link)),
                    CustomLinkEvent.created_at >= ini_utc,
                    CustomLinkEvent.created_at < fim_utc)
            .group_by(CustomLinkEvent.custom_link_id)
            .all()
        )
        return {por_link[lid]: int(n) for lid, n in linhas if lid in por_link}

    def _comissao_por_sub_id(self, user_id: int, sub_ids: set,
                             inicio: date, fim: date) -> Dict[str, Dict]:
        """Pedidos e comissão líquida por SubID de grupo, com a fórmula do
        KpiService (comissão bruta × (1 − imposto sobre comissão))."""
        if not sub_ids:
            return {}
        _ad_rate, comm_rate = KpiService(self.db).taxas(user_id)
        alvos = {normalizar_sub_id(s) for s in sub_ids}

        # O MESMO recorte do KpiService: allowlist de status (UNPAID fica de
        # fora — comissão não confirmada não é comissão) e filtro do sub_id no
        # SQL, não em Python. Sem a allowlist, esta tela e o Dashboard mostram
        # números diferentes para a mesma venda, e é esta que decide o gasto.
        linhas = (
            self.db.query(DatasetRow.sub_id1, DatasetRow.order_id,
                          DatasetRow.commission, DatasetRow.status)
            .filter(DatasetRow.user_id == user_id,
                    DatasetRow.date >= inicio, DatasetRow.date <= fim,
                    DatasetRow.sub_id1.isnot(None),
                    # rtrim('-') + lower = o normalizar_sub_id em SQL. Filtrar só
                    # por lower() descartaria "wg1-" (que normaliza para "wg1")
                    # em silêncio — a venda sumiria da tela sem erro nenhum.
                    func.rtrim(func.lower(func.trim(
                        func.coalesce(DatasetRow.sub_id1, ""))), "-").in_(alvos),
                    func.lower(func.coalesce(DatasetRow.status, "")).in_(STATUS_DO_KPI))
            .all()
        )
        resultado: Dict[str, Dict] = {}
        pedidos_por_sub: Dict[str, set] = {}
        for sub_id1, order_id, comissao, status in linhas:
            chave = normalizar_sub_id(sub_id1)
            if chave not in alvos:
                continue
            bucket = resultado.setdefault(chave, {"comissao_bruta": 0.0, "pedidos": 0})
            bucket["comissao_bruta"] += float(comissao or 0.0)
            # Pedido cancelado não conta como pedido, mas a comissão da linha
            # continua somando — mesma regra do KpiService.
            if order_id and str(status or "").lower() not in STATUS_CANCELADO:
                pedidos_por_sub.setdefault(chave, set()).add(order_id)

        for chave, bucket in resultado.items():
            bucket["pedidos"] = len(pedidos_por_sub.get(chave, ()))
            bucket["comissao_liquida"] = bucket["comissao_bruta"] * (1 - comm_rate)
        return resultado

    def _gasto_atribuido(self, user_id: int, campanha, grupo_ids: List[int],
                         eventos: Dict, inicio: date, fim: date) -> Dict[int, float]:
        """
        Gasto dos anúncios vinculados, rateado por ENTRADAS do período.

        O rateio é explícito e a tela diz de onde vem: sem isso, o "lucro por
        grupo" seria comissão pura e a afiliada tomaria decisão de
        investimento ignorando o que pagou para encher o grupo.
        """
        from app.repositories.campanha_anuncio_repository import CampanhaAnuncioRepository

        gasto_total = CampanhaAnuncioRepository(self.db).gasto_com_imposto(
            user_id, campanha.id, inicio, fim
        )
        if not gasto_total:
            return {}
        entradas = {gid: (eventos.get(gid, {}).get("entradas", 0)) for gid in grupo_ids}
        soma = sum(entradas.values())
        if not soma:
            # Sem entrada no período, ratear igualmente é menos errado do que
            # jogar tudo no primeiro grupo.
            return {gid: gasto_total / len(grupo_ids) for gid in grupo_ids}
        return {gid: gasto_total * (n / soma) for gid, n in entradas.items()}

    # --- totais -------------------------------------------------------------

    def _totais_vazios(self) -> Dict:
        return {"participantes": 0, "entradas": 0, "saidas": 0, "ficaram": 0,
                "mensagens": 0, "cliques": 0, "pedidos": 0,
                "comissao_liquida": 0.0, "gasto_atribuido": 0.0, "lucro": 0.0,
                "lucro_por_pessoa": None}

    def _totais(self, linhas: List[Dict]) -> Dict:
        t = self._totais_vazios()
        for l in linhas:
            for chave in ("participantes", "entradas", "saidas", "ficaram",
                          "mensagens", "cliques", "pedidos"):
                t[chave] += l[chave]
            for chave in ("comissao_liquida", "gasto_atribuido", "lucro"):
                t[chave] = _duas_casas(t[chave] + l[chave])
        t["lucro_por_pessoa"] = (
            _duas_casas(t["lucro"] / t["participantes"]) if t["participantes"] else None
        )
        return t
