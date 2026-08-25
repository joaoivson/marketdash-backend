from typing import List, Optional
from sqlalchemy import func
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

    def ids_de_links_de_grupo(self, user_id: int) -> set:
        """Ids de custom_links referenciados por whatsapp_grupos.custom_link_id."""
        from app.models.whatsapp_grupos import WhatsappGrupo

        linhas = (
            self.db.query(WhatsappGrupo.custom_link_id)
            .filter(
                WhatsappGrupo.user_id == user_id,
                WhatsappGrupo.custom_link_id.isnot(None),
            )
            .all()
        )
        return {i for (i,) in linhas}

    def criar_link_de_grupo(self, user_id: int, nome: str, slug: str) -> CustomLink:
        """Link interno de grupo de WhatsApp: add + flush SEM commit (transação
        do sync). Fora do fluxo/limite de Meus Links — a exclusão é pela FK
        whatsapp_grupos.custom_link_id, não pela tag (tag é texto livre da
        usuária e colidiria)."""
        link = CustomLink(
            user_id=user_id,
            name=nome[:120],
            original_url="https://shopee.com.br/",
            slug=slug,
            tag="whatsapp",
            is_active=True,
        )
        self.db.add(link)
        self.db.flush()
        return link

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

    def increment_click_count(self, db_obj: CustomLink) -> CustomLink:
        db_obj.click_count += 1
        self.db.add(db_obj)
        # Ponto ÚNICO pós-dedup/bot: grava também 1 evento (forward-only) com timestamp
        # para a série do insight, na MESMA transação. click_count segue sendo o total.
        self.db.add(CustomLinkEvent(custom_link_id=db_obj.id, user_id=db_obj.user_id))
        self.db.commit()
        self.db.refresh(db_obj)
        return db_obj
