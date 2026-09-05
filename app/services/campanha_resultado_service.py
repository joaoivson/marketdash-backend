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
from app.repositories.campanha_anuncio_repository import CampanhaAnuncioRepository
from app.repositories.campanha_link_repository import CampanhaLinkRepository
from app.repositories.campanha_sub_id_repository import CampanhaSubIdRepository
from app.services.janela_envio_service import BRT
from app.utils.order_status import STATUS_CANCELADO, STATUS_DO_KPI
from app.services.kpi_service import KpiService, normalizar_sub_id

logger = logging.getLogger(__name__)


def _duas_casas(v: float) -> float:
    return round(float(v or 0.0) + 0.0, 2)


def _evasao(saidas: int, participantes: int) -> Optional[float]:
    """
    Saídas sobre a população EXPOSTA ao risco de sair no período.

    A base é `participantes + saidas`, e a aritmética é o argumento:
    `participantes_hoje = base_inicial + entradas − saidas`, logo
    `participantes + saidas = base_inicial + entradas` — todo mundo que esteve
    dentro do grupo em algum momento da janela. É o único denominador que
    garante `saidas <= base`, ou seja, evasão nunca acima de 100%.

    A fórmula anterior dividia pelas ENTRADAS do período e explodia no caso
    mais comum de todos: grupo cheio, que quase não recebe e continua perdendo
    gente. Um grupo com 1 entrada e 9 saídas dava **900%**.

    `None`, nunca 0,0: sem ninguém exposto a métrica não existe, e 0,0
    afirmaria "ninguém saiu" — o mesmo colapso null-vs-zero que o módulo evita
    em `leads` e `cpl`.
    """
    base = (participantes or 0) + (saidas or 0)
    if not base:
        return None
    return _duas_casas(saidas / base * 100)


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
        sub_ids_de_grupo = {normalizar_sub_id(g.sub_id) for g in grupos if g.sub_id}

        # Sub IDs vinculados À MÃO à campanha (080): comissão que não passa por
        # grupo rastreado. Entram no TOTAL, nunca numa linha de grupo — não há
        # como saber a qual grupo pertencem, e inventar seria o mesmo erro do
        # rateio que acabou de sair daqui.
        manuais = {
            normalizar_sub_id(s)
            for s in CampanhaSubIdRepository(self.db).sub_ids(campanha.id)
        }
        # Dedup obrigatória: o mesmo sub_id vinculado à mão E pertencente a um
        # grupo somaria a comissão DUAS vezes.
        manuais -= sub_ids_de_grupo

        ini_utc, fim_utc = _intervalo_brt(inicio, fim)
        eventos = self.repo_link.eventos_por_grupo(grupo_ids, ini_utc, fim_utc)
        mensagens = self._mensagens_por_grupo(user_id, grupo_ids, inicio, fim)
        cliques = self._cliques_por_grupo(grupos, inicio, fim)
        # UMA consulta para os dois conjuntos: separar em duas faria a mesma
        # varredura de dataset_rows duas vezes.
        comissao = self._comissao_por_sub_id(
            user_id, sub_ids_de_grupo | manuais, inicio, fim
        )

        linhas = []
        for g in grupos:
            ev = eventos.get(g.id, {})
            dados_comissao = comissao.get(normalizar_sub_id(g.sub_id), {})
            liquida = dados_comissao.get("comissao_liquida", 0.0)
            linhas.append({
                "grupo_id": g.id,
                "grupo": g.nome,
                "sub_id": g.sub_id,
                "participantes": g.participantes or 0,
                "entradas": ev.get("entradas", 0),
                "saidas": ev.get("saidas", 0),
                "ficaram": ev.get("ficaram", 0),
                "evasao_pct": _evasao(ev.get("saidas", 0), g.participantes or 0),
                "mensagens": mensagens.get(g.id, 0),
                "cliques": cliques.get(g.id, 0),
                "pedidos": dados_comissao.get("pedidos", 0),
                "comissao_liquida": _duas_casas(liquida),
            })

        # Ordena por COMISSÃO. Ordenava por `lucro`, que era comissão menos um
        # gasto rateado — ou seja, a ordem da tabela dependia de um número
        # inventado.
        linhas.sort(key=lambda l: l["comissao_liquida"], reverse=True)

        comissao_manual = _duas_casas(sum(
            comissao.get(s, {}).get("comissao_liquida", 0.0) for s in manuais
        ))
        pedidos_manuais = sum(
            comissao.get(s, {}).get("pedidos", 0) for s in manuais
        )
        gasto = CampanhaAnuncioRepository(self.db).gasto_com_imposto(
            user_id, campanha.id, inicio, fim
        )
        # Há medição de comissão nesta campanha?
        #
        # NÃO basta o grupo ter `sub_id`: ele nasce na ativação, sempre, e só
        # captura venda se as ofertas do grupo usarem os links do MarketDash.
        # Contar a mera existência dele faria "Lucro −R$1.305,73" continuar
        # aparecendo como prejuízo medido onde ninguém mediu nada.
        #
        # Conta como medição: vínculo manual (ela declarou o que rastrear) ou
        # sub_id de grupo que EFETIVAMENTE trouxe pedido no período.
        com_pedido = {
            s for s in sub_ids_de_grupo
            if comissao.get(s, {}).get("pedidos", 0) > 0
        }
        rastreados = len(manuais) + len(com_pedido)
        return {
            "linhas": linhas,
            "totais": self._totais(linhas, comissao_manual, pedidos_manuais, gasto,
                                   rastreados),
        }

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

    # --- totais -------------------------------------------------------------

    def _totais_vazios(self) -> Dict:
        return {"participantes": 0, "entradas": 0, "saidas": 0, "ficaram": 0,
                "mensagens": 0, "cliques": 0, "pedidos": 0,
                "comissao_liquida": 0.0, "gasto_atribuido": 0.0, "lucro": 0.0,
                "roas": None, "lucro_por_pessoa": None, "evasao_pct": None,
                # 0 = nenhum Sub ID rastreando. Comissão zero aí não é
                # prejuízo, é ausência de medição — e a tela mostra "—".
                "sub_ids_vinculados": 0}

    def _totais(self, linhas: List[Dict], comissao_manual: float = 0.0,
                pedidos_manuais: int = 0, gasto: float = 0.0,
                sub_ids_vinculados: int = 0) -> Dict:
        """
        Totais da CAMPANHA — o único nível em que gasto, lucro e ROAS existem.

        **Não é a soma de um rateio.** O gasto entra INTEIRO, uma vez, como o
        `/resumo` do Dashboard já fazia. A versão anterior somava as parcelas
        rateadas por grupo, e como o rateio dividia igualmente quando ninguém
        entrava no período, o "lucro por pessoa" de destaque saía de uma
        divisão arbitrária — foi o que produziu −R$0,65 e −R$0,92 lado a lado
        para dois grupos de tamanhos completamente diferentes.

        `comissao_manual` são os Sub IDs vinculados à campanha (080), que não
        têm linha de grupo e por isso só existem aqui.
        """
        t = self._totais_vazios()
        t["sub_ids_vinculados"] = sub_ids_vinculados
        for l in linhas:
            for chave in ("participantes", "entradas", "saidas", "ficaram",
                          "mensagens", "cliques", "pedidos"):
                t[chave] += l[chave]
            t["comissao_liquida"] = _duas_casas(
                t["comissao_liquida"] + l["comissao_liquida"]
            )
        t["comissao_liquida"] = _duas_casas(t["comissao_liquida"] + comissao_manual)
        t["pedidos"] += pedidos_manuais
        t["gasto_atribuido"] = _duas_casas(gasto)
        t["lucro"] = _duas_casas(t["comissao_liquida"] - t["gasto_atribuido"])
        # ROAS Real com a mesma fórmula do resto do produto: comissão LÍQUIDA
        # sobre gasto COM imposto. Sem investimento não existe ROAS — `None`,
        # nunca 0,00, que afirmaria "cada real gasto voltou zero".
        t["roas"] = (_duas_casas(t["comissao_liquida"] / t["gasto_atribuido"])
                     if t["gasto_atribuido"] else None)
        t["lucro_por_pessoa"] = (
            _duas_casas(t["lucro"] / t["participantes"]) if t["participantes"] else None
        )
        # Evasão do TOTAL não é a média das evasões: é o conjunto inteiro.
        t["evasao_pct"] = _evasao(t["saidas"], t["participantes"])
        return t

    # --- Sub IDs vinculáveis (080) -------------------------------------------

    def sub_ids_disponiveis(self, user_id: int, inicio: date,
                            fim: date) -> Dict[str, Dict]:
        """
        Todo Sub ID que teve venda no período, com pedidos e comissão líquida.

        É o mesmo recorte de `_comissao_por_sub_id` — allowlist de status e a
        fórmula do KpiService — só que sem alvo: aqui a pergunta é "quais
        existem?", não "quanto trouxe este conjunto".
        """
        _ad_rate, comm_rate = KpiService(self.db).taxas(user_id)
        linhas = (
            self.db.query(DatasetRow.sub_id1, DatasetRow.order_id,
                          DatasetRow.commission, DatasetRow.status)
            .filter(DatasetRow.user_id == user_id,
                    DatasetRow.date >= inicio, DatasetRow.date <= fim,
                    DatasetRow.sub_id1.isnot(None),
                    func.lower(func.coalesce(DatasetRow.status, "")).in_(STATUS_DO_KPI))
            .all()
        )
        saida: Dict[str, Dict] = {}
        pedidos: Dict[str, set] = {}
        for sub_id1, order_id, comissao, status in linhas:
            chave = normalizar_sub_id(sub_id1)
            if not chave:
                continue
            bucket = saida.setdefault(chave, {"comissao_bruta": 0.0, "pedidos": 0})
            bucket["comissao_bruta"] += float(comissao or 0.0)
            if order_id and str(status or "").lower() not in STATUS_CANCELADO:
                pedidos.setdefault(chave, set()).add(order_id)
        for chave, bucket in saida.items():
            bucket["pedidos"] = len(pedidos.get(chave, ()))
            bucket["comissao_liquida"] = _duas_casas(
                bucket["comissao_bruta"] * (1 - comm_rate)
            )
        return saida
