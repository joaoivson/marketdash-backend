"""Acesso ao link de entrada, seus eventos e aos eventos/snapshots de grupo."""
import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, text, tuple_
from sqlalchemy.orm import Session

from app.models.campanha_grupos import Campanha, CampanhaGrupo
from app.models.campanha_link import (
    EVENTO_ENTRADA, EVENTO_SAIDA, CampanhaLink, CampanhaLinkEvento,
    GrupoEvento, GrupoSnapshot,
)
from app.models.whatsapp_grupos import WhatsappGrupo


# Teto efetivo de um grupo: o MENOR entre a capacidade real do WhatsApp e o
# limite que a campanha configurou. A capacidade continua sendo o teto absoluto
# (spec §3.4) — o limite da campanha só aperta, nunca afrouxa.
#
# Existe uma vez só de propósito: a regra de lotação é lida em três lugares
# (escolher, abrir o próximo, fechar os lotados) e o dia em que uma cópia ficar
# para trás o grupo continua recebendo gente depois de "cheio", em silêncio.
TETO_SQL = "LEAST(g.capacidade, COALESCE(c.limite_participantes, g.capacidade))"


def teto_efetivo(grupo=WhatsappGrupo, campanha=Campanha):
    """Mesma regra do `TETO_SQL`, em SQLAlchemy (para os filtros do ORM)."""
    return func.least(
        grupo.capacidade,
        func.coalesce(campanha.limite_participantes, grupo.capacidade),
    )


# "Cheio" com o override da usuária por cima (migration 080). NULL no override
# = vale a ocupação; TRUE/FALSE = a decisão dela vence.
#
# Os dois casos reais que o override resolve: segurar um grupo ANTES de lotar,
# e destravar um grupo cuja contagem o WhatsApp não atualizou. Antes disso,
# "cheio" só existia derivado e o grupo lotado nunca era MARCADO — a linha
# ficava amarela e ele continuava recebendo.
CHEIO_SQL = f"COALESCE(cg.cheio_override, g.participantes >= {TETO_SQL})"


# A CAPACIDADE do WhatsApp, sozinha — sem o limite da campanha por cima.
#
# Existe separada de propósito, e é a ÚNICA diferença entre a rotação normal e
# o fallback de lotação. O limite da campanha (ex.: 900) governa a rotação
# normal; a capacidade (1024) é o limite duro, onde o convite falha do lado do
# WhatsApp. Quando todos os grupos estouraram o limite DELA, o fallback ainda
# manda gente para o primeiro da ordem — porque sempre há alguém saindo, e um
# clique que cai em "vagas esgotadas" é CPC gasto que não vira lead.
CAPACIDADE_SQL = "g.participantes < g.capacidade"


def cheio_efetivo(vinculo=CampanhaGrupo, grupo=WhatsappGrupo, campanha=Campanha):
    """Mesma regra do `CHEIO_SQL`, em SQLAlchemy."""
    return func.coalesce(
        vinculo.cheio_override,
        grupo.participantes >= teto_efetivo(grupo, campanha),
    )


class CampanhaLinkRepository:
    def __init__(self, db: Session):
        self.db = db

    # --- link ---------------------------------------------------------------

    def por_campanha(self, campanha_id: int) -> Optional[CampanhaLink]:
        return (
            self.db.query(CampanhaLink)
            .filter(CampanhaLink.campanha_id == campanha_id)
            .first()
        )

    def por_slug(self, slug: str) -> Optional[CampanhaLink]:
        return self.db.query(CampanhaLink).filter(CampanhaLink.slug == slug).first()

    def campanha_do_link(self, link: CampanhaLink) -> Optional[Campanha]:
        return self.db.query(Campanha).get(link.campanha_id)

    def adicionar(self, obj):
        self.db.add(obj)
        self.db.flush()
        return obj

    # --- roteamento ---------------------------------------------------------

    def escolher_grupo(self, campanha_id: int, aleatorio: bool) -> Optional[Tuple[int, int]]:
        """
        Escolhe UM grupo com vaga e trava a linha do vínculo (SKIP LOCKED):
        dois cliques simultâneos nunca recebem o mesmo "último lugar".

        Devolve (grupo_id, posicao) ou None quando não há vaga.
        """
        ordem = "random()" if aleatorio else "cg.posicao, cg.grupo_id"
        linha = self.db.execute(
            text(f"""
                SELECT cg.grupo_id, cg.posicao
                  FROM campanha_grupos cg
                  JOIN whatsapp_grupos g ON g.id = cg.grupo_id
                  JOIN campanhas c ON c.id = cg.campanha_id
                 WHERE cg.campanha_id = :campanha_id
                   AND cg.aberto
                   AND g.ativo
                   AND g.link_convite IS NOT NULL
                   AND NOT {CHEIO_SQL}
                 ORDER BY {ordem}
                 LIMIT 1
                   FOR UPDATE OF cg SKIP LOCKED
            """),
            {"campanha_id": campanha_id},
        ).fetchone()
        return (linha[0], linha[1]) if linha else None

    def primeiro_por_capacidade(self, campanha_id: int) -> Optional[Tuple[int, int]]:
        """
        Primeiro grupo da ORDEM que ainda cabe alguém pela capacidade do
        WhatsApp — o destino do fallback quando tudo está cheio.

        Três diferenças deliberadas em relação a `escolher_grupo`:

        * **Ignora `aberto` e `cheio`.** É o último recurso: se respeitasse os
          dois, cairia na mesma "vagas esgotadas" que ele existe para evitar.
        * **Ignora o limite da campanha**, respeitando só `g.capacidade`. Acima
          dela o convite falha do lado do WhatsApp — aí não há o que tentar.
        * **Sem `FOR UPDATE SKIP LOCKED`.** No fallback não há vaga a
          distribuir: todo mundo vai para o MESMO destino. O SKIP LOCKED faria
          a segunda requisição concorrente pular o grupo e cair em "vagas
          esgotadas" sem motivo — falha que só apareceria em produção, com dois
          cliques no mesmo instante.

        A ordem é sempre `posicao`, mesmo com estratégia aleatória: a decisão é
        "o primeiro grupo da ordem", não "um qualquer".
        """
        linha = self.db.execute(
            text(f"""
                SELECT cg.grupo_id, cg.posicao
                  FROM campanha_grupos cg
                  JOIN whatsapp_grupos g ON g.id = cg.grupo_id
                 WHERE cg.campanha_id = :campanha_id
                   AND g.ativo
                   AND g.link_convite IS NOT NULL
                   AND {CAPACIDADE_SQL}
                 ORDER BY cg.posicao, cg.grupo_id
                 LIMIT 1
            """),
            {"campanha_id": campanha_id},
        ).fetchone()
        return (linha[0], linha[1]) if linha else None

    def proximo_fechado(self, campanha_id: int) -> Optional[CampanhaGrupo]:
        """Próximo grupo da fila que está fechado mas ainda tem vaga — é o que
        a abertura automática abre quando o atual lota."""
        return (
            self.db.query(CampanhaGrupo)
            .join(WhatsappGrupo, WhatsappGrupo.id == CampanhaGrupo.grupo_id)
            .join(Campanha, Campanha.id == CampanhaGrupo.campanha_id)
            .filter(CampanhaGrupo.campanha_id == campanha_id,
                    CampanhaGrupo.aberto.is_(False),
                    WhatsappGrupo.ativo.is_(True),
                    WhatsappGrupo.link_convite.isnot(None),
                    cheio_efetivo().is_(False))
            .order_by(CampanhaGrupo.posicao)
            .first()
        )

    def lotados_abertos(self, campanha_id: int) -> List[CampanhaGrupo]:
        return (
            self.db.query(CampanhaGrupo)
            .join(WhatsappGrupo, WhatsappGrupo.id == CampanhaGrupo.grupo_id)
            .join(Campanha, Campanha.id == CampanhaGrupo.campanha_id)
            .filter(CampanhaGrupo.campanha_id == campanha_id,
                    CampanhaGrupo.aberto.is_(True),
                    cheio_efetivo().is_(True))
            .all()
        )

    def grupo(self, grupo_id: int) -> Optional[WhatsappGrupo]:
        return self.db.query(WhatsappGrupo).get(grupo_id)

    # --- eventos ------------------------------------------------------------

    def registrar_clique(self, link_id: int, grupo_id: Optional[int],
                         ip_hash: Optional[str], user_agent: Optional[str],
                         referer: Optional[str], is_teste: bool,
                         resultado: Optional[str] = None) -> CampanhaLinkEvento:
        evento = CampanhaLinkEvento(
            link_id=link_id, grupo_id=grupo_id, ip_hash=ip_hash,
            user_agent=(user_agent or "")[:500] or None,
            referer=(referer or "")[:500] or None, is_teste=is_teste,
            resultado=resultado,
        )
        self.db.add(evento)
        self.db.flush()
        return evento

    def clique_recente_do_grupo(self, grupo_id: int, desde: datetime
                                ) -> Optional[CampanhaLinkEvento]:
        """Clique mais recente roteado a este grupo — base da heurística que
        marca a entrada como vinda do link."""
        return (
            self.db.query(CampanhaLinkEvento)
            .filter(CampanhaLinkEvento.grupo_id == grupo_id,
                    CampanhaLinkEvento.is_teste.is_(False),
                    CampanhaLinkEvento.criado_em >= desde)
            .order_by(CampanhaLinkEvento.criado_em.desc())
            .first()
        )

    def registrar_evento_de_grupo(self, grupo_id: int, tipo: str, origem: str,
                                  identificador_hash: str,
                                  link_evento_id: Optional[int] = None,
                                  identificador: Optional[str] = None,
                                  identificador_tipo: Optional[str] = None) -> GrupoEvento:
        evento = GrupoEvento(grupo_id=grupo_id, tipo=tipo, origem=origem,
                             identificador_hash=identificador_hash,
                             identificador=identificador,
                             identificador_tipo=identificador_tipo,
                             link_evento_id=link_evento_id)
        self.db.add(evento)
        self.db.flush()
        return evento

    def atividade(self, grupo_ids: List[int], limite: int = 50,
                  cursor: Optional[Tuple[datetime, int]] = None,
                  tipo: Optional[str] = None,
                  grupo_id: Optional[int] = None) -> List[GrupoEvento]:
        """
        Página do feed, do mais recente para o mais antigo.

        **Keyset, não OFFSET.** `criado_em` empata em lote — uma entrada de 30
        pessoas grava 30 eventos no mesmo instante — e OFFSET sobre ordem
        ambígua repete e pula linhas entre páginas. O desempate é o `id`
        (BigInteger, PK), e o cursor é o par `(criado_em, id)` do último item.

        Pede `limite + 1` para quem chama saber se há próxima página sem uma
        segunda consulta.
        """
        if not grupo_ids:
            return []
        q = self.db.query(GrupoEvento).filter(GrupoEvento.grupo_id.in_(grupo_ids))
        if grupo_id is not None:
            q = q.filter(GrupoEvento.grupo_id == grupo_id)
        if tipo:
            q = q.filter(GrupoEvento.tipo == tipo)
        if cursor:
            quando, ident = cursor
            q = q.filter(
                tuple_(GrupoEvento.criado_em, GrupoEvento.id) < tuple_(quando, ident)
            )
        return (
            q.order_by(GrupoEvento.criado_em.desc(), GrupoEvento.id.desc())
            .limit(limite + 1)
            .all()
        )

    # --- métricas -----------------------------------------------------------

    def cliques_por_grupo(self, link_id: int) -> Dict[int, int]:
        linhas = (
            self.db.query(CampanhaLinkEvento.grupo_id, func.count(CampanhaLinkEvento.id))
            .filter(CampanhaLinkEvento.link_id == link_id,
                    CampanhaLinkEvento.is_teste.is_(False))
            .group_by(CampanhaLinkEvento.grupo_id)
            .all()
        )
        return {gid: int(n) for gid, n in linhas if gid is not None}

    def eventos_por_grupo(self, grupo_ids: List[int], inicio=None,
                          fim=None) -> Dict[int, Dict[str, int]]:
        """grupo_id → {entradas, saidas, ficaram}.

        "Ficaram" casa entrada e saída do MESMO identificador — é o número que
        sustenta custo por permanência (e a diferença dele para "entradas" é a
        evasão real, não uma estimativa).

        `inicio`/`fim` são datetimes UTC (fim exclusivo) e limitam as ENTRADAS e
        SAÍDAS à janela. Sem eles, conta tudo desde sempre.

        A saída que anula um "ficaram" NÃO é limitada pela janela de propósito:
        quem entrou no período e saiu depois dele não ficou. Restringir os dois
        lados infla a permanência quanto mais curto for o filtro.
        """
        if not grupo_ids:
            return {}
        q = (
            self.db.query(GrupoEvento.grupo_id, GrupoEvento.tipo,
                          func.count(GrupoEvento.id))
            .filter(GrupoEvento.grupo_id.in_(grupo_ids))
        )
        if inicio is not None:
            q = q.filter(GrupoEvento.criado_em >= inicio)
        if fim is not None:
            q = q.filter(GrupoEvento.criado_em < fim)
        linhas = q.group_by(GrupoEvento.grupo_id, GrupoEvento.tipo).all()
        resultado: Dict[int, Dict[str, int]] = {
            gid: {"entradas": 0, "saidas": 0, "ficaram": 0} for gid in grupo_ids
        }
        for gid, tipo, n in linhas:
            chave = "entradas" if tipo == EVENTO_ENTRADA else "saidas"
            resultado[gid][chave] = int(n)

        # Quem entrou e não saiu (por identificador).
        ficaram = self.db.execute(
            text("""
                SELECT e.grupo_id, COUNT(DISTINCT e.identificador_hash)
                  FROM grupo_eventos e
                 WHERE e.grupo_id = ANY(:ids)
                   AND e.tipo = :entrada
                   AND (:inicio IS NULL OR e.criado_em >= :inicio)
                   AND (:fim IS NULL OR e.criado_em < :fim)
                   AND NOT EXISTS (
                       SELECT 1 FROM grupo_eventos s
                        WHERE s.grupo_id = e.grupo_id
                          AND s.tipo = :saida
                          AND s.identificador_hash = e.identificador_hash
                          AND s.criado_em > e.criado_em
                   )
                 GROUP BY e.grupo_id
            """),
            {"ids": list(grupo_ids), "entrada": EVENTO_ENTRADA, "saida": EVENTO_SAIDA,
             "inicio": inicio, "fim": fim},
        ).fetchall()
        for gid, n in ficaram:
            resultado[gid]["ficaram"] = int(n)
        return resultado

    # --- snapshots ----------------------------------------------------------

    def upsert_snapshot(self, grupo_id: int, dia: date, participantes: int,
                        admins: int) -> None:
        existente = (
            self.db.query(GrupoSnapshot)
            .filter(GrupoSnapshot.grupo_id == grupo_id, GrupoSnapshot.data == dia)
            .first()
        )
        if existente:
            existente.participantes = participantes
            existente.admins = admins
            self.db.add(existente)
        else:
            self.db.add(GrupoSnapshot(grupo_id=grupo_id, data=dia,
                                      participantes=participantes, admins=admins))

    def snapshots_recentes(self, grupo_ids: List[int], dias: int = 30
                           ) -> Dict[int, List[GrupoSnapshot]]:
        if not grupo_ids:
            return {}
        desde = date.today() - timedelta(days=dias)
        linhas = (
            self.db.query(GrupoSnapshot)
            .filter(GrupoSnapshot.grupo_id.in_(grupo_ids), GrupoSnapshot.data >= desde)
            .order_by(GrupoSnapshot.data)
            .all()
        )
        agrupado: Dict[int, List[GrupoSnapshot]] = {}
        for s in linhas:
            agrupado.setdefault(s.grupo_id, []).append(s)
        return agrupado
