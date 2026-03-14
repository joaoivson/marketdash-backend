from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.custom_link import CustomLink
from app.schemas.custom_link import CustomLinkCreate, CustomLinkUpdate


class CustomLinkRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, id: int) -> Optional[CustomLink]:
        return self.db.query(CustomLink).filter(CustomLink.id == id).first()

    def get_by_slug(self, slug: str) -> Optional[CustomLink]:
        return self.db.query(CustomLink).filter(CustomLink.slug == slug).first()

    def get_by_user(self, user_id: int) -> List[CustomLink]:
        return (
            self.db.query(CustomLink)
            .filter(CustomLink.user_id == user_id)
            .order_by(CustomLink.created_at.desc())
            .all()
        )

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
        self.db.commit()
        self.db.refresh(db_obj)
        return db_obj
