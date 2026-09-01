"""Acesso ao pool de servidores WAHA (waha_servidores) e à ocupação por sessão.

Mesmo desenho do `whatsapp_proxy_repository`: `contagem_de_sessoes` é UMA query
com GROUP BY porque o pool é lido no caminho de criar número e um N+1 aqui
viraria uma consulta por servidor a cada tela aberta.

`max_sessoes` conta apenas instância ATIVA — contar o histórico deixaria o pool
cheio de vagas fantasma, que foi o bug que a contagem do proxy já evitou.
"""
from typing import Dict, List, Optional, Set

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.waha_servidores import WahaServidor
from app.models.whatsapp_grupos import INSTANCIA_REMOVIDA, WhatsappInstancia


class WahaServidorRepository:
    def __init__(self, db: Session):
        self.db = db

    def listar(self, ativos_apenas: bool = False) -> List[WahaServidor]:
        q = self.db.query(WahaServidor)
        if ativos_apenas:
            q = q.filter(WahaServidor.ativo.is_(True))
        return q.order_by(WahaServidor.id).all()

    def por_id(self, servidor_id: int) -> Optional[WahaServidor]:
        return self.db.query(WahaServidor).filter(WahaServidor.id == servidor_id).first()

    def por_rotulo(self, rotulo: str) -> Optional[WahaServidor]:
        return self.db.query(WahaServidor).filter(WahaServidor.rotulo == rotulo).first()

    def contagem_de_sessoes(self) -> Dict[int, int]:
        """{servidor_id: nº de instâncias ativas}. Removida não ocupa vaga."""
        linhas = (
            self.db.query(WhatsappInstancia.servidor_id, func.count(WhatsappInstancia.id))
            .filter(WhatsappInstancia.servidor_id.isnot(None),
                    WhatsappInstancia.status != INSTANCIA_REMOVIDA)
            .group_by(WhatsappInstancia.servidor_id)
            .all()
        )
        return {servidor_id: total for servidor_id, total in linhas}

    def usuarias_por_servidor(self) -> Dict[int, Set[int]]:
        """{servidor_id: {user_id, ...}} — base da regra de afinidade.

        Ao contrário do proxy, aqui a afinidade é só uma PREFERÊNCIA: dividir
        servidor com outra afiliada não contamina ninguém (o IP é do proxy, não
        do servidor). Manter os 3 chips da mesma pessoa juntos existe para
        simplificar debug e o roteiro de shard morto — não por isolamento.
        """
        linhas = (
            self.db.query(WhatsappInstancia.servidor_id, WhatsappInstancia.user_id)
            .filter(WhatsappInstancia.servidor_id.isnot(None),
                    WhatsappInstancia.status != INSTANCIA_REMOVIDA)
            .distinct()
            .all()
        )
        mapa: Dict[int, Set[int]] = {}
        for servidor_id, user_id in linhas:
            mapa.setdefault(servidor_id, set()).add(user_id)
        return mapa

    def capacidade_total(self) -> int:
        """SUM(max_sessoes) dos servidores ativos — substitui a constante
        WHATSAPP_MAX_INSTANCIAS_GLOBAL como teto real da plataforma."""
        total = (
            self.db.query(func.coalesce(func.sum(WahaServidor.max_sessoes), 0))
            .filter(WahaServidor.ativo.is_(True))
            .scalar()
        )
        return int(total or 0)
