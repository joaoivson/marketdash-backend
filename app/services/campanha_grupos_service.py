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
from app.repositories.campanha_numero_repository import CampanhaNumeroRepository
from app.repositories.whatsapp_grupo_repository import WhatsappGrupoRepository
from app.repositories.whatsapp_instancia_repository import WhatsappInstanciaRepository

logger = logging.getLogger(__name__)

STATUS_VALIDOS = {CAMPANHA_ATIVA, CAMPANHA_PAUSADA, CAMPANHA_ARQUIVADA}
ESTRATEGIAS_VALIDAS = {ESTRATEGIA_SEQUENCIAL, ESTRATEGIA_ALEATORIA}
MODOS_DE_IMAGEM_VALIDOS = {MODO_IMAGEM_LINK, MODO_IMAGEM_NORMAL}


class LimiteDeCampanhas(Exception):
    pass


class GrupoInvalido(Exception):
    """Grupo inexistente ou de outra usuária."""


class NumeroInvalido(Exception):
    """Número inexistente ou de outra usuária."""


class NumeroEmUso(Exception):
    """Número que ainda tem grupos na campanha (spec §2.4).

    Carrega os grupos que dependem dele: bloquear sem dizer o que trava a ação
    deixa a afiliada sem o próximo passo.
    """

    def __init__(self, grupos_por_numero: Dict[str, List[str]]):
        self.grupos_por_numero = grupos_por_numero
        super().__init__("Números com grupos vinculados na campanha.")


class GrupoForaDosNumeros(Exception):
    """Grupo que não pertence a nenhum número selecionado (spec §2.3).

    É o bug que a aba Números existe para matar: grupo do número A numa
    campanha que dispara pelo B faz o envio falhar em silêncio.
    """

    def __init__(self, nomes: List[str]):
        self.nomes = nomes
        super().__init__("Grupos fora dos números da campanha.")


class CampanhaGruposService:
    def __init__(self, db: Session, plan_limit_campanhas: int = -1):
        self.db = db
        self.repo = CampanhaGruposRepository(db)
        self.repo_grupos = WhatsappGrupoRepository(db)
        self.repo_numeros = CampanhaNumeroRepository(db)
        self.repo_instancias = WhatsappInstanciaRepository(db)
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
        if "limite_participantes" in mudancas:
            # Vazio volta a "sem limite próprio" (vale a capacidade do grupo).
            # O Pydantic já valida a faixa; aqui só normaliza o apagar.
            bruto = mudancas["limite_participantes"]
            campanha.limite_participantes = int(bruto) if bruto else None
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

        # Escopo pelos números da campanha (spec §2.3). A regra precisa viver
        # AQUI e não só na tela: sem isto o PUT continua aceitando o vínculo que
        # quebra o envio — que é exatamente o bug que a aba Números resolve.
        # Campanha sem número escolhido não restringe nada (ainda não configurou).
        selecionados = set(self.repo_numeros.instancia_ids(campanha.id))
        if selecionados and itens:
            das_instancias = self.repo_grupos.instancias_por_grupo(campanha.user_id)
            fora = [
                gid for gid, _, _ in itens
                if not (set(das_instancias.get(gid, ())) & selecionados)
            ]
            if fora:
                nomes = {g.id: g.nome for g in self.repo_grupos.por_usuario(
                    campanha.user_id, apenas_ativos=False)}
                raise GrupoForaDosNumeros(
                    [nomes.get(gid) or f"Grupo {gid}" for gid in fora]
                )

        self.repo.substituir_vinculos(campanha.id, itens)
        self.repo.marcar_tocada(campanha)
        self.db.commit()

    # --- números da campanha (F2, spec §2) ----------------------------------

    def numeros_da_campanha(self, campanha: Campanha) -> Tuple[List, set, Dict[int, int]]:
        """Instâncias da usuária + quais estão nesta campanha + grupos por número."""
        instancias = self.repo_instancias.por_usuario(campanha.user_id)
        selecionados = set(self.repo_numeros.instancia_ids(campanha.id))
        return instancias, selecionados, self.repo_numeros.contagem_de_grupos(campanha.id)

    def definir_numeros(self, campanha: Campanha, instancia_ids: List[int]) -> None:
        """Substitui o conjunto de números. Remover número com grupos é bloqueado."""
        desejados = list(dict.fromkeys(instancia_ids))
        minhas = {i.id: i for i in self.repo_instancias.por_usuario(campanha.user_id)}
        alheias = [iid for iid in desejados if iid not in minhas]
        if alheias:
            raise NumeroInvalido(f"Números inexistentes: {alheias}")

        # §2.4: bloquear em vez de reassociar. Reassociação automática de envios
        # pendentes é complexidade sem retorno neste momento — e silenciosa.
        #
        # O bloqueio é por ÓRFÃO, não por presença: o mesmo grupo pode ser
        # alcançado por dois chips, e desmarcar um deles enquanto o outro
        # permanece não deixa grupo nenhum sem número. Bloquear pela simples
        # presença tornava impossível tirar da campanha o chip que vai aquecer
        # sem antes esvaziar a campanha inteira.
        removidos = set(self.repo_numeros.instancia_ids(campanha.id)) - set(desejados)
        if removidos:
            por_instancia = self.repo_numeros.grupos_por_instancia(campanha.id)
            servidos = set()
            for iid in desejados:
                servidos.update(por_instancia.get(iid, {}))

            travando: Dict[str, List[str]] = {}
            for iid in removidos:
                orfaos = {
                    gid: nome
                    for gid, nome in por_instancia.get(iid, {}).items()
                    if gid not in servidos
                }
                if not orfaos:
                    continue
                # `.get`, não `[...]`: a instância pode ter sido REMOVIDA da
                # conta (soft delete) e continuar em `campanha_numeros` — o
                # `por_usuario` não a devolve, e indexar dava KeyError → 500.
                inst = minhas.get(iid)
                rotulo = (inst.nome_exibicao or inst.numero) if inst else None
                travando[rotulo or f"Número {iid}"] = sorted(orfaos.values())
            if travando:
                raise NumeroEmUso(travando)

        self.repo_numeros.definir(campanha.id, desejados)
        self.repo.marcar_tocada(campanha)
        self.db.commit()
