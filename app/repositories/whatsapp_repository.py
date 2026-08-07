"""Acesso ao opt-in do WhatsApp e ao log de envios."""
from datetime import date, datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.subscription import Subscription
from app.models.whatsapp import (
    ENVIO_OK, STATUS_CONFIRMADO, TIPO_RESUMO, WhatsappEnvio, WhatsappOptin,
)


class WhatsappRepository:
    def __init__(self, db: Session):
        self.db = db

    # --- opt-in ------------------------------------------------------------

    def por_usuario(self, user_id: int) -> Optional[WhatsappOptin]:
        return (
            self.db.query(WhatsappOptin)
            .filter(WhatsappOptin.user_id == user_id)
            .first()
        )

    def por_numero(self, numero: str) -> List[WhatsappOptin]:
        """
        Lista, não um só: o webhook chega com o número, e nada impede que duas
        contas tenham cadastrado o mesmo celular (casal, sócias, revenda). O
        SAIR precisa desligar todas — desligar uma e deixar a outra mandando
        mensagem é pior do que não ter SAIR.
        """
        return (
            self.db.query(WhatsappOptin)
            .filter(WhatsappOptin.numero == numero)
            .all()
        )

    def salvar(self, optin: WhatsappOptin) -> WhatsappOptin:
        optin.atualizado_em = datetime.now(timezone.utc)
        self.db.add(optin)
        self.db.commit()
        self.db.refresh(optin)
        return optin

    def confirmados_dos_planos(self, planos: Tuple[str, ...]) -> List[Tuple[WhatsappOptin, str]]:
        """
        Opt-ins confirmados cuja assinatura está num dos planos dados.

        O filtro de plano vive na consulta, e não num `if` depois: assim uma
        afiliada que caiu de Pro para Essencial simplesmente para de entrar no
        lote, sem ninguém precisar lembrar de limpar o opt-in dela.
        """
        return (
            self.db.query(WhatsappOptin, Subscription.plan)
            .join(Subscription, Subscription.user_id == WhatsappOptin.user_id)
            .filter(
                WhatsappOptin.status == STATUS_CONFIRMADO,
                func.lower(func.coalesce(Subscription.plan, "")).in_(planos),
            )
            .order_by(WhatsappOptin.user_id)
            .all()
        )

    # --- log de envio ------------------------------------------------------

    def ja_enviou(self, user_id: int, tipo: str, referencia: date) -> bool:
        return (
            self.db.query(WhatsappEnvio.id)
            .filter(
                WhatsappEnvio.user_id == user_id,
                WhatsappEnvio.tipo == tipo,
                WhatsappEnvio.referencia == referencia,
                WhatsappEnvio.status == ENVIO_OK,
            )
            .first()
            is not None
        )

    def registrar_envio(self, user_id: int, tipo: str, status: str,
                        referencia: Optional[date] = None,
                        erro: Optional[str] = None) -> bool:
        """
        Grava o envio. Devolve False quando o índice único recusou — outra
        execução do cron já mandou esta mensagem, e a duplicata para aqui.
        """
        registro = WhatsappEnvio(
            user_id=user_id, tipo=tipo, referencia=referencia,
            status=status, erro=(erro or None),
        )
        self.db.add(registro)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return False
        return True

    def enviados_no_dia(self, dia: date) -> int:
        """Quantas mensagens já saíram hoje — alimenta o teto diário."""
        return (
            self.db.query(func.count(WhatsappEnvio.id))
            .filter(
                WhatsappEnvio.status == ENVIO_OK,
                func.date(WhatsappEnvio.criado_em) == dia,
            )
            .scalar()
        ) or 0

    def ultimos_envios(self, limite: int = 50) -> List[WhatsappEnvio]:
        return (
            self.db.query(WhatsappEnvio)
            .order_by(WhatsappEnvio.criado_em.desc())
            .limit(limite)
            .all()
        )

    def resumo_do_dia(self, dia: date) -> dict:
        linhas = (
            self.db.query(WhatsappEnvio.status, func.count(WhatsappEnvio.id))
            .filter(
                WhatsappEnvio.tipo == TIPO_RESUMO,
                func.date(WhatsappEnvio.criado_em) == dia,
            )
            .group_by(WhatsappEnvio.status)
            .all()
        )
        return {status: int(n) for status, n in linhas}
