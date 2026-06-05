from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base


class FacebookIntegration(Base):
    """Credenciais e estado da integração com a Facebook Marketing API.

    Espelha o padrão de ShopeeIntegration: 1 registro por usuário, token de
    acesso (long-lived) criptografado via Fernet, e a ad account selecionada
    de onde as campanhas são sincronizadas.
    """

    __tablename__ = "facebook_integrations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # Identidade do usuário no Facebook
    fb_user_id = Column(String(64), nullable=True)
    fb_user_name = Column(String(255), nullable=True)

    # Token de acesso long-lived (criptografado com Fernet, igual à senha Shopee)
    encrypted_access_token = Column(Text, nullable=False)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Conta de anúncios selecionada (formato "act_123456789")
    ad_account_id = Column(String(64), nullable=True)
    ad_account_name = Column(String(255), nullable=True)

    # Escopos concedidos no OAuth (csv) — ex: "ads_read,ads_management"
    scopes = Column(Text, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationship
    user = relationship("User", back_populates="facebook_integration")
