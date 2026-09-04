import json

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

    # Conta de anúncios "principal" (legado/compat — primeira selecionada). Formato "act_123".
    ad_account_id = Column(String(64), nullable=True)
    ad_account_name = Column(String(255), nullable=True)

    # Múltiplas contas selecionadas (JSON: ["act_123", "act_456"]). Fonte da verdade do sync.
    ad_accounts_json = Column(Text, nullable=True)

    # Nomes das contas selecionadas (JSON: {"act_123": "Nome"}) — persistidos na
    # seleção para o status não precisar bater na Graph. A seleção em si continua
    # sendo ad_accounts_json; isto é só metadado de exibição.
    ad_accounts_names_json = Column(Text, nullable=True)

    # Escopos concedidos no OAuth (csv) — ex: "ads_read,ads_management"
    scopes = Column(Text, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationship
    user = relationship("User", back_populates="facebook_integration")

    def account_ids_list(self) -> list[str]:
        """Lista de contas selecionadas (act_...). Faz fallback para a coluna legada."""
        if self.ad_accounts_json:
            try:
                value = json.loads(self.ad_accounts_json)
                if isinstance(value, list):
                    return [str(x) for x in value if x]
            except (ValueError, TypeError):
                pass
        return [self.ad_account_id] if self.ad_account_id else []

    def account_meta_dict(self) -> dict[str, dict]:
        """Metadado de exibição por conta: {"act_123": {"name": ..., "currency": ...}}.

        Aceita os DOIS formatos gravados nesta coluna: o de agora (dict) e o
        original ({"act_123": "Nome"}), porque a seleção de quem já estava
        conectada foi gravada no formato antigo e reescrever tudo exigiria
        migration de dado — a leitura tolerante custa menos e não perde nome.
        """
        if not self.ad_accounts_names_json:
            return {}
        try:
            value = json.loads(self.ad_accounts_names_json)
        except (ValueError, TypeError):
            return {}
        if not isinstance(value, dict):
            return {}
        meta: dict[str, dict] = {}
        for chave, bruto in value.items():
            if not chave:
                continue
            if isinstance(bruto, dict):
                nome = bruto.get("name")
                moeda = bruto.get("currency")
            else:
                nome, moeda = bruto, None
            if not nome and not moeda:
                continue
            meta[str(chave)] = {
                "name": str(nome) if nome else None,
                "currency": str(moeda) if moeda else None,
            }
        return meta

    def account_names_dict(self) -> dict[str, str]:
        """Só os nomes — mantido para quem já lia este formato."""
        return {
            chave: dados["name"]
            for chave, dados in self.account_meta_dict().items()
            if dados.get("name")
        }
