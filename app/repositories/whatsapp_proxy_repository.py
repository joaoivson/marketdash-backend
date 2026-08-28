"""Acesso ao pool de proxies (whatsapp_proxies) e à ocupação por sessão.

Duas consultas aqui carregam as regras que o schema não expressa:
`contagem_de_sessoes` (o `max_sessoes` conta só instância ATIVA) e
`usuarias_por_proxy` (afinidade: um proxy nunca serve duas afiliadas). Ambas
são UMA query com GROUP BY — o pool é lido no caminho de criar/parear número,
e N+1 aqui viraria uma consulta por proxy a cada tela aberta.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.whatsapp_grupos import INSTANCIA_REMOVIDA, WhatsappInstancia
from app.models.whatsapp_proxies import WhatsappProxy


class WhatsappProxyRepository:
    def __init__(self, db: Session):
        self.db = db

    def listar(self, ativos_apenas: bool = False) -> List[WhatsappProxy]:
        q = self.db.query(WhatsappProxy)
        if ativos_apenas:
            q = q.filter(WhatsappProxy.ativo.is_(True))
        return q.order_by(WhatsappProxy.id).all()

    def por_id(self, proxy_id: int) -> Optional[WhatsappProxy]:
        return self.db.query(WhatsappProxy).filter(WhatsappProxy.id == proxy_id).first()

    def contagem_de_sessoes(self) -> Dict[int, int]:
        """{proxy_id: nº de instâncias ativas}. Removida não ocupa vaga —
        contar o histórico deixaria o pool cheio de fantasmas."""
        linhas = (
            self.db.query(WhatsappInstancia.proxy_id, func.count(WhatsappInstancia.id))
            .filter(WhatsappInstancia.proxy_id.isnot(None),
                    WhatsappInstancia.status != INSTANCIA_REMOVIDA)
            .group_by(WhatsappInstancia.proxy_id)
            .all()
        )
        return {proxy_id: total for proxy_id, total in linhas}

    def usuarias_por_proxy(self) -> Dict[int, Set[int]]:
        """{proxy_id: {user_id, ...}} — base da regra de afinidade.

        Chip de usuárias diferentes NÃO divide IP: um banimento contaminaria a
        vizinhança inteira. Chips da MESMA afiliada, sim — é o retrato coerente
        de uma pessoa com três aparelhos na mesma casa (e derruba o custo de
        3 IPs por afiliada para 1).
        """
        linhas = (
            self.db.query(WhatsappInstancia.proxy_id, WhatsappInstancia.user_id)
            .filter(WhatsappInstancia.proxy_id.isnot(None),
                    WhatsappInstancia.status != INSTANCIA_REMOVIDA)
            .distinct()
            .all()
        )
        mapa: Dict[int, Set[int]] = {}
        for proxy_id, user_id in linhas:
            mapa.setdefault(proxy_id, set()).add(user_id)
        return mapa

    def instancias_do_proxy(self, proxy_id: int) -> List[WhatsappInstancia]:
        return (
            self.db.query(WhatsappInstancia)
            .filter(WhatsappInstancia.proxy_id == proxy_id,
                    WhatsappInstancia.status != INSTANCIA_REMOVIDA)
            .order_by(WhatsappInstancia.id)
            .all()
        )

    def salvar(self, proxy: WhatsappProxy) -> WhatsappProxy:
        proxy.atualizado_em = datetime.now(timezone.utc)
        self.db.add(proxy)
        self.db.commit()
        self.db.refresh(proxy)
        return proxy

    def criar(self, proxy: WhatsappProxy) -> WhatsappProxy:
        return self.salvar(proxy)

    def desativar(self, proxy: WhatsappProxy) -> WhatsappProxy:
        """Soft-delete: `ativo=false`. Apagar a linha zeraria o `proxy_id` das
        instâncias (ON DELETE SET NULL) e perderíamos o histórico de qual IP
        atendeu qual chip — que é o dado da investigação quando um número cai."""
        proxy.ativo = False
        return self.salvar(proxy)
