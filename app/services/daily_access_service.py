"""
Registro de acessos — uma linha por autenticação efetiva.

Regra (rodada 4): acesso = cada autenticação. Logou de manhã = 1; sessão caiu e
relogou à tarde = 2; renovação silenciosa de token com a pessoa ativa não conta.
Substitui a regra anterior de 1 registro por usuário/dia, que escondia a
diferença entre "abriu uma vez" e "usa o dia todo".

Quem garante que renovação de token não vira acesso é o frontend (AccessBeacon
só dispara em SIGNED_IN). A janela de dedupe aqui existe só para colapsar o
disparo duplo da MESMA autenticação — a tela de login chama o beacon e o evento
SIGNED_IN do Supabase dispara logo em seguida.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.user_login import UserLogin

JANELA_DEDUPE = timedelta(minutes=2)


def record_access(
    db: Session, user_id: int, ip: Optional[str] = None, user_agent: Optional[str] = None
) -> bool:
    """Grava uma autenticação. Retorna False quando colapsada pela janela.

    ip/user_agent são opcionais (não quebra chamadores existentes) — gravados
    quando disponíveis pra permitir investigar outliers de acesso (várias
    sessões reais vs. bug de sessão reautenticando), o que hoje é impossível
    porque essas colunas existem no modelo mas nunca são preenchidas.
    """
    agora = datetime.now(timezone.utc)
    recente = (
        db.query(UserLogin.id)
        .filter(
            UserLogin.user_id == user_id,
            UserLogin.logged_at >= agora - JANELA_DEDUPE,
        )
        .first()
    )
    if recente:
        return False
    db.add(UserLogin(user_id=user_id, logged_at=agora, ip=ip, user_agent=user_agent))
    db.commit()
    return True


# Nome antigo mantido para não quebrar import existente.
record_daily_access = record_access
