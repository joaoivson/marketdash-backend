"""
Link de conexão externa — item 18 da spec.

A afiliada gera um link temporário e manda para quem está com o celular (uma
assistente, o dono do número). A pessoa abre, escaneia o QR e o número conecta —
**sem acesso à conta dela no MarketDash**.

A tela é PÚBLICA por necessidade: quem vai escanear não tem login aqui. Isso
torna o token a única barreira, então ele é tratado como senha:

  * 32 bytes aleatórios, e o banco guarda só o **hash** — vazar o banco não
    abre nenhum link;
  * **vida curta** (`CONEXAO_CONVITE_MINUTOS`, 15 por padrão): o tempo de mandar
    a mensagem e a pessoa escanear, não mais;
  * **morre ao conectar**, não no fim do prazo. Link de pareamento que continua
    valendo depois de pareado é um convite para outra pessoa conectar OUTRO
    número no lugar;
  * expõe **só o QR daquela sessão**. Nenhum dado da conta, nenhuma outra rota;
  * revogável pela afiliada a qualquer momento.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.conexao_convite import ConexaoConvite

logger = logging.getLogger(__name__)


class ConviteInvalido(Exception):
    """Token inexistente, expirado, já usado ou revogado — a tela mostra o
    mesmo texto para os quatro: dizer QUAL deles é ajudar quem está tentando
    adivinhar."""


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


class ConexaoConviteService:
    def __init__(self, db: Session):
        self.db = db

    def criar(self, user_id: int, instancia_id: int) -> Tuple[ConexaoConvite, str]:
        """
        Devolve `(convite, token em claro)`. O token em claro só existe aqui e
        na resposta HTTP — nunca é gravado.

        Convites anteriores da MESMA sessão são revogados: dois links vivos
        para o mesmo número significam que o primeiro, que ela talvez tenha
        mandado no grupo errado, continua funcionando.
        """
        agora = datetime.now(timezone.utc)
        (self.db.query(ConexaoConvite)
         .filter(ConexaoConvite.instancia_id == instancia_id,
                 ConexaoConvite.usado_em.is_(None),
                 ConexaoConvite.revogado_em.is_(None))
         .update({"revogado_em": agora}, synchronize_session=False))

        token = secrets.token_urlsafe(32)
        convite = ConexaoConvite(
            user_id=user_id, instancia_id=instancia_id, token_hash=_hash(token),
            expira_em=agora + timedelta(minutes=settings.CONEXAO_CONVITE_MINUTOS),
        )
        self.db.add(convite)
        self.db.flush()
        return convite, token

    def resolver(self, token: str) -> ConexaoConvite:
        """Convite válido para este token, ou `ConviteInvalido`."""
        convite = (
            self.db.query(ConexaoConvite)
            .filter(ConexaoConvite.token_hash == _hash(token))
            .first()
        )
        if not convite:
            raise ConviteInvalido()
        if convite.revogado_em or convite.usado_em:
            raise ConviteInvalido()
        if convite.expira_em <= datetime.now(timezone.utc):
            raise ConviteInvalido()
        return convite

    def marcar_usado(self, convite: ConexaoConvite) -> None:
        convite.usado_em = datetime.now(timezone.utc)
        self.db.add(convite)
        self.db.commit()
        logger.info("Convite de conexão %s consumido", convite.id)

    def revogar(self, user_id: int, convite_id: int) -> bool:
        convite = (
            self.db.query(ConexaoConvite)
            .filter(ConexaoConvite.id == convite_id,
                    ConexaoConvite.user_id == user_id)
            .first()
        )
        if not convite or convite.revogado_em:
            return False
        convite.revogado_em = datetime.now(timezone.utc)
        self.db.add(convite)
        return True

    def ativos_da_instancia(self, user_id: int,
                            instancia_id: int) -> List[ConexaoConvite]:
        return (
            self.db.query(ConexaoConvite)
            .filter(ConexaoConvite.user_id == user_id,
                    ConexaoConvite.instancia_id == instancia_id,
                    ConexaoConvite.usado_em.is_(None),
                    ConexaoConvite.revogado_em.is_(None),
                    ConexaoConvite.expira_em > datetime.now(timezone.utc))
            .order_by(ConexaoConvite.criado_em.desc())
            .all()
        )

    def url_publica(self, token: str, base: Optional[str] = None) -> str:
        """
        Montada da base pública configurada, NUNCA de `url_for`: atrás do proxy
        o `url_for` gera `http`, toma 301 e o link chega quebrado para quem
        precisa escanear.
        """
        raiz = (base or settings.FRONTEND_URL or "").rstrip("/")
        return f"{raiz}/conectar/{token}"
