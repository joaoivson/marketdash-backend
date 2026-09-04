"""
Link de entrada (F6): roteia a pessoa para um grupo com vaga e registra o
clique — a primeira metade da corrente que o módulo inteiro existe para medir.

Decisões que moram aqui:

* **Uma transação só** para escolher o grupo e gravar o clique. A escolha usa
  `FOR UPDATE SKIP LOCKED` no vínculo: dois cliques simultâneos nunca recebem
  o mesmo "último lugar" do grupo que está lotando.
* **Lotado sai da rotação** pelo próprio WHERE (`participantes < capacidade`);
  com `abertura_automatica`, o próximo da fila é aberto na hora.
* **`/g/preview/{slug}`** roteia igual mas grava `is_teste=true` e não entra
  em métrica nenhuma — resolve a afiliada testar o próprio link sem sujar o
  número que ela mostra para o gestor de tráfego.
* Dedup de 60s e detecção de bot vêm do mesmo lugar do `/l/{slug}`: um
  crawler de rede social não pode contar como pessoa.
"""
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.campanha_grupos import (
    CAMPANHA_ARQUIVADA, CAMPANHA_ENCERRADA, ESTRATEGIA_ALEATORIA, Campanha,
)
from app.models.campanha_link import CampanhaLink
from app.repositories.campanha_link_repository import CampanhaLinkRepository
from app.utils.bot_detection import is_bot
from app.utils.tracking_dedup import should_count

logger = logging.getLogger(__name__)

DEDUP_SEGUNDOS = 60


class LinkInvalido(Exception):
    pass


class SemVaga(Exception):
    """Todos os grupos lotados/fechados — a página diz "vagas esgotadas"."""


class CampanhaEncerrada(Exception):
    """A campanha foi excluída — a página diz isso, com 200.

    NÃO é `LinkInvalido`. O anúncio que aponta para este link continua
    veiculando por dias depois da exclusão, e devolver 404 para esse tráfego
    é o pior desfecho: o Meta passa a tratar o destino como quebrado e a
    pessoa vê uma tela de erro em vez de uma explicação.
    """


def _hash_ip(ip: Optional[str], user_agent: Optional[str]) -> Optional[str]:
    if not ip:
        return None
    return hashlib.sha256(f"{ip}|{user_agent or ''}".encode()).hexdigest()[:64]


class CampanhaLinkService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = CampanhaLinkRepository(db)

    # --- CRUD ---------------------------------------------------------------

    def obter_ou_criar(self, campanha: Campanha) -> CampanhaLink:
        link = self.repo.por_campanha(campanha.id)
        if link:
            return link
        slug = uuid.uuid4().hex[:8]
        while self.repo.por_slug(slug):
            slug = uuid.uuid4().hex[:8]
        link = self.repo.adicionar(CampanhaLink(campanha_id=campanha.id, slug=slug))
        self.db.commit()
        return link

    def atualizar(self, link: CampanhaLink, mudancas: Dict) -> CampanhaLink:
        for campo, limite in (("titulo_previa", 160), ("descricao_previa", 300)):
            if campo in mudancas:
                valor = (str(mudancas[campo] or "").strip())[:limite]
                setattr(link, campo, valor or None)
        if "banner_previa_url" in mudancas:
            link.banner_previa_url = (str(mudancas["banner_previa_url"] or "").strip()) or None
        if "pixel_facebook_id" in mudancas:
            pixel = (str(mudancas["pixel_facebook_id"] or "").strip())[:32]
            link.pixel_facebook_id = pixel or None
        # `pixel_eventos` deixou de ser configurável (04/09): PageView e Lead
        # disparam sempre. Ignorar o campo aqui — em vez de só tirar os toggles
        # da tela — impede que um cliente antigo continue gravando `false` numa
        # coluna que ninguém mais lê.
        if isinstance(mudancas.get("ativo"), bool):
            link.ativo = mudancas["ativo"]
        link.atualizado_em = datetime.now(timezone.utc)
        self.db.add(link)
        self.db.commit()
        return link

    # --- roteamento ---------------------------------------------------------

    def rotear(self, slug: str, ip: Optional[str], user_agent: Optional[str],
               referer: Optional[str], is_preview: bool = False
               ) -> Tuple[CampanhaLink, str]:
        """Devolve (link, url_de_convite). Levanta SemVaga / LinkInvalido."""
        link = self.repo.por_slug(slug)
        if not link or not link.ativo:
            raise LinkInvalido("Link não encontrado.")
        campanha = self.repo.campanha_do_link(link)
        if campanha is not None and campanha.status == CAMPANHA_ENCERRADA:
            raise CampanhaEncerrada()
        if not campanha or campanha.status == CAMPANHA_ARQUIVADA:
            raise LinkInvalido("Campanha indisponível.")

        aleatorio = campanha.estrategia_entrada == ESTRATEGIA_ALEATORIA
        escolha = self.repo.escolher_grupo(campanha.id, aleatorio)

        if escolha is None and campanha.abertura_automatica:
            # O grupo da vez lotou: abre o próximo da fila e tenta de novo.
            proximo = self.repo.proximo_fechado(campanha.id)
            if proximo:
                proximo.aberto = True
                self.db.add(proximo)
                self.db.flush()
                logger.info("Campanha %s: grupo %s aberto automaticamente",
                            campanha.id, proximo.grupo_id)
                escolha = self.repo.escolher_grupo(campanha.id, aleatorio)

        if escolha is None:
            # PRENDE os lotados como cheios — não fecha o `aberto` deles.
            #
            # `aberto` voltou a ser só a decisão da usuária (080); quem tira da
            # rotação é `cheio`. Escrever `aberto=False` aqui desfazia a escolha
            # dela por baixo, e como "cheio" só existia derivado, o grupo com
            # 946/900 aparecia "Aberto" na tela para sempre.
            #
            # O pin só acontece com `reabertura_automatica` DESLIGADA: com ela
            # ligada, a lotação cair abaixo do teto já devolve o grupo à
            # rotação sozinha, que é justamente o que a opção promete.
            if not campanha.reabertura_automatica:
                for vinculo in self.repo.lotados_abertos(campanha.id):
                    vinculo.cheio_override = True
                    self.db.add(vinculo)
            self.db.commit()
            raise SemVaga()

        grupo_id, _posicao = escolha
        grupo = self.repo.grupo(grupo_id)

        contar = not is_preview and not is_bot(user_agent or "")
        if contar:
            # Dedup 60s (Redis, fail-open): recarregar a página não vira duas
            # pessoas. Fail-open porque contar a mais é melhor que perder o
            # roteamento por causa do cache.
            contar = should_count("gclk", link.id, ip or "", user_agent or "",
                                  DEDUP_SEGUNDOS)
        if contar or is_preview:
            self.repo.registrar_clique(
                link.id, grupo_id, _hash_ip(ip, user_agent), user_agent, referer,
                is_teste=is_preview,
            )
        self.db.commit()
        return link, grupo.link_convite

    # --- prévia (OG tags) ---------------------------------------------------

    def dados_da_previa(self, link: CampanhaLink) -> Dict[str, Optional[str]]:
        """Título/descrição/imagem da prévia. Sem personalização, cai no nome
        e na foto do primeiro grupo aberto — a prévia nunca fica vazia."""
        campanha = self.repo.campanha_do_link(link)
        titulo = link.titulo_previa
        descricao = link.descricao_previa
        imagem = link.banner_previa_url
        if not (titulo and imagem):
            escolha = self.repo.escolher_grupo(campanha.id, aleatorio=False)
            if escolha:
                grupo = self.repo.grupo(escolha[0])
                titulo = titulo or (grupo.nome if grupo else None)
                imagem = imagem or (grupo.foto_url if grupo else None)
        return {
            "titulo": titulo or (campanha.nome if campanha else "Entrar no grupo"),
            "descricao": descricao or "Toque para entrar no grupo do WhatsApp.",
            "imagem": imagem,
        }
