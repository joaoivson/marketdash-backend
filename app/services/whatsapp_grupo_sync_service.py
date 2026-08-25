"""
Sync de grupos de uma sessão conectada — e o nascimento da atribuição.

Duas decisões da spec moram aqui e não podem regredir:

1. `sub_id` e `custom_link` nascem NO SYNC, não no primeiro envio. Todo clique
   e conversão entre o sync e o primeiro disparo seria atribuição perdida para
   sempre — o custo de criar já aqui é praticamente zero.
2. Grupo que some do retorno vira `ativo=False`, NUNCA é deletado: apagar a
   linha destruiria o histórico de comissão por grupo.

LGPD: a lista de participantes que o WAHA devolve é consumida em memória
(contagem + "sou admin?") e descartada. Nada de membro individual toca o banco.
"""
import logging
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.custom_link import CustomLink
from app.models.whatsapp_grupos import WhatsappGrupo, WhatsappInstancia
from app.repositories.custom_link_repository import CustomLinkRepository
from app.repositories.whatsapp_grupo_repository import WhatsappGrupoRepository
from app.repositories.sync_run_repository import SyncRunRepository
from app.services.waha_client import ErroWhatsapp, WahaClient, numero_de_jid
from app.services.whatsapp_instancia_service import cliente_da_sessao

logger = logging.getLogger(__name__)

_ALFABETO_36 = "0123456789abcdefghijklmnopqrstuvwxyz"
PAGINA = 100


def base36(n: int) -> str:
    if n == 0:
        return "0"
    saida = ""
    while n:
        n, resto = divmod(n, 36)
        saida = _ALFABETO_36[resto] + saida
    return saida


def sub_id_do_grupo(grupo_id: int) -> str:
    """wg{base36(id)} — satisfaz ^[A-Za-z0-9]+$ da Shopee, estável para sempre."""
    return f"wg{base36(grupo_id)}"


def _extrair_agregados(dados: Dict[str, Any], meu_numero: Optional[str]) -> Dict[str, Any]:
    """Conta participantes e descobre o papel do nosso número — em memória."""
    participantes = dados.get("participants") or []
    total = len(participantes) if participantes else int(dados.get("size") or 0)
    sou_admin = False
    if meu_numero and participantes:
        for p in participantes:
            pid = str((p or {}).get("id") or "")
            if pid.split("@")[0].split(":")[0] == meu_numero:
                sou_admin = str((p or {}).get("role") or "") in ("admin", "superadmin")
                break
    # "Só admins podem enviar" desligado = grupo aberto. Ler a config real do
    # grupo, não assumir (a spec corrige o Orbit exatamente nisso). Payload SEM
    # nenhum dos flags → conservador: só admin garante envio; marcar "aberto"
    # no chute mandaria o lote contra grupos que devolvem 403.
    flags = ("announce", "isAnnounce", "membersCanSendMessages")
    if not any(f in dados for f in flags):
        permite = sou_admin
    else:
        anuncio_so_admin = bool(
            dados.get("announce") or dados.get("isAnnounce")
            or (dados.get("membersCanSendMessages") is False)
        )
        permite = sou_admin or not anuncio_so_admin
    return {
        "participantes": total,
        "sou_admin": sou_admin,
        "permite_envio": permite,
    }


class WhatsappGrupoSyncService:
    def __init__(self, db: Session, cliente: Optional[WahaClient] = None):
        self.db = db
        self.repo = WhatsappGrupoRepository(db)
        self.cliente = cliente

    def listar(self, user_id: int, instancia_id=None, busca=None,
               incluir_inativos: bool = False):
        """Grupos + vínculos por grupo, prontos para a resposta da rota."""
        grupos = self.repo.por_usuario(
            user_id,
            apenas_ativos=not incluir_inativos,
            instancia_id=instancia_id,
            busca=busca,
        )
        vinculos = self.repo.instancias_por_grupo(user_id)
        return grupos, vinculos

    def sincronizar(self, instancia: WhatsappInstancia, trigger: str = "manual") -> Dict[str, int]:
        cliente = self.cliente or cliente_da_sessao(instancia.nome_instancia)

        sync_repo = SyncRunRepository(self.db)
        run = sync_repo.create(source="whatsapp_grupos", trigger=trigger,
                               user_id=instancia.user_id)
        try:
            resultado = self._sincronizar(instancia, cliente)
        except ErroWhatsapp as e:
            self.db.rollback()
            sync_repo.mark_failed(run, error_message=f"{e.motivo}: {e.detalhe}"[:500])
            raise
        except Exception as e:
            self.db.rollback()
            sync_repo.mark_failed(run, error_message=str(e)[:500])
            raise
        sync_repo.mark_success(run, records_fetched=resultado["vistos"],
                               records_upserted=resultado["novos"] + resultado["atualizados"])
        return resultado

    def _sincronizar(self, instancia: WhatsappInstancia, cliente: WahaClient) -> Dict[str, int]:
        meu_numero = instancia.numero or numero_de_jid(
            (cliente.sessao_info().get("me") or {}).get("id")
        ) or None

        # Preloads: 2 queries no lugar de 2-por-grupo (uma afiliada de achadinho
        # tem centenas de grupos; o N+1 dobrava a duração do sync).
        grupos_por_jid = self.repo.por_jids(instancia.user_id)
        vinculos = self.repo.vinculos_da_instancia(instancia.id)

        vistos = novos = atualizados = 0
        grupo_ids_vistos = []
        offset = 0
        while True:
            pagina = cliente.listar_grupos(limite=PAGINA, offset=offset)
            for dados in pagina:
                jid = str(dados.get("id") or "")
                if not jid.endswith("@g.us"):
                    continue
                vistos += 1
                agregados = _extrair_agregados(dados, meu_numero)
                grupo = grupos_por_jid.get(jid)
                if grupo is None:
                    grupo = self._criar_grupo(instancia.user_id, jid, dados, agregados)
                    grupos_por_jid[jid] = grupo
                    novos += 1
                else:
                    self._atualizar_grupo(grupo, dados, agregados)
                    atualizados += 1
                if agregados["sou_admin"] and not grupo.link_convite:
                    # Puxar o convite automático quando somos admin — o RealLead
                    # pede pra colar à mão; atrito desnecessário (spec §6.2).
                    grupo.link_convite = self._convite(cliente, jid)
                self.repo.vincular_instancia(
                    grupo.id, instancia.id, agregados["sou_admin"],
                    vinculo=vinculos.get(grupo.id),
                )
                grupo_ids_vistos.append(grupo.id)
            if len(pagina) < PAGINA:
                break
            offset += PAGINA

        # Some desta sessão → desfaz o vínculo; sem vínculo com NENHUMA sessão
        # → ativo=False (nunca DELETE). Se reaparecer num sync futuro, revive.
        self.repo.desvincular_instancia(instancia.id, grupo_ids_vistos)
        self.db.flush()
        desativados = self.repo.desativar_sem_vinculo(instancia.user_id)

        self.db.commit()
        return {"vistos": vistos, "novos": novos, "atualizados": atualizados,
                "desativados": desativados}

    def _criar_grupo(self, user_id: int, jid: str, dados: Dict[str, Any],
                     agregados: Dict[str, Any]) -> WhatsappGrupo:
        grupo = self.repo.adicionar(WhatsappGrupo(
            user_id=user_id,
            jid=jid,
            nome=(dados.get("subject") or dados.get("name") or "")[:255] or None,
            foto_url=dados.get("picture") or dados.get("pictureUrl"),
            ativo=True,
            **agregados,
        ))  # flush garante grupo.id para o sub_id determinístico
        grupo.sub_id = sub_id_do_grupo(grupo.id)
        grupo.custom_link_id = self._criar_custom_link(user_id, grupo).id
        return grupo

    def _atualizar_grupo(self, grupo: WhatsappGrupo, dados: Dict[str, Any],
                         agregados: Dict[str, Any]) -> None:
        grupo.nome = (dados.get("subject") or dados.get("name") or grupo.nome or "")[:255] or None
        foto = dados.get("picture") or dados.get("pictureUrl")
        if foto:
            grupo.foto_url = foto
        grupo.participantes = agregados["participantes"]
        grupo.sou_admin = agregados["sou_admin"]
        grupo.permite_envio = agregados["permite_envio"]
        grupo.ativo = True
        # Backfill de linha antiga que tenha nascido sem atribuição.
        if not grupo.sub_id:
            grupo.sub_id = sub_id_do_grupo(grupo.id)
        if not grupo.custom_link_id:
            grupo.custom_link_id = self._criar_custom_link(grupo.user_id, grupo).id
        self.repo.marcar_tocado(grupo)

    def _criar_custom_link(self, user_id: int, grupo: WhatsappGrupo) -> CustomLink:
        """
        Link rastreável do grupo — FORA do limite de links do plano e filtrado
        de Meus Links pela FK `whatsapp_grupos.custom_link_id` (nunca pela tag:
        tag é texto livre da usuária e colidiria). `original_url` é placeholder
        até o primeiro disparo congelar o short link da oferta (F3/F4).
        """
        repo_links = CustomLinkRepository(self.db)
        slug = uuid.uuid4().hex[:8]
        # Slug é único GLOBAL: colisão aqui estouraria a constraint no meio da
        # transação do sync. Duas tentativas cobrem o (raríssimo) azar.
        if repo_links.get_by_slug(slug):
            slug = f"{slug}-{uuid.uuid4().hex[:4]}"
        return repo_links.criar_link_de_grupo(
            user_id, nome=(grupo.nome or f"Grupo {grupo.sub_id}"), slug=slug,
        )

    def _convite(self, cliente: WahaClient, jid: str) -> Optional[str]:
        try:
            return cliente.convite_do_grupo(jid)
        except ErroWhatsapp:
            return None

