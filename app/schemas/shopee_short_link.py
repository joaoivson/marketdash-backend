import re
from typing import Optional

from pydantic import BaseModel, field_validator

_SUB_ID_RE = re.compile(r"^[A-Za-z0-9]+$")


class ShortLinkRequest(BaseModel):
    originUrl: str
    subId: Optional[str] = None

    @field_validator("originUrl", mode="before")
    @classmethod
    def strip_origin_url(cls, v: str) -> str:
        return v.strip() if isinstance(v, str) else v

    @field_validator("subId", mode="before")
    @classmethod
    def normalize_sub_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        if not _SUB_ID_RE.match(v):
            raise ValueError("subId deve conter apenas letras e números (A-Za-z0-9).")
        return v


class ShortLinkResponse(BaseModel):
    shortLink: str
