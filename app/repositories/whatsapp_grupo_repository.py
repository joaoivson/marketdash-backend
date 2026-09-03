"""Acesso aos grupos sincronizados e ao vínculo N:N grupo↔instância."""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from sqlalchemy import exists

from app.models.whatsapp_grupos import WhatsappGrupo, WhatsappGrupoInstancia


class WhatsappGrupoRepository:
    def __init__(self, db: Session):
        self.db = db

    def por_usuario(self, user_id: int, apenas_ativos: bool = True,
                    instancia_id: Optional[int] = None,
                    busca: Optional[str] = None,
                    apenas_ativados: bool = False) -> List[WhatsappGrupo]:
        q = self.db.query(WhatsappGrupo).filter(WhatsappGrupo.user_id == user_id)
        if apenas_ativos:
            q = q.filter(WhatsappGrupo.ativo.is_(True))
        if apenas_ativados:
            # Toggle da usuária (074) — eixo separado do `ativo` do sync.
            q = q.filter(WhatsappGrupo.ativado.is_(True))
        if instancia_id is not None:
            q = q.join(
                WhatsappGrupoInstancia,
                WhatsappGrupoInstancia.grupo_id == WhatsappGrupo.id,
            ).filter(WhatsappGrupoInstancia.instancia_id == instancia_id)
        if busca:
            q = q.filter(WhatsappGrupo.nome.ilike(f"%{busca}%"))
        return q.order_by(WhatsappGrupo.nome).all()

    def por_id(self, user_id: int, grupo_id: int) -> Optional[WhatsappGrupo]:
        """Ownership embutida: id de outra dona devolve None (vira 404)."""
        return (
            self.db.query(WhatsappGrupo)
            .filter(WhatsappGrupo.user_id == user_id, WhatsappGrupo.id == grupo_id)
            .first()
        )

    def total_ativados(self, user_id: int) -> int:
        """Denominador do limite `whatsapp_grupos` do plano."""
        return (
            self.db.query(WhatsappGrupo)
            .filter(WhatsappGrupo.user_id == user_id,
                    WhatsappGrupo.ativado.is_(True))
            .count()
        )

    def por_jid(self, user_id: int, jid: str) -> Optional[WhatsappGrupo]:
        return (
            self.db.query(WhatsappGrupo)
            .filter(WhatsappGrupo.user_id == user_id, WhatsappGrupo.jid == jid)
            .first()
        )

    def por_jids(self, user_id: int) -> Dict[str, WhatsappGrupo]:
        """Mapa jid→grupo do usuário — preload que mata o SELECT-por-grupo do sync."""
        return {
            g.jid: g
            for g in self.db.query(WhatsappGrupo).filter(WhatsappGrupo.user_id == user_id)
        }

    def vinculos_da_instancia(self, instancia_id: int) -> Dict[int, WhatsappGrupoInstancia]:
        """grupo_id→vínculo da sessão — preload do upsert N:N."""
        return {
            v.grupo_id: v
            for v in self.db.query(WhatsappGrupoInstancia).filter(
                WhatsappGrupoInstancia.instancia_id == instancia_id
            )
        }

    def adicionar(self, grupo: WhatsappGrupo) -> WhatsappGrupo:
        """add + flush (garante o id) SEM commit — o sync é uma transação só."""
        self.db.add(grupo)
        self.db.flush()
        return grupo

    def vincular_instancia(self, grupo_id: int, instancia_id: int, sou_admin: bool,
                           vinculo: Optional[WhatsappGrupoInstancia] = None) -> None:
        """Upsert do vínculo N:N — o mesmo grupo pode ter 2 números da afiliada."""
        if vinculo is not None:
            vinculo.sou_admin = sou_admin
        else:
            self.db.add(WhatsappGrupoInstancia(
                grupo_id=grupo_id, instancia_id=instancia_id, sou_admin=sou_admin,
            ))

    def desvincular_instancia(self, instancia_id: int, exceto_grupo_ids: List[int]) -> int:
        """Remove vínculos de grupos que a sessão não vê mais neste sync."""
        q = self.db.query(WhatsappGrupoInstancia).filter(
            WhatsappGrupoInstancia.instancia_id == instancia_id
        )
        if exceto_grupo_ids:
            q = q.filter(~WhatsappGrupoInstancia.grupo_id.in_(exceto_grupo_ids))
        n = q.delete(synchronize_session=False)
        return n

    def instancias_por_grupo(self, user_id: int) -> Dict[int, List[int]]:
        """grupo_id → [instancia_id] para a tela mostrar por qual número enviar."""
        linhas = (
            self.db.query(WhatsappGrupoInstancia.grupo_id, WhatsappGrupoInstancia.instancia_id)
            .join(WhatsappGrupo, WhatsappGrupo.id == WhatsappGrupoInstancia.grupo_id)
            .filter(WhatsappGrupo.user_id == user_id)
            .all()
        )
        resultado: Dict[int, List[int]] = {}
        for grupo_id, instancia_id in linhas:
            resultado.setdefault(grupo_id, []).append(instancia_id)
        return resultado

    def desativar_sem_vinculo(self, user_id: int) -> int:
        """UPDATE único: grupos do usuário sem NENHUMA sessão viram inativos."""
        n = (
            self.db.query(WhatsappGrupo)
            .filter(
                WhatsappGrupo.user_id == user_id,
                WhatsappGrupo.ativo.is_(True),
                ~exists().where(WhatsappGrupoInstancia.grupo_id == WhatsappGrupo.id),
            )
            .update(
                {"ativo": False, "atualizado_em": datetime.now(timezone.utc)},
                synchronize_session=False,
            )
        )
        return int(n or 0)

    def marcar_tocado(self, grupo: WhatsappGrupo) -> None:
        grupo.atualizado_em = datetime.now(timezone.utc)
        self.db.add(grupo)
