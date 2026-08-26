"""
Campanhas de grupos — CRUD e composição de grupos (F2).

Regras que moram aqui:
  * limite do plano (`campanhas_grupos`) vale para campanhas NÃO arquivadas;
  * todo grupo vinculado precisa pertencer à usuária (o vínculo cruza duas
    tabelas dela — um id alheio aqui seria vazamento);
  * arquivar em vez de deletar: a campanha carrega histórico de atribuição.
"""
import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.plans import is_unlimited
from app.models.campanha_grupos import (
    CAMPANHA_ARQUIVADA, CAMPANHA_ATIVA, CAMPANHA_PAUSADA,
    ESTRATEGIA_ALEATORIA, ESTRATEGIA_SEQUENCIAL,
    MODO_IMAGEM_LINK, MODO_IMAGEM_NORMAL, Campanha,
)
from app.repositories.campanha_grupos_repository import CampanhaGruposRepository
from app.repositories.whatsapp_grupo_repository import WhatsappGrupoRepository

logger = logging.getLogger(__name__)

STATUS_VALIDOS = {CAMPANHA_ATIVA, CAMPANHA_PAUSADA, CAMPANHA_ARQUIVADA}
ESTRATEGIAS_VALIDAS = {ESTRATEGIA_SEQUENCIAL, ESTRATEGIA_ALEATORIA}
MODOS_DE_IMAGEM_VALIDOS = {MODO_IMAGEM_LINK, MODO_IMAGEM_NORMAL}


class LimiteDeCampanhas(Exception):
    pass


class GrupoInvalido(Exception):
    """Grupo inexistente ou de outra usuária."""


class CampanhaGruposService:
    def __init__(self, db: Session, plan_limit_campanhas: int = -1):
        self.db = db
        self.repo = CampanhaGruposRepository(db)
        self.repo_grupos = WhatsappGrupoRepository(db)
        self.plan_limit_campanhas = plan_limit_campanhas

    # --- leitura ------------------------------------------------------------

    def listar(self, user_id: int, incluir_arquivadas: bool = False):
        campanhas = self.repo.por_usuario(user_id, incluir_arquivadas)
        return campanhas, self.repo.contagem_de_grupos(user_id)

    def obter(self, user_id: int, campanha_id: int) -> Optional[Campanha]:
        return self.repo.por_id(user_id, campanha_id)

    def total_de_grupos(self, campanha: Campanha) -> int:
        return len(self.repo.vinculos(campanha.id))

    def grupos_da_campanha(self, campanha: Campanha):
        """Vínculos ordenados por posição + os grupos correspondentes."""
        vinculos = self.repo.vinculos(campanha.id)
        grupos = {
            g.id: g
            for g in self.repo_grupos.por_usuario(campanha.user_id, apenas_ativos=False)
        }
        return [(v, grupos[v.grupo_id]) for v in vinculos if v.grupo_id in grupos]

    # --- escrita ------------------------------------------------------------

    def criar(self, user_id: int, nome: str, descricao: Optional[str] = None) -> Campanha:
        if not is_unlimited(self.plan_limit_campanhas):
            if self.plan_limit_campanhas <= 0:
                raise LimiteDeCampanhas(
                    "PLANO_INSUFICIENTE: Campanhas de grupos são exclusivas do plano Max"
                )
            if self.repo.total_ativas(user_id) >= self.plan_limit_campanhas:
                raise LimiteDeCampanhas(
                    f"Limite de {self.plan_limit_campanhas} campanhas atingido"
                )
        nome = (nome or "").strip()[:120]
        if not nome:
            # min_length=1 do Pydantic aceita "   " — o strip é daqui.
            raise ValueError("Informe um nome para a campanha.")
        campanha = self.repo.adicionar(Campanha(
            user_id=user_id,
            nome=nome,
            descricao=(descricao or "").strip() or None,
        ))
        self.db.commit()
        return campanha

    def atualizar(self, campanha: Campanha, mudancas: Dict) -> Campanha:
        if "nome" in mudancas and mudancas["nome"] is not None:
            campanha.nome = str(mudancas["nome"]).strip()[:120] or campanha.nome
        if "descricao" in mudancas:
            campanha.descricao = (str(mudancas["descricao"] or "").strip()) or None
        novo_status = mudancas.get("status")
        if novo_status in STATUS_VALIDOS:
            desarquivando = (
                campanha.status == CAMPANHA_ARQUIVADA and novo_status != CAMPANHA_ARQUIVADA
            )
            if desarquivando and not is_unlimited(self.plan_limit_campanhas):
                if self.repo.total_ativas(campanha.user_id) >= self.plan_limit_campanhas:
                    raise LimiteDeCampanhas(
                        f"Limite de {self.plan_limit_campanhas} campanhas atingido"
                    )
            campanha.status = novo_status
        if mudancas.get("estrategia_entrada") in ESTRATEGIAS_VALIDAS:
            campanha.estrategia_entrada = mudancas["estrategia_entrada"]
        if mudancas.get("modo_imagem") in MODOS_DE_IMAGEM_VALIDOS:
            campanha.modo_imagem = mudancas["modo_imagem"]
        for chave in ("abertura_automatica", "reabertura_automatica"):
            if isinstance(mudancas.get(chave), bool):
                setattr(campanha, chave, mudancas[chave])
        for chave in ("prefixo", "sufixo"):
            if chave in mudancas:
                setattr(campanha, chave, (str(mudancas[chave] or "").strip()) or None)
        self.repo.marcar_tocada(campanha)
        self.db.commit()
        return campanha

    def definir_grupos(self, campanha: Campanha,
                       itens: List[Tuple[int, int, bool]]) -> None:
        """itens = [(grupo_id, posicao, aberto)] — substitui o conjunto inteiro.

        A tela manda a lista completa na ordem final (arrastar = reenviar);
        substituição é mais simples e à prova de drift do que deltas.
        """
        # Dedup preservando a ÚLTIMA ocorrência: payload com grupo repetido
        # estouraria a PK composta no commit (500 no meio do salvar-ordem).
        por_grupo = {gid: (gid, pos, aberto) for gid, pos, aberto in itens}
        itens = list(por_grupo.values())

        meus_grupos = {
            g.id for g in self.repo_grupos.por_usuario(campanha.user_id, apenas_ativos=False)
        }
        estranhos = [gid for gid, _, _ in itens if gid not in meus_grupos]
        if estranhos:
            raise GrupoInvalido(f"Grupos inexistentes: {estranhos}")
        self.repo.substituir_vinculos(campanha.id, itens)
        self.repo.marcar_tocada(campanha)
        self.db.commit()
