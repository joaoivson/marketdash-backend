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
import random
import time
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.custom_link import CustomLink
from app.models.whatsapp_grupos import WhatsappGrupo, WhatsappInstancia
from app.repositories.custom_link_repository import CustomLinkRepository
from app.repositories.whatsapp_grupo_repository import WhatsappGrupoRepository
from app.repositories.sync_run_repository import SyncRunRepository
from app.services.waha_client import (
    ErroWhatsapp, WahaClient, campo as _valor, numero_de_jid, tem_campo as _tem_campo,
)
from app.services.whatsapp_instancia_service import cliente_da_sessao

logger = logging.getLogger(__name__)

_ALFABETO_36 = "0123456789abcdefghijklmnopqrstuvwxyz"
PAGINA = 100
# Orçamento da fase de convites (uma chamada HTTP por grupo). Ver _preencher_convites.
ORCAMENTO_CONVITES_S = 8.0


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


def _identidades(dados: Any) -> set:
    """
    Todos os identificadores de um participante, reduzidos à parte do usuário.

    Em grupo com endereçamento **LID** o `JID` do participante é `…@lid` e o
    telefone vem em `PhoneNumber` — comparar só um dos dois faz o nosso próprio
    número não se reconhecer, e o grupo inteiro nasce como "não sou admin".
    """
    saida = set()
    for chave in ("JID", "id", "PhoneNumber", "LID", "jid"):
        bruto = _valor(dados, chave)
        if isinstance(bruto, dict):
            bruto = bruto.get("_serialized") or bruto.get("user")
        texto = numero_de_jid(bruto)
        if texto:
            saida.add(texto)
    return saida


def jid_do_grupo(dados: Dict[str, Any]) -> Optional[str]:
    """
    JID do grupo, aceitando as formas que os engines do WAHA usam.

    Três formas já vistas em produção:

    - `id` string  (`"1203…@g.us"`)            — NOWEB/WEBJS
    - `id` objeto  (`{"_serialized": …}`)      — WEBJS
    - **`JID` string, PascalCase**             — GOWS (structs do whatsmeow)

    A versão anterior lia só `dados.get("id")`. Contra o GOWS isso descartava
    **todo** grupo em silêncio: em 26/08 o sync trouxe 499 grupos em 5 páginas e
    gravou zero, terminando "com sucesso". `types.JID` do whatsmeow implementa
    `MarshalText`, então chega como string simples — não como objeto.
    """
    bruto = _valor(dados, "JID", "id", "gid", "chatId")
    if isinstance(bruto, str):
        jid = bruto
    elif isinstance(bruto, dict):
        jid = str(bruto.get("_serialized") or "")
        if not jid and bruto.get("user"):
            jid = f"{bruto['user']}@{bruto.get('server') or 'g.us'}"
    else:
        jid = ""
    jid = jid.strip()
    return jid if jid.endswith("@g.us") else None


def _extrair_agregados(dados: Dict[str, Any], meus_ids: set) -> Dict[str, Any]:
    """Conta participantes e descobre o papel do nosso número — em memória."""
    participantes = _valor(dados, "participants") or []
    total = len(participantes) if participantes else int(_valor(dados, "size") or 0)

    sou_admin = False
    if meus_ids and participantes:
        for p in participantes:
            if not _identidades(p) & meus_ids:
                continue
            # GOWS: dois booleanos. NOWEB/WEBJS: um papel em texto.
            sou_admin = bool(_valor(p, "IsAdmin")) or bool(_valor(p, "IsSuperAdmin")) or (
                str(_valor(p, "role") or "") in ("admin", "superadmin")
            )
            break

    # "Só admins podem enviar" desligado = grupo aberto. Ler a config real do
    # grupo, não assumir (a spec corrige o Orbit exatamente nisso). Payload SEM
    # nenhum dos flags → conservador: só admin garante envio; marcar "aberto"
    # no chute mandaria o lote contra grupos que devolvem 403.
    if not _tem_campo(dados, "announce", "isAnnounce", "membersCanSendMessages"):
        permite = sou_admin
    else:
        anuncio_so_admin = bool(
            _valor(dados, "announce", "isAnnounce")
            or (_valor(dados, "membersCanSendMessages") is False)
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
        self._falha_convite = ""

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
        detalhes = {k: resultado[k] for k in ("novos", "atualizados", "desativados",
                                              "ignorados", "convites")}
        if self._falha_convite:
            # Fica no run para ser auditável em /admin/sincronizacoes: "169
            # grupos de admin e zero convites" precisa de motivo, não de log.
            detalhes["convites_falha"] = self._falha_convite[:200]
        sync_repo.mark_success(run, records_fetched=resultado["vistos"],
                               records_upserted=resultado["novos"] + resultado["atualizados"],
                               details=detalhes)
        return resultado

    def _sincronizar(self, instancia: WhatsappInstancia, cliente: WahaClient) -> Dict[str, int]:
        # Quem somos nós, em TODAS as formas que podem aparecer na lista de
        # participantes. Em grupo com endereçamento LID o participante vem como
        # `…@lid`; comparar só o telefone faz o número não se achar e todo grupo
        # nasce "não sou admin" — o que trava envio e convite.
        eu = (cliente.sessao_info().get("me") or {})
        meus_ids = {numero_de_jid(x) for x in
                    (instancia.numero, eu.get("id"), eu.get("lid"), eu.get("jid"))}
        meus_ids.discard("")

        # Preloads: 2 queries no lugar de 2-por-grupo (uma afiliada de achadinho
        # tem centenas de grupos; o N+1 dobrava a duração do sync).
        grupos_por_jid = self.repo.por_jids(instancia.user_id)
        vinculos = self.repo.vinculos_da_instancia(instancia.id)

        vistos = novos = atualizados = ignorados = 0
        grupo_ids_vistos = []
        precisam_convite = []
        offset = 0
        while True:
            pagina = cliente.listar_grupos(limite=PAGINA, offset=offset)
            ignorados_na_pagina = 0
            for dados in pagina:
                jid = jid_do_grupo(dados)
                if not jid:
                    ignorados_na_pagina += 1
                    ignorados += 1
                    continue
                vistos += 1
                agregados = _extrair_agregados(dados, meus_ids)
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
                    # Fora do laço: é UMA chamada HTTP por grupo.
                    precisam_convite.append(grupo)
                self.repo.vincular_instancia(
                    grupo.id, instancia.id, agregados["sou_admin"],
                    vinculo=vinculos.get(grupo.id),
                )
                grupo_ids_vistos.append(grupo.id)
            if ignorados_na_pagina:
                # Página com itens e nenhum reconhecido como grupo é sintoma de
                # formato novo, não de conta sem grupos. Sem este log, o sync
                # termina "com sucesso" e ninguém descobre por quê.
                logger.warning(
                    "Sync de grupos: %s de %s itens sem JID reconhecível "
                    "(chaves do 1º: %s)",
                    ignorados_na_pagina, len(pagina),
                    sorted(pagina[0].keys())[:8] if pagina else [],
                )
            if len(pagina) < PAGINA:
                break
            offset += PAGINA

        # Veio conteúdo e não reconhecemos NADA: isso é contrato quebrado do
        # engine, não conta sem grupos. Falhar alto aqui é a diferença entre um
        # 502 com motivo e o que aconteceu em 26/08 — cinco páginas, 499 grupos,
        # sync "com sucesso", zero gravado, e nenhum sinal na tela.
        if ignorados and not vistos:
            raise ErroWhatsapp(
                "formato",
                f"{ignorados} itens sem JID reconhecível — o engine mudou o formato",
            )

        # Some desta sessão → desfaz o vínculo; sem vínculo com NENHUMA sessão
        # → ativo=False (nunca DELETE). Se reaparecer num sync futuro, revive.
        self.repo.desvincular_instancia(instancia.id, grupo_ids_vistos)
        self.db.flush()
        desativados = self.repo.desativar_sem_vinculo(instancia.user_id)

        self.db.commit()

        # A partir daqui é enriquecimento: os grupos já estão gravados, então
        # uma queda no meio não perde mais nada.
        convites = self._preencher_convites(cliente, precisam_convite)

        return {"vistos": vistos, "novos": novos, "atualizados": atualizados,
                "desativados": desativados, "ignorados": ignorados,
                "convites": convites}

    def _preencher_convites(self, cliente: WahaClient, grupos: list) -> int:
        """
        Link de convite dos grupos onde somos admin — UMA chamada HTTP cada.

        Com 499 grupos (o caso real de 26/08) buscar tudo dentro do request leva
        minutos, o proxy corta a conexão e a afiliada vê "Failed to fetch" sem
        nada gravado. Orçamento de tempo em vez de teto fixo: quem tem poucos
        grupos resolve na primeira vez, quem tem centenas converge em alguns
        syncs — e o commit dos grupos já aconteceu, então o corte é inofensivo.
        """
        if not grupos:
            return 0
        self._falha_convite = ""
        # Ordem embaralhada de propósito. A lista chega sempre na mesma ordem
        # (`sortBy=id`), e alguns grupos falham SEMPRE — o WAHA devolve 500 em
        # parte deles. Sem embaralhar, esses mesmos grupos consomem o orçamento
        # em toda rodada e os que funcionariam nunca chegam a ser tentados.
        grupos = list(grupos)
        random.shuffle(grupos)
        limite = time.monotonic() + ORCAMENTO_CONVITES_S
        preenchidos = 0
        for grupo in grupos:
            if time.monotonic() >= limite:
                logger.info(
                    "Convites: %s de %s dentro do orçamento; o resto vai no próximo sync",
                    preenchidos, len(grupos),
                )
                break
            link = self._convite(cliente, grupo.jid)
            if link:
                grupo.link_convite = link
                preenchidos += 1
        if self._falha_convite:
            # Uma linha, não 169: o motivo da PRIMEIRA falha basta para o
            # diagnóstico e não afoga o log.
            logger.warning("Convites: %s de %s resolvidos; primeira falha — %s",
                           preenchidos, len(grupos), self._falha_convite)
        if preenchidos:
            self.db.commit()
        return preenchidos

    def _criar_grupo(self, user_id: int, jid: str, dados: Dict[str, Any],
                     agregados: Dict[str, Any]) -> WhatsappGrupo:
        grupo = self.repo.adicionar(WhatsappGrupo(
            user_id=user_id,
            jid=jid,
            nome=(_valor(dados, "subject", "name") or "")[:255] or None,
            foto_url=_valor(dados, "picture", "pictureUrl"),
            ativo=True,
            **agregados,
        ))  # flush garante grupo.id para o sub_id determinístico
        grupo.sub_id = sub_id_do_grupo(grupo.id)
        grupo.custom_link_id = self._criar_custom_link(user_id, grupo).id
        return grupo

    def _atualizar_grupo(self, grupo: WhatsappGrupo, dados: Dict[str, Any],
                         agregados: Dict[str, Any]) -> None:
        grupo.nome = (_valor(dados, "subject", "name") or grupo.nome or "")[:255] or None
        foto = _valor(dados, "picture", "pictureUrl")
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
        """Convite é sempre best-effort: falha aqui nunca derruba o sync."""
        try:
            return cliente.convite_do_grupo(jid)
        except ErroWhatsapp as e:
            if not self._falha_convite:
                self._falha_convite = f"{e.motivo}: {e.detalhe}"
            return None

