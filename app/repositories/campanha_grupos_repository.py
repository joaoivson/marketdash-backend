"""Acesso às campanhas de grupos e aos vínculos campanha↔grupo."""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.campanha_grupos import (
    CAMPANHA_ARQUIVADA, CAMPANHA_ENCERRADA, Campanha, CampanhaGrupo,
)


class CampanhaGruposRepository:
    def __init__(self, db: Session):
        self.db = db

    def por_usuario(self, user_id: int, incluir_arquivadas: bool = False) -> List[Campanha]:
        """Campanhas da usuária. `encerrada` NUNCA volta — é terminal."""
        q = (
            self.db.query(Campanha)
            .filter(Campanha.user_id == user_id,
                    Campanha.status != CAMPANHA_ENCERRADA)
        )
        if not incluir_arquivadas:
            q = q.filter(Campanha.status != CAMPANHA_ARQUIVADA)
        return q.order_by(Campanha.criado_em.desc()).all()

    def por_id(self, user_id: int, campanha_id: int) -> Optional[Campanha]:
        """Ownership embutida. Encerrada devolve None — vira 404, como deve:
        a campanha excluída não abre mais, só o `/g/{slug}` dela responde."""
        return (
            self.db.query(Campanha)
            .filter(Campanha.id == campanha_id, Campanha.user_id == user_id,
                    Campanha.status != CAMPANHA_ENCERRADA)
            .first()
        )

    def contagem_de_grupos(self, user_id: int) -> Dict[int, int]:
        """campanha_id → nº de grupos, para a listagem (sem N+1)."""
        linhas = (
            self.db.query(CampanhaGrupo.campanha_id, func.count(CampanhaGrupo.grupo_id))
            .join(Campanha, Campanha.id == CampanhaGrupo.campanha_id)
            .filter(Campanha.user_id == user_id)
            .group_by(CampanhaGrupo.campanha_id)
            .all()
        )
        return {cid: int(n) for cid, n in linhas}

    def vinculos(self, campanha_id: int) -> List[CampanhaGrupo]:
        return (
            self.db.query(CampanhaGrupo)
            .filter(CampanhaGrupo.campanha_id == campanha_id)
            .order_by(CampanhaGrupo.posicao)
            .all()
        )

    def total_ativas(self, user_id: int) -> int:
        """Denominador do limite do plano — encerrada não ocupa vaga."""
        return (
            self.db.query(func.count(Campanha.id))
            .filter(Campanha.user_id == user_id,
                    Campanha.status.notin_((CAMPANHA_ARQUIVADA, CAMPANHA_ENCERRADA)))
            .scalar()
        ) or 0

    def adicionar(self, campanha: Campanha) -> Campanha:
        self.db.add(campanha)
        self.db.flush()
        return campanha

    def substituir_vinculos(self, campanha_id: int, itens: List[Tuple]) -> None:
        """Substitui o conjunto: itens = [(grupo_id, posicao, aberto[, cheio_override])].

        Upsert + remoção dos ausentes em memória (o conjunto é pequeno — dezenas
        de grupos por campanha), numa transação só; o commit é de quem chamou.
        """
        atuais = {v.grupo_id: v for v in self.vinculos(campanha_id)}
        desejados = {item[0] for item in itens}
        for grupo_id, posicao, aberto, *resto in itens:
            # `cheio_override` é o 4º elemento e é OPCIONAL: chamador antigo
            # (e teste) manda a tupla de 3 e não mexe no override.
            vinculo = atuais.get(grupo_id)
            if vinculo:
                vinculo.posicao = posicao
                vinculo.aberto = aberto
                if resto:
                    vinculo.cheio_override = resto[0]
            else:
                self.db.add(CampanhaGrupo(
                    campanha_id=campanha_id, grupo_id=grupo_id,
                    posicao=posicao, aberto=aberto,
                    cheio_override=(resto[0] if resto else None),
                ))
        for grupo_id, vinculo in atuais.items():
            if grupo_id not in desejados:
                self.db.delete(vinculo)

    def marcar_tocada(self, campanha: Campanha) -> None:
        campanha.atualizado_em = datetime.now(timezone.utc)
        self.db.add(campanha)
