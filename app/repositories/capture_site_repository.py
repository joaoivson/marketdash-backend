from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.capture_site import CaptureSite
from app.schemas.capture_site import CaptureSiteCreate, CaptureSiteUpdate

class CaptureSiteRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, id: int) -> Optional[CaptureSite]:
        return self.db.query(CaptureSite).filter(CaptureSite.id == id).first()

    def get_by_slug(self, slug: str) -> Optional[CaptureSite]:
        return self.db.query(CaptureSite).filter(CaptureSite.slug == slug).first()

    def get_by_user(self, user_id: int) -> List[CaptureSite]:
        return self.db.query(CaptureSite).filter(CaptureSite.user_id == user_id).all()

    def create(self, user_id: int, obj_in: CaptureSiteCreate) -> CaptureSite:
        db_obj = CaptureSite(
            user_id=user_id,
            **obj_in.dict()
        )
        self.db.add(db_obj)
        self.db.commit()
        self.db.refresh(db_obj)
        return db_obj

    def update(self, db_obj: CaptureSite, obj_in: CaptureSiteUpdate) -> CaptureSite:
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
