"""Resolução e alocação de servidor WAHA — o pool que substitui `settings.WAHA_URL`.

Duas responsabilidades, e a primeira é a quente:

**Resolver** (`endereco_da_sessao`) responde "com qual caixa eu falo para esta
sessão?". Ela roda no caminho de CADA mensagem enviada, então tem cache em
memória com TTL curto — sem ele seria uma query por mensagem. O cache é seguro
porque a alocação é DEFINITIVA: o estado do whatsmeow vive no Postgres do WAHA
que pareou a sessão, então o vínculo sessão↔servidor nunca muda depois de
criado. O TTL existe só para o caso de um admin editar `base_url`/`api_key`.

**Alocar** (`escolher`) responde "onde a próxima sessão nasce?". Mesma forma do
`proxy_pool_service`, com uma diferença importante de motivação: a afinidade
por usuária aqui é PREFERÊNCIA (debug mais simples, raio de incêndio menor),
não isolamento — quem isola vizinhança é o proxy, que dá o IP. Dividir servidor
com outra afiliada não contamina ninguém.

**Fallback é proposital.** Enquanto a 071 não tiver rodado o backfill, ou num
ambiente que ainda não cadastrou servidor nenhum, tudo cai em
`settings.WAHA_URL`/`WAHA_API_KEY` — exatamente o comportamento de antes. O
módulo não pode ser um degrau que quebra ambiente antigo.
"""
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.encryption import decrypt_value
from app.models.waha_servidores import SERVIDOR_OK, WahaServidor
from app.repositories.waha_servidor_repository import WahaServidorRepository

logger = logging.getLogger(__name__)

# Endereço = (base_url, api_key). A key vai decifrada — este é o único lugar
# que decifra, e o valor segue direto para o header da chamada ao WAHA.
Endereco = Tuple[Optional[str], Optional[str]]

# Cache POR PROCESSO (o worker roda em prefork; cada processo tem o seu).
# TTL curto: o vínculo sessão↔servidor é imutável, mas base_url/api_key podem
# ser editados no admin e não queremos exigir redeploy para isso valer.
_TTL = timedelta(seconds=60)
_cache: Dict[str, Tuple[Endereco, datetime]] = {}
_lock = threading.Lock()


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _padrao() -> Endereco:
    """O servidor único de antes do pool."""
    return settings.WAHA_URL, settings.WAHA_API_KEY


def api_key(servidor: WahaServidor) -> Optional[str]:
    """Único ponto que decifra. Nunca logar o retorno: a key é a chave de
    TODOS os números daquele servidor, não de um."""
    if not servidor.api_key_cifrada:
        return None
    try:
        return decrypt_value(servidor.api_key_cifrada)
    except Exception:
        # Chave Fernet trocada ou valor corrompido. Sem a key não há chamada
        # possível — melhor falhar alto aqui do que mandar request sem auth.
        logger.exception("Falha ao decifrar a api_key do servidor %s", servidor.rotulo)
        return None


def limpar_cache() -> None:
    """Para os testes e para o admin depois de editar um servidor."""
    with _lock:
        _cache.clear()


def endereco_da_sessao(nome_instancia: str, db: Optional[Session] = None) -> Endereco:
    """(base_url, api_key) desta sessão, com cache. Cai no padrão quando a
    instância não tem servidor (sessão anterior ao pool) ou não existe."""
    if not nome_instancia:
        return _padrao()

    with _lock:
        achado = _cache.get(nome_instancia)
        if achado and achado[1] > _agora():
            return achado[0]

    endereco = _consultar(nome_instancia, db)
    with _lock:
        _cache[nome_instancia] = (endereco, _agora() + _TTL)
    return endereco


def _consultar(nome_instancia: str, db: Optional[Session]) -> Endereco:
    from app.models.whatsapp_grupos import WhatsappInstancia

    proprio = db is None
    if proprio:
        from app.db.session import SessionLocal
        db = SessionLocal()
    try:
        linha = (
            db.query(WahaServidor)
            .join(WhatsappInstancia, WhatsappInstancia.servidor_id == WahaServidor.id)
            .filter(WhatsappInstancia.nome_instancia == nome_instancia)
            .first()
        )
        if linha is None:
            return _padrao()
        chave = api_key(linha)
        if not chave:
            # Servidor cadastrado mas sem key utilizável: cair no padrão
            # mandaria a sessão para a caixa ERRADA, em silêncio. Melhor
            # devolver o base_url sem key e deixar o WAHA responder 401.
            return linha.base_url, None
        return linha.base_url, chave
    finally:
        if proprio:
            db.close()


def escolher(db: Session, user_id: int) -> Optional[WahaServidor]:
    """Servidor onde a PRÓXIMA sessão desta afiliada nasce.

    Ordem, parando no primeiro que servir:

      1. servidor que já hospeda outro chip da MESMA afiliada e tem vaga —
         mantém os 3 números juntos (debug e roteiro de shard morto);
      2. servidor disponível com vaga, o de MENOR ocupação (espalha carga);
      3. nenhum → None, e quem chama decide (o cap global levanta LimiteGlobal).

    Não grava: quem chama persiste `servidor_id` na MESMA transação da
    instância, senão vira vaga fantasma no pool.
    """
    repo = WahaServidorRepository(db)
    servidores = [s for s in repo.listar(ativos_apenas=True) if s.disponivel]
    if not servidores:
        return None

    ocupacao = repo.contagem_de_sessoes()
    usuarias = repo.usuarias_por_servidor()

    def tem_vaga(s: WahaServidor) -> bool:
        return ocupacao.get(s.id, 0) < (s.max_sessoes or 0)

    afins = [s for s in servidores
             if user_id in usuarias.get(s.id, set()) and tem_vaga(s)]
    if afins:
        escolhido = min(afins, key=lambda s: ocupacao.get(s.id, 0))
        logger.info("Servidor %s reusado por afinidade (user %s)", escolhido.rotulo, user_id)
        return escolhido

    livres = [s for s in servidores if tem_vaga(s)]
    if livres:
        escolhido = min(livres, key=lambda s: (ocupacao.get(s.id, 0), s.id))
        logger.info("Servidor %s alocado (user %s)", escolhido.rotulo, user_id)
        return escolhido

    logger.error("Pool de servidores WAHA sem vaga (user %s)", user_id)
    return None


def fixar(instancia, servidor: Optional[WahaServidor]) -> None:
    """Carimba o vínculo — sem commit, para nascer na transação da instância."""
    if servidor is None:
        return
    instancia.servidor_id = servidor.id


def capacidade_global(db: Session) -> int:
    """Teto real da plataforma: SUM(max_sessoes) dos servidores ativos, limitado
    por WHATSAPP_MAX_INSTANCIAS_GLOBAL como trava de segurança.

    Pool vazio (ambiente que ainda não cadastrou servidor) devolve o env puro —
    é o comportamento de antes, e evita que a migration sozinha zere a
    capacidade e trave a criação de números.
    """
    teto_env = settings.WHATSAPP_MAX_INSTANCIAS_GLOBAL
    soma = WahaServidorRepository(db).capacidade_total()
    if soma <= 0:
        return teto_env
    if soma > teto_env:
        # O pool cresceu e o env ficou para trás. Sem este aviso, "adicionei um
        # servidor e a capacidade não mudou" seria um mistério silencioso — o
        # INSERT teria sido feito e não teria efeito nenhum.
        logger.warning(
            "Pool WAHA soma %s sessões, mas WHATSAPP_MAX_INSTANCIAS_GLOBAL=%s "
            "está segurando o teto. Suba o env para o pool valer.",
            soma, teto_env,
        )
    return min(soma, teto_env)
