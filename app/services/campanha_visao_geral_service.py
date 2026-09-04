"""
Visão geral da campanha (spec §1.3) — o painel de leitura que abre a campanha.

Só métrica OPERACIONAL: cliques, entradas, saídas, permanência e estado dos
grupos. Comissão, lucro e ROAS pertencem a Resultados e não entram aqui — a
pergunta desta tela é "o funil está andando?", não "deu lucro?".

Duas regras que o resto do módulo já carrega e que valem igual aqui:

* **Dia civil é BRT.** O bucketing por dia acontece em Python, com `_brt_date`
  — `cast(timestamptz, Date)` trunca no fuso da SESSÃO do Postgres e espalha
  uma janela de 7 dias por 8 datas.
* **`None` ≠ `0`.** Taxa de entrada sem clique e evasão sem entrada não
  existem; devolver 0,0 afirmaria "ninguém converteu", que é outra coisa.
"""
import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.campanha_grupos import Campanha, CampanhaGrupo
from app.models.campanha_link import (
    EVENTO_ENTRADA, ORIGEM_LINK, CampanhaLink, CampanhaLinkEvento, GrupoEvento,
)
from app.models.whatsapp_grupos import WhatsappGrupo
from app.repositories.campanha_link_repository import teto_efetivo
from app.services.admin_metrics_service import BRT, _brt_date
from app.services.campanha_grupos_service import CampanhaGruposService
from app.services.campanha_resultado_service import _intervalo_brt

logger = logging.getLogger(__name__)

# Períodos do seletor do gráfico (spec §1.3c).
DIAS_VALIDOS = (7, 14, 30)


def _taxa(numerador: int, denominador: int) -> Optional[float]:
    """Percentual com uma casa, ou None quando o denominador não existe."""
    if not denominador:
        return None
    return round(100.0 * numerador / denominador, 1)


class CampanhaVisaoGeralService:
    def __init__(self, db: Session):
        self.db = db

    def resumo(self, campanha, dias: int = 7) -> Dict:
        dias = dias if dias in DIAS_VALIDOS else DIAS_VALIDOS[0]
        servico = CampanhaGruposService(self.db)
        pares = servico.grupos_da_campanha(campanha)
        grupo_ids = [g.id for _v, g in pares]

        # Fecha no último dia FECHADO em Brasília, igual a `presetRangeKeys` no
        # frontend. Incluir o dia corrente poria um ponto pela metade na ponta
        # do gráfico — que a afiliada lê como queda, não como dia em curso.
        fim = _brt_date(datetime.now(BRT)) - timedelta(days=1)
        inicio = fim - timedelta(days=dias - 1)
        ini_utc, fim_utc = _intervalo_brt(inicio, fim)

        cliques = self._cliques(campanha.id, ini_utc, fim_utc)
        entradas, saidas, serie = self._eventos(grupo_ids, ini_utc, fim_utc, inicio, fim)
        entradas_do_link = self._entradas_do_link(grupo_ids, ini_utc, fim_utc)

        return {
            "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat(), "dias": dias},
            "cliques": cliques,
            "entradas": entradas,
            # Taxa de entrada casa ENTRADA VINDA DO LINK com CLIQUE — misturar a
            # entrada orgânica aqui dá taxa acima de 100% em campanha divulgada
            # também fora do link, e a afiliada não confia mais no número.
            "entradas_do_link": entradas_do_link,
            "taxa_entrada": _taxa(entradas_do_link, cliques),
            "saidas": saidas,
            "evasao": _taxa(saidas, entradas),
            "participantes": self._participantes(grupo_ids),
            "grupos": self._estado_dos_grupos(campanha.id),
            "serie": serie,
        }

    # --- pedaços -------------------------------------------------------------

    def _cliques(self, campanha_id: int, ini_utc, fim_utc) -> int:
        """Cliques no link, sem os de teste (`/g/preview/` nunca entra em métrica)."""
        return int(
            self.db.query(func.count(CampanhaLinkEvento.id))
            .join(CampanhaLink, CampanhaLink.id == CampanhaLinkEvento.link_id)
            .filter(CampanhaLink.campanha_id == campanha_id,
                    CampanhaLinkEvento.is_teste.is_(False),
                    CampanhaLinkEvento.criado_em >= ini_utc,
                    CampanhaLinkEvento.criado_em < fim_utc)
            .scalar() or 0
        )

    def _entradas_do_link(self, grupo_ids: List[int], ini_utc, fim_utc) -> int:
        if not grupo_ids:
            return 0
        return int(
            self.db.query(func.count(GrupoEvento.id))
            .filter(GrupoEvento.grupo_id.in_(grupo_ids),
                    GrupoEvento.tipo == EVENTO_ENTRADA,
                    GrupoEvento.origem == ORIGEM_LINK,
                    GrupoEvento.criado_em >= ini_utc,
                    GrupoEvento.criado_em < fim_utc)
            .scalar() or 0
        )

    def _eventos(self, grupo_ids: List[int], ini_utc, fim_utc,
                 inicio: date, fim: date):
        """Totais + a série diária de entradas × saídas, já em dia civil BRT."""
        serie = {
            (inicio + timedelta(days=i)).isoformat(): {"entradas": 0, "saidas": 0}
            for i in range((fim - inicio).days + 1)
        }
        if not grupo_ids:
            return 0, 0, self._serie_ordenada(serie)

        # Traz os timestamps e agrupa em Python: `cast(col, Date)` truncaria no
        # fuso da sessão do Postgres, não em BRT.
        linhas = (
            self.db.query(GrupoEvento.tipo, GrupoEvento.criado_em)
            .filter(GrupoEvento.grupo_id.in_(grupo_ids),
                    GrupoEvento.criado_em >= ini_utc,
                    GrupoEvento.criado_em < fim_utc)
            .all()
        )
        entradas = saidas = 0
        for tipo, quando in linhas:
            chave = "entradas" if tipo == EVENTO_ENTRADA else "saidas"
            if chave == "entradas":
                entradas += 1
            else:
                saidas += 1
            dia = _brt_date(quando)
            if dia is None:
                continue
            balde = serie.get(dia.isoformat())
            if balde is not None:
                balde[chave] += 1
        return entradas, saidas, self._serie_ordenada(serie)

    @staticmethod
    def _serie_ordenada(serie: Dict[str, Dict[str, int]]) -> List[Dict]:
        return [
            {"data": dia, "entradas": v["entradas"], "saidas": v["saidas"]}
            for dia, v in sorted(serie.items())
        ]

    def _participantes(self, grupo_ids: List[int]) -> int:
        """
        Soma do contador vivo de cada grupo.

        O snapshot NÃO é usado aqui, e isso é deliberado: `grupo_snapshots` é
        uma cópia congelada do mesmo campo (`whatsapp_grupos.participantes`),
        gravada 1×/dia pelo cron. Preferi-lo faria esta tela mostrar o número
        de ontem enquanto a aba Grupos e Resultados — que leem o contador vivo,
        atualizado a cada entrada pelo webhook — mostram o de hoje. Duas telas
        da MESMA campanha divergindo é o tipo de número que vira chamado.

        O snapshot continua existindo para a série histórica; para "quantos
        estão no grupo agora", o contador vivo é a resposta.
        """
        if not grupo_ids:
            return 0
        return int(
            self.db.query(func.coalesce(func.sum(WhatsappGrupo.participantes), 0))
            .filter(WhatsappGrupo.id.in_(grupo_ids))
            .scalar() or 0
        )

    def _estado_dos_grupos(self, campanha_id: int) -> Dict[str, int]:
        """
        Total · abertos · cheios · disponíveis.

        "Disponível" tem que significar **exatamente** o que o roteador aceita
        (`escolher_grupo`, em campanha_link_repository): aberto, com vaga, o
        grupo `ativo` e com `link_convite`. Contar só `aberto` + vaga fazia a
        tela dizer "Disponíveis: 1" para um grupo que sumiu do WhatsApp ou
        ficou sem convite — e quem clicasse no link veria "vagas esgotadas"
        com o painel afirmando que estava tudo certo.

        "Cheio" usa `teto_efetivo()`, a mesma expressão da rotação.
        """
        linhas = (
            self.db.query(CampanhaGrupo.aberto,
                          WhatsappGrupo.participantes >= teto_efetivo(),
                          WhatsappGrupo.ativo,
                          WhatsappGrupo.link_convite.isnot(None))
            .join(WhatsappGrupo, WhatsappGrupo.id == CampanhaGrupo.grupo_id)
            .join(Campanha, Campanha.id == CampanhaGrupo.campanha_id)
            .filter(CampanhaGrupo.campanha_id == campanha_id)
            .all()
        )
        total = len(linhas)
        abertos = sum(1 for aberto, _c, _a, _l in linhas if aberto)
        cheios = sum(1 for _ab, cheio, _a, _l in linhas if cheio)
        disponiveis = sum(
            1 for aberto, cheio, ativo, tem_convite in linhas
            if aberto and not cheio and ativo and tem_convite
        )
        return {"total": total, "abertos": abertos, "cheios": cheios,
                "disponiveis": disponiveis}
