"""
Métricas da aba "Uso da plataforma" do painel admin.

Mede adoção real: quantas autenticações, quantas pessoas distintas, com que
frequência e em quais telas. Tudo governado por um único filtro de período.

As regras de exclusão valem para TODOS os números daqui — se cada bloco filtrar
do seu jeito, os cards param de fechar com a tabela.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Date, cast, func
from sqlalchemy.orm import Session

from app.models.page_view import PageView
from app.models.user import User
from app.models.user_login import UserLogin

PERIODOS_VALIDOS = {"hoje": 1, "7d": 7, "30d": 30, "90d": 90}
DIAS_SEM_ACESSO = 10

# Rotas que não são uso do produto por aluna logada.
_PREFIXOS_EXCLUIDOS = ("/admin", "/login", "/l/", "/c/")
_EXATOS_EXCLUIDOS = ("/", "", "/login", "/admin")

# Rota → nome amigável. Prefixo mais longo primeiro (o match é por prefixo).
NOMES_DE_TELA = [
    ("/dashboard/upload-cliques", "Upload Cliques"),
    ("/dashboard/upload", "Upload"),
    ("/dashboard/investimentos", "Investimentos"),
    ("/dashboard/campanhas", "Campanhas"),
    ("/dashboard/captura-site", "Página de Captura"),
    ("/dashboard/meus-links", "Meus Links"),
    ("/dashboard/configuracoes", "Configurações"),
    ("/dashboard/assinatura", "Assinatura"),
    ("/dashboard/afiliados", "Afiliados"),
    ("/dashboard/relatorios", "Relatórios"),
    ("/dashboard", "Dashboard"),
]


def rota_excluida(path: Optional[str]) -> bool:
    """True quando a rota não conta como uso do produto."""
    if not path:
        return True
    p = path.strip().lower()
    if p in _EXATOS_EXCLUIDOS:
        return True
    return any(p.startswith(prefixo) for prefixo in _PREFIXOS_EXCLUIDOS)


def nome_da_tela(path: Optional[str]) -> str:
    """Nome amigável da rota; cai no próprio path quando desconhecida."""
    if not path:
        return "—"
    p = path.strip().rstrip("/").lower() or "/"
    for prefixo, nome in NOMES_DE_TELA:
        if p == prefixo:
            return nome
        # "/dashboard" sozinho não pode capturar subrota desconhecida:
        # /dashboard/coisa-nova é uma tela nova, não a Dashboard.
        if prefixo != "/dashboard" and p.startswith(prefixo + "/"):
            return nome
    limpo = re.sub(r"^/dashboard/", "", p).strip("/")
    if not limpo:
        return "Dashboard"
    return limpo.replace("-", " ").replace("/", " · ").title()


def capitalizar_nome(nome: Optional[str]) -> Optional[str]:
    """Title Case preservando partículas — 'rubiane de melo' → 'Rubiane de Melo'."""
    if not nome:
        return nome
    particulas = {"de", "da", "do", "das", "dos", "e", "di", "du", "van", "von", "la"}
    partes = [p for p in re.split(r"\s+", nome.strip()) if p]
    saida = []
    for i, parte in enumerate(partes):
        baixo = parte.lower()
        if i > 0 and baixo in particulas:
            saida.append(baixo)
        elif "'" in parte:
            saida.append("'".join(s.capitalize() for s in parte.split("'")))
        else:
            saida.append(baixo.capitalize())
    return " ".join(saida)


class PlatformUsageService:
    def __init__(self, db: Session):
        self.db = db

    # -- base ---------------------------------------------------------------

    def _inicio(self, periodo: str) -> datetime:
        dias = PERIODOS_VALIDOS.get(periodo, 7)
        agora = datetime.now(timezone.utc)
        if periodo == "hoje":
            return agora.replace(hour=0, minute=0, second=0, microsecond=0)
        return agora - timedelta(days=dias)

    def _ids_admin(self) -> List[int]:
        """Contas admin não contam — senão a taxa de uso nasce inflada por nós."""
        return [
            uid
            for (uid,) in self.db.query(User.id).filter(
                (User.is_admin.is_(True)) | (User.is_demo.is_(True))
            )
        ]

    def _logins_do_periodo(self, periodo: str):
        q = self.db.query(UserLogin).filter(UserLogin.logged_at >= self._inicio(periodo))
        excluidos = self._ids_admin()
        if excluidos:
            q = q.filter(~UserLogin.user_id.in_(excluidos))
        return q

    # -- blocos -------------------------------------------------------------

    def cards(self, periodo: str) -> Dict[str, Any]:
        logins = self._logins_do_periodo(periodo)
        acessos = logins.count()
        usuarias_ativas = logins.with_entities(
            func.count(func.distinct(UserLogin.user_id))
        ).scalar() or 0

        base_ativa = self._base_ativa()
        corte = datetime.now(timezone.utc) - timedelta(days=DIAS_SEM_ACESSO)
        ultimo_por_usuario = dict(
            self.db.query(UserLogin.user_id, func.max(UserLogin.logged_at))
            .group_by(UserLogin.user_id)
            .all()
        )
        sem_acesso = 0
        for uid in base_ativa:
            ultimo = ultimo_por_usuario.get(uid)
            if ultimo is None:
                sem_acesso += 1
            else:
                if ultimo.tzinfo is None:
                    ultimo = ultimo.replace(tzinfo=timezone.utc)
                if ultimo < corte:
                    sem_acesso += 1

        return {
            "acessos": acessos,
            "usuarias_ativas": usuarias_ativas,
            "base_ativa": len(base_ativa),
            "taxa_uso": (
                round(usuarias_ativas / len(base_ativa), 4) if base_ativa else None
            ),
            "sem_acesso_10d": sem_acesso,
            "dias_sem_acesso": DIAS_SEM_ACESSO,
        }

    def _base_ativa(self) -> List[int]:
        """Clientes com assinatura ativa (exclui admin/demo)."""
        from app.models.subscription import Subscription

        excluidos = set(self._ids_admin())
        return [
            uid
            for (uid,) in self.db.query(Subscription.user_id).filter(
                Subscription.is_active.is_(True)
            )
            if uid not in excluidos
        ]

    def usuarias_por_dia(self, periodo: str) -> List[Dict[str, Any]]:
        """Pessoas DISTINTAS por dia — hits inflam, distintas mostram adoção."""
        linhas = (
            self._logins_do_periodo(periodo)
            .with_entities(
                cast(UserLogin.logged_at, Date).label("d"),
                func.count(func.distinct(UserLogin.user_id)),
            )
            .group_by("d")
            .order_by("d")
            .all()
        )
        return [{"date": str(d), "usuarias": n} for d, n in linhas]

    def atividade_por_usuaria(self, periodo: str) -> List[Dict[str, Any]]:
        linhas = (
            self._logins_do_periodo(periodo)
            .with_entities(
                UserLogin.user_id,
                func.count().label("acessos"),
                func.count(func.distinct(cast(UserLogin.logged_at, Date))).label("dias"),
                func.max(UserLogin.logged_at).label("ultimo"),
            )
            .group_by(UserLogin.user_id)
            .order_by(func.count().desc())
            .all()
        )
        if not linhas:
            return []
        nomes = dict(
            self.db.query(User.id, User.name).filter(
                User.id.in_([l[0] for l in linhas])
            )
        )
        emails = dict(
            self.db.query(User.id, User.email).filter(
                User.id.in_([l[0] for l in linhas])
            )
        )
        return [
            {
                "user_id": uid,
                "nome": capitalizar_nome(nomes.get(uid)) or emails.get(uid) or f"#{uid}",
                "email": emails.get(uid),
                "acessos": acessos,
                "dias_ativos": dias,
                "ultimo_acesso": ultimo.isoformat() if ultimo else None,
            }
            for uid, acessos, dias, ultimo in linhas
        ]

    def telas_mais_acessadas(self, periodo: str, limite: int = 8) -> List[Dict[str, Any]]:
        excluidos = self._ids_admin()
        q = self.db.query(PageView.path, func.count().label("c")).filter(
            PageView.viewed_at >= self._inicio(periodo)
        )
        if excluidos:
            q = q.filter(
                (PageView.user_id.is_(None)) | (~PageView.user_id.in_(excluidos))
            )
        # agrupa por nome amigável: /dashboard/campanhas e /dashboard/campanhas/12
        # são a mesma tela pro leitor
        agregado: Dict[str, int] = {}
        for path, c in q.group_by(PageView.path).all():
            if rota_excluida(path):
                continue
            agregado[nome_da_tela(path)] = agregado.get(nome_da_tela(path), 0) + c

        total = sum(agregado.values())
        ordenado = sorted(agregado.items(), key=lambda kv: kv[1], reverse=True)[:limite]
        return [
            {
                "tela": nome,
                "acessos": c,
                "proporcao": round(c / total, 4) if total else 0,
            }
            for nome, c in ordenado
        ]

    def resumo(self, periodo: str = "7d") -> Dict[str, Any]:
        if periodo not in PERIODOS_VALIDOS:
            periodo = "7d"
        return {
            "periodo": periodo,
            "cards": self.cards(periodo),
            "usuarias_por_dia": self.usuarias_por_dia(periodo),
            "atividade": self.atividade_por_usuaria(periodo),
            "telas": self.telas_mais_acessadas(periodo),
        }
