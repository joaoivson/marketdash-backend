"""
Toggle "Ativo" do grupo — a escolha da USUÁRIA (spec §6.2/6.3).

`ativado` é um eixo separado do `ativo` do sync (lifecycle automático que
revive em toda rodada) e o sync nunca escreve nele. Ativar é o PONTO DE
ATRIBUIÇÃO: `sub_id` e `custom_link` são garantidos na MESMA transação do
toggle — o link de entrada já pode ir para anúncio antes de existir campanha,
e todo clique nesse intervalo fica atribuído. Desativar é só a flag: nada é
apagado, o histórico de comissão por grupo permanece.
"""
import logging
import uuid

from sqlalchemy.orm import Session

from app.core.plans import is_unlimited
from app.models.custom_link import CustomLink
from app.models.whatsapp_grupos import WhatsappGrupo
from app.repositories.custom_link_repository import CustomLinkRepository
from app.repositories.whatsapp_grupo_repository import WhatsappGrupoRepository
from app.services.whatsapp_grupo_sync_service import sub_id_do_grupo

logger = logging.getLogger(__name__)


class LimiteDeGruposAtivados(Exception):
    """Plano sem espaço para mais um grupo ativado."""

    def __init__(self, limite: int):
        self.limite = limite
        super().__init__(f"Limite de {limite} grupos ativos do plano atingido")


def criar_custom_link_de_grupo(db: Session, user_id: int,
                               grupo: WhatsappGrupo) -> CustomLink:
    """
    Link rastreável do grupo — FORA do limite de links do plano e filtrado
    de Meus Links pela FK `whatsapp_grupos.custom_link_id` (nunca pela tag:
    tag é texto livre da usuária e colidiria). `original_url` é placeholder
    até o primeiro disparo congelar o short link da oferta (F3/F4).

    add + flush SEM commit: quem comanda a transação é a ativação.
    """
    repo_links = CustomLinkRepository(db)
    slug = uuid.uuid4().hex[:8]
    # Slug é único GLOBAL: colisão estouraria a constraint no meio da
    # transação. Duas tentativas cobrem o (raríssimo) azar.
    if repo_links.get_by_slug(slug):
        slug = f"{slug}-{uuid.uuid4().hex[:4]}"
    return repo_links.criar_link_de_grupo(
        user_id, nome=(grupo.nome or f"Grupo {grupo.sub_id}"), slug=slug,
    )


def garantir_atribuicao(db: Session, grupo: WhatsappGrupo) -> None:
    """Idempotente: sub_id (`wg`+base36 — NUNCA regenerar) e custom_link só
    são criados se não existirem. Grupo antigo, que nasceu no sync com os
    dois, passa ileso."""
    if not grupo.sub_id:
        grupo.sub_id = sub_id_do_grupo(grupo.id)
    if not grupo.custom_link_id:
        grupo.custom_link_id = criar_custom_link_de_grupo(db, grupo.user_id, grupo).id


class WhatsappGrupoService:
    def __init__(self, db: Session, plan_limit_grupos: int = -1):
        self.db = db
        self.repo = WhatsappGrupoRepository(db)
        self.plan_limit_grupos = plan_limit_grupos

    def por_id(self, user_id: int, grupo_id: int) -> WhatsappGrupo | None:
        return self.repo.por_id(user_id, grupo_id)

    def instancia_ids(self, grupo: WhatsappGrupo) -> list[int]:
        return self.repo.instancias_por_grupo(grupo.user_id).get(grupo.id, [])

    def definir_ativado(self, grupo: WhatsappGrupo, ativado: bool) -> WhatsappGrupo:
        """Aplica o toggle. Ao ativar: limite do plano + atribuição, numa
        transação só. Ao desativar: só a flag — NUNCA apaga nada."""
        if ativado:
            # Limite só quando o count vai crescer: religar um grupo já
            # ativado (ou repetir o PATCH) não pode tomar 403.
            if not grupo.ativado and not is_unlimited(self.plan_limit_grupos):
                if self.repo.total_ativados(grupo.user_id) >= self.plan_limit_grupos:
                    raise LimiteDeGruposAtivados(self.plan_limit_grupos)
            garantir_atribuicao(self.db, grupo)
            grupo.ativado = True
        else:
            grupo.ativado = False
        self.repo.marcar_tocado(grupo)
        self.db.commit()
        return grupo
