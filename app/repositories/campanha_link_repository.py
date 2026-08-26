"""Acesso ao link de entrada, seus eventos e aos eventos/snapshots de grupo."""
import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.campanha_grupos import Campanha, CampanhaGrupo
from app.models.campanha_link import (
    EVENTO_ENTRADA, EVENTO_SAIDA, CampanhaLink, CampanhaLinkEvento,
    GrupoEvento, GrupoSnapshot,
)
from app.models.whatsapp_grupos import WhatsappGrupo


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
                 WHERE cg.campanha_id = :campanha_id
                   AND cg.aberto
                   AND g.ativo
                   AND g.link_convite IS NOT NULL
                   AND g.participantes < g.capacidade
                 ORDER BY {ordem}
                 LIMIT 1
                   FOR UPDATE OF cg SKIP LOCKED
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
            .filter(CampanhaGrupo.campanha_id == campanha_id,
                    CampanhaGrupo.aberto.is_(False),
                    WhatsappGrupo.ativo.is_(True),
                    WhatsappGrupo.link_convite.isnot(None),
                    WhatsappGrupo.participantes < WhatsappGrupo.capacidade)
            .order_by(CampanhaGrupo.posicao)
            .first()
        )

    def lotados_abertos(self, campanha_id: int) -> List[CampanhaGrupo]:
        return (
            self.db.query(CampanhaGrupo)
            .join(WhatsappGrupo, WhatsappGrupo.id == CampanhaGrupo.grupo_id)
            .filter(CampanhaGrupo.campanha_id == campanha_id,
                    CampanhaGrupo.aberto.is_(True),
                    WhatsappGrupo.participantes >= WhatsappGrupo.capacidade)
            .all()
        )

    def grupo(self, grupo_id: int) -> Optional[WhatsappGrupo]:
        return self.db.query(WhatsappGrupo).get(grupo_id)

    # --- eventos ------------------------------------------------------------

    def registrar_clique(self, link_id: int, grupo_id: Optional[int],
                         ip_hash: Optional[str], user_agent: Optional[str],
                         referer: Optional[str], is_teste: bool) -> CampanhaLinkEvento:
        evento = CampanhaLinkEvento(
            link_id=link_id, grupo_id=grupo_id, ip_hash=ip_hash,
            user_agent=(user_agent or "")[:500] or None,
            referer=(referer or "")[:500] or None, is_teste=is_teste,
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
                                  link_evento_id: Optional[int] = None) -> GrupoEvento:
        evento = GrupoEvento(grupo_id=grupo_id, tipo=tipo, origem=origem,
                             identificador_hash=identificador_hash,
                             link_evento_id=link_evento_id)
        self.db.add(evento)
        self.db.flush()
        return evento

    def atividade(self, grupo_ids: List[int], limite: int = 50) -> List[GrupoEvento]:
        if not grupo_ids:
            return []
        return (
            self.db.query(GrupoEvento)
            .filter(GrupoEvento.grupo_id.in_(grupo_ids))
            .order_by(GrupoEvento.criado_em.desc())
            .limit(limite)
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

    def eventos_por_grupo(self, grupo_ids: List[int]) -> Dict[int, Dict[str, int]]:
        """grupo_id → {entradas, saidas, ficaram}.

        "Ficaram" casa entrada e saída do MESMO identificador — é o número que
        sustenta custo por permanência (e a diferença dele para "entradas" é a
        evasão real, não uma estimativa).
        """
        if not grupo_ids:
            return {}
        linhas = (
            self.db.query(GrupoEvento.grupo_id, GrupoEvento.tipo,
                          func.count(GrupoEvento.id))
            .filter(GrupoEvento.grupo_id.in_(grupo_ids))
            .group_by(GrupoEvento.grupo_id, GrupoEvento.tipo)
            .all()
        )
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
                   AND NOT EXISTS (
                       SELECT 1 FROM grupo_eventos s
                        WHERE s.grupo_id = e.grupo_id
                          AND s.tipo = :saida
                          AND s.identificador_hash = e.identificador_hash
                          AND s.criado_em > e.criado_em
                   )
                 GROUP BY e.grupo_id
            """),
            {"ids": list(grupo_ids), "entrada": EVENTO_ENTRADA, "saida": EVENTO_SAIDA},
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
