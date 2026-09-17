from typing import List, Optional
from sqlalchemy import func, text, update
from sqlalchemy.orm import Session
from app.models.custom_link import CustomLink
from app.models.custom_link_event import CustomLinkEvent
from app.schemas.custom_link import CustomLinkCreate, CustomLinkUpdate


class CustomLinkRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, id: int) -> Optional[CustomLink]:
        return self.db.query(CustomLink).filter(CustomLink.id == id).first()

    def get_by_slug(self, slug: str) -> Optional[CustomLink]:
        return self.db.query(CustomLink).filter(CustomLink.slug == slug).first()

    def get_by_user(self, user_id: int) -> List[CustomLink]:
        """Lista os links do usuário com `last_click_at` (atributo transiente,
        não é coluna mapeada) via outerjoin de subquery agregada — sem isso,
        link nunca clicado ficaria de fora do outerjoin em vez de vir com None.
        """
        last_click_sq = (
            self.db.query(
                CustomLinkEvent.custom_link_id.label("link_id"),
                func.max(CustomLinkEvent.created_at).label("last_click_at"),
            )
            .filter(CustomLinkEvent.user_id == user_id)
            .group_by(CustomLinkEvent.custom_link_id)
            .subquery()
        )
        rows = (
            self.db.query(CustomLink, last_click_sq.c.last_click_at)
            .outerjoin(last_click_sq, last_click_sq.c.link_id == CustomLink.id)
            .filter(CustomLink.user_id == user_id)
            .order_by(CustomLink.created_at.desc())
            .all()
        )
        links = []
        for link, last_click_at in rows:
            link.last_click_at = last_click_at
            links.append(link)
        return links

    def create(self, user_id: int, obj_in: CustomLinkCreate) -> CustomLink:
        db_obj = CustomLink(
            user_id=user_id,
            **obj_in.dict()
        )
        self.db.add(db_obj)
        self.db.commit()
        self.db.refresh(db_obj)
        return db_obj

    def update(self, db_obj: CustomLink, obj_in: CustomLinkUpdate) -> CustomLink:
        update_data = obj_in.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_obj, field, value)

        self.db.add(db_obj)
        self.db.commit()
        self.db.refresh(db_obj)
        return db_obj

    def delete(self, id: int) -> None:
        db_obj = self.get(id)
        if db_obj:
            self.db.delete(db_obj)
            self.db.commit()

    def increment_click_count(self, db_obj: CustomLink) -> None:
        """Conta 1 clique: evento + click_count, na mesma transação.

        Incidente de 17/09/2026: `click_count += 1` no Python gravava
        `SET click_count=10601` — o valor LIDO + 1. Cliques simultâneos no mesmo
        link gravavam o mesmo número (clique perdido) e, pior, seguravam a trava
        da linha durante a transação inteira. Com o disco do banco lento, os
        links mais clicados viraram fila de travas de até 118 s, prenderam as
        conexões e derrubaram o banco todo — login incluído.

        Agora:
        - o incremento é ATÔMICO no banco (`click_count = click_count + 1`);
        - o UPDATE (que trava a linha) é o último comando antes do commit;
        - `lock_timeout` curto: com a linha ocupada, desiste de contar este
          clique em vez de entrar na fila. Quem chama nunca deixa isso impedir
          o redirecionamento.
        """
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            self.db.execute(text("SET LOCAL lock_timeout = '2s'"))
            self.db.execute(text("SET LOCAL statement_timeout = '5s'"))
        # Ponto ÚNICO pós-dedup/bot: 1 evento (forward-only) com timestamp para a
        # série do insight. click_count segue sendo o total.
        self.db.add(CustomLinkEvent(custom_link_id=db_obj.id, user_id=db_obj.user_id))
        self.db.flush()
        self.db.execute(
            update(CustomLink)
            .where(CustomLink.id == db_obj.id)
            .values(click_count=func.coalesce(CustomLink.click_count, 0) + 1)
        )
        self.db.commit()
