"""
Ciclo de vida das sessões WAHA das afiliadas: provisionar, parear (QR),
acompanhar estado e remover.

O nome da sessão é a identidade que amarra tudo: `mkd{ref4}u{user_id}x{hex4}`.
O prefixo do ambiente (ref do banco) impede que homologação e produção, no
mesmo servidor WAHA, derrubem a sessão uma da outra; o `user_id` no meio torna
o dono auditável a olho nu; o sufixo aleatório permite remover e recriar sem
colisão com uma sessão zumbi.
"""
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.ambiente import identidade_do_banco
from app.core.config import settings
from app.models.whatsapp_grupos import (
    INSTANCIA_CONECTADA, INSTANCIA_CRIADA, INSTANCIA_DESCONECTADA,
    INSTANCIA_REMOVIDA, WhatsappInstancia,
)
from app.services import proxy_pool_service
from app.services.waha_client import ErroWhatsapp, WahaClient, numero_de_jid

logger = logging.getLogger(__name__)

# Sessão de aluna nasce SÓ com o evento de estado — nenhum conteúdo de mensagem
# chega ao backend (anti webhook-storm + LGPD). `group.v2.participants` entra
# na F6; `message` só em sessão com monitoramento ativo (F8).
# `group.v2.participants` entra na F6: é a ÚNICA forma de saber quem entrou e
# quem saiu (diff de snapshot só dá contagem líquida e inviabiliza "entraram e
# ficaram"). Continua sem `message`: conteúdo de grupo não chega ao backend.
EVENTOS_DE_ALUNA = ["session.status", "group.v2.participants"]


EVENTO_DE_MONITORAMENTO = "message"


class EnvioEmAndamento(Exception):
    """Reconfigurar a sessão agora derrubaria um lote no meio."""


class LimiteDeNumeros(Exception):
    """A afiliada bateu no limite do plano."""


class LimiteGlobal(Exception):
    """A plataforma bateu no cap global de sessões (proteção de RAM do WAHA)."""


def prefixo_de_sessao() -> str:
    """Metade fixa da convenção de nome — gerador e roteador do webhook usam a MESMA."""
    return f"mkd{identidade_do_banco()[:4]}"


def pertence_a_este_ambiente(nome_sessao: str) -> bool:
    return (nome_sessao or "").startswith(prefixo_de_sessao())


def nome_de_instancia(user_id: int) -> str:
    return f"{prefixo_de_sessao()}u{user_id}x{secrets.token_hex(2)}"


def cliente_da_sessao(nome_instancia: str) -> WahaClient:
    return WahaClient(settings.WAHA_URL, settings.WAHA_API_KEY, nome_instancia)


def eventos_desejados(precisa_de_message: bool) -> List[str]:
    """Lista de eventos que a sessão DEVE ter assinado agora."""
    eventos = list(EVENTOS_DE_ALUNA)
    if precisa_de_message and EVENTO_DE_MONITORAMENTO not in eventos:
        eventos.append(EVENTO_DE_MONITORAMENTO)
    return eventos


def _precisa_reconfigurar(instancia, desejados: List[str]) -> Optional[bool]:
    """
    A sessão está com uma lista de eventos diferente da desejada?

    Três respostas, e a terceira é a que importa:
      True  — precisa reconfigurar;
      False — já está alinhada, ou a sessão nem existe no WAHA (sem sessão
              não há evento chegando, então desligar já está cumprido);
      None  — **não deu para saber** (WAHA fora do ar). Tratar isso como
              False fazia o desligar responder 200 com a sessão possivelmente
              ainda assinando `message`.
    """
    try:
        info = cliente_da_sessao(instancia.nome_instancia).sessao_info() or {}
    except ErroWhatsapp:
        # NÃO é "nada a fazer": é "não deu para saber". A diferença importa no
        # sentido DESLIGAR — responder sucesso aqui diria à afiliada que o
        # monitoramento parou quando a sessão pode seguir assinando `message`.
        logger.warning("Sessão %s inacessível: estado dos eventos desconhecido",
                       getattr(instancia, "nome_instancia", "?"))
        return None
    if not info:
        # `sessao_info()` devolve {} em 404: a sessão não existe no WAHA (foi
        # removida, ou o registro do banco ficou órfão). Não há webhook para
        # consertar — quem recria a sessão, já com os eventos certos, é o fluxo
        # de pareamento. Tentar o PUT aqui só produziria um erro sem saída.
        logger.info("Sessão %s não existe no WAHA: nada a reconfigurar",
                    instancia.nome_instancia)
        return False
    for wh in (((info.get("config") or {}).get("webhooks")) or []):
        return sorted(list(wh.get("events") or [])) != sorted(desejados)
    return True     # sessão existe e está sem webhook: reconfigurar é o certo


def _ha_envio_em_andamento(db, user_id: int) -> bool:
    from app.models.roteiro import EXEC_ENVIANDO, RoteiroExecucao

    return (
        db.query(RoteiroExecucao)
        .filter(RoteiroExecucao.user_id == user_id,
                RoteiroExecucao.status == EXEC_ENVIANDO)
        .first()
    ) is not None


def sincronizar_eventos(db, instancia, precisa_de_message: bool,
                        webhook_url: str, verificar_envio: bool = True) -> bool:
    """
    Alinha os eventos assinados da sessão com o estado do monitoramento.

    Devolve True se reconfigurou. Não faz nada quando já está alinhado — o
    `PUT /api/sessions/{sessao}` **reinicia a sessão**, e reiniciar à toa
    derruba a conexão de um número por nada.

    Levanta `EnvioEmAndamento` se houver execução enviando para esta usuária:
    o restart mataria o lote no meio. As linhas não duplicam (o claim garante
    isso), mas o envio pararia sem a afiliada entender por quê — melhor recusar
    com uma frase clara do que fazer o estrago e explicar depois.

    `verificar_envio=False` é para quem já checou ANTES de mexer em qualquer
    sessão (ver `sincronizar_todas`): com várias sessões, checar por sessão
    deixaria a primeira reconfigurada e a segunda recusada.
    """
    if not (webhook_url or "").strip():
        # Guarda dura: `config_de_webhook("")` gravaria `url: ""` na sessão e ela
        # perderia TODO o webhook — `session.status` (o número cairia e a tela
        # continuaria "conectada"), `group.v2.participants` (F6) e o próprio
        # `message` que o toggle acabou de pedir.
        logger.error("Sem URL de webhook: sessão %s não reconfigurada",
                     getattr(instancia, "nome_instancia", "?"))
        return False
    desejados = eventos_desejados(precisa_de_message)
    if not _precisa_reconfigurar(instancia, desejados):
        return False
    if verificar_envio and _ha_envio_em_andamento(db, instancia.user_id):
        raise EnvioEmAndamento(
            "Há um envio em andamento neste número. Tente de novo quando ele "
            "terminar — mudar isso agora reiniciaria a conexão e pararia o envio."
        )
    # O PUT reescreve o `config` INTEIRO: mandar só os webhooks apagaria o
    # `config.proxy` da sessão, e ela voltaria a sair pelo IP do servidor — em
    # silêncio, que é a pior forma de perder o proxy.
    cliente_da_sessao(instancia.nome_instancia).atualizar_sessao(
        config_de_webhook(webhook_url, desejados),
        proxy=proxy_pool_service.credenciais_da_instancia(db, instancia),
    )
    logger.info("Sessão %s reconfigurada: eventos=%s",
                instancia.nome_instancia, desejados)
    return True


def sincronizar_todas(db, instancias, precisa_por_instancia: Dict[int, bool],
                      webhook_url: str) -> Tuple[int, List[str]]:
    """
    Alinha VÁRIAS sessões como uma operação só: ou todas as que precisam mudam,
    ou nenhuma muda.

    Devolve `(quantas mudaram, nomes das sessões cujo estado não deu para
    confirmar)`. A segunda parte não é detalhe: quem chama precisa saber que
    "não mudou nada" pode significar "não consegui falar com o WhatsApp", e não
    "já estava do jeito certo".

    Reconfigurar sessão por sessão deixaria a primeira reiniciada e a segunda
    recusada por envio em andamento — e aí o banco diverge do que as sessões
    realmente escutam, que é o pior estado possível para este módulo.
    """
    if not (webhook_url or "").strip():
        logger.error("Sem URL de webhook: nenhuma sessão reconfigurada")
        return 0, [getattr(i, "nome_instancia", "?") for i in instancias]
    pendentes, desconhecidas = [], []
    for i in instancias:
        estado = _precisa_reconfigurar(
            i, eventos_desejados(precisa_por_instancia.get(i.id, False))
        )
        if estado is None:
            desconhecidas.append(getattr(i, "nome_instancia", "?"))
        elif estado:
            pendentes.append(i)
    if not pendentes:
        return 0, desconhecidas
    if any(_ha_envio_em_andamento(db, i.user_id) for i in pendentes):
        raise EnvioEmAndamento(
            "Há um envio em andamento. Tente de novo quando ele terminar — "
            "mudar isso agora reiniciaria a conexão e pararia o envio."
        )
    # Aplica direto: `pendentes` já foi filtrado por `_precisa_reconfigurar`, e
    # re-perguntar chamaria `GET /api/sessions/{s}` uma segunda vez por número.
    feitas = 0
    for i in pendentes:
        cliente_da_sessao(i.nome_instancia).atualizar_sessao(
            config_de_webhook(webhook_url,
                              eventos_desejados(precisa_por_instancia.get(i.id, False))),
            proxy=proxy_pool_service.credenciais_da_instancia(db, i),
        )
        logger.info("Sessão %s reconfigurada", i.nome_instancia)
        feitas += 1
    return feitas, desconhecidas


def config_de_webhook(url: str, eventos: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Config de webhook de uma sessão. Sem chaves None no payload — o WAHA
    responde 422 a `hmac: null`, e 422 engolido já foi bug aqui.
    """
    wh: Dict[str, Any] = {
        "url": url,
        "events": list(eventos or EVENTOS_DE_ALUNA),
        "retries": {"policy": "exponential", "delaySeconds": 2, "attempts": 5},
    }
    if settings.WAHA_WEBHOOK_TOKEN:
        wh["hmac"] = {"key": settings.WAHA_WEBHOOK_TOKEN}
        wh["customHeaders"] = [{"name": "X-Webhook-Token", "value": settings.WAHA_WEBHOOK_TOKEN}]
    return [wh]


class WhatsappInstanciaService:
    def __init__(self, repo, plan_limit_numeros: int, webhook_url: Optional[str],
                 db=None):
        self.repo = repo
        self.plan_limit_numeros = plan_limit_numeros
        self.webhook_url = webhook_url
        # A alocação de proxy é a única coisa aqui que precisa de Session
        # própria (o pool é global, não passa pelo repo de instâncias).
        self.db = db if db is not None else getattr(repo, "db", None)

    def criar(self, user_id: int, nome_exibicao: Optional[str]) -> WhatsappInstancia:
        from app.core.plans import is_unlimited

        ativas = self.repo.por_usuario(user_id)
        if not is_unlimited(self.plan_limit_numeros):
            if self.plan_limit_numeros <= 0:
                raise LimiteDeNumeros("PLANO_INSUFICIENTE: Números de WhatsApp são exclusivos do plano Max")
            if len(ativas) >= self.plan_limit_numeros:
                raise LimiteDeNumeros(f"Limite de {self.plan_limit_numeros} números atingido")
        if self.repo.total_global_ativas() >= settings.WHATSAPP_MAX_INSTANCIAS_GLOBAL:
            logger.error("Cap global de sessões WAHA atingido (%s)",
                         settings.WHATSAPP_MAX_INSTANCIAS_GLOBAL)
            raise LimiteGlobal("Estamos no limite de conexões da plataforma. Tente mais tarde.")

        nome = nome_de_instancia(user_id)
        cliente = cliente_da_sessao(nome)
        if not self.webhook_url or not settings.WAHA_WEBHOOK_TOKEN:
            # Sem webhook a sessão pareia, mas o estado nunca chega: número
            # caído continua "conectada" na tela até alguém abrir o QR.
            logger.error("Sessão %s criada SEM webhook (WAHA_WEBHOOK_URL/TOKEN ausentes)", nome)
        webhooks = config_de_webhook(self.webhook_url) if self.webhook_url else None

        instancia = WhatsappInstancia(
            user_id=user_id,
            nome_exibicao=(nome_exibicao or "").strip() or f"Número {len(ativas) + 1}",
            nome_instancia=nome,
            status=INSTANCIA_CRIADA,
        )
        # Proxy ANTES do WAHA: a sessão precisa nascer já atrás do IP dela.
        # Aplicar depois exigiria stop→PUT→start numa sessão recém-pareada —
        # justamente a troca de IP que o desenho existe para evitar.
        # Com o pool esgotado e WHATSAPP_PROXY_OBRIGATORIO=true isto levanta
        # `sem_proxy` e nada é criado (nem aqui, nem no WAHA).
        proxy = proxy_pool_service.alocar(self.db, instancia)
        # WAHA primeiro: se falhar, nada é persistido — linha órfã local
        # consumiria o limite do plano sem sessão nenhuma por trás. E o proxy
        # só é fixado depois do sucesso: alocado sem sessão = vaga fantasma.
        cliente.criar_sessao(
            webhooks=webhooks,
            proxy=proxy_pool_service.credenciais(proxy),
        )
        proxy_pool_service.fixar(instancia, proxy)
        return self.repo.salvar(instancia)

    def qr(self, instancia: WhatsappInstancia) -> Dict[str, Any]:
        """Estado + QR (quando há QR a mostrar). Reconcilia o status local.

        UMA leitura de sessao_info por poll — a tela consulta em loop e cada
        round-trip extra multiplica a carga no WAHA.
        """
        cliente = cliente_da_sessao(instancia.nome_instancia)
        try:
            info = cliente.sessao_info()
            if not info:
                webhooks = config_de_webhook(self.webhook_url) if self.webhook_url else None
                # MESMO proxy já fixado: recriar a sessão com outro IP (ou sem
                # IP) é a troca que queremos evitar — e aqui ela aconteceria
                # sozinha, num poll de tela.
                cliente.criar_sessao(
                    webhooks=webhooks,
                    proxy=proxy_pool_service.credenciais_da_instancia(
                        self.db, instancia),
                )
                info = cliente.sessao_info()
            estado = str(info.get("status") or "inexistente")
        except ErroWhatsapp as e:
            return {"estado": f"erro: {e.motivo}", "qrcode": None}

        if estado == "WORKING":
            self._marcar_conectada(instancia, info)
            return {"estado": "conectada", "qrcode": None}

        if estado in ("STOPPED", "FAILED"):
            # Restart do WAHA/logout no celular: sem religar, a tela ficaria
            # em "aguardando" sem QR para sempre (e recriar o número consome
            # o limite do plano). O próximo poll pega o QR.
            try:
                cliente.iniciar_sessao()
            except ErroWhatsapp:
                pass
            return {"estado": "aguardando", "qrcode": None}

        qr = None
        if estado in ("SCAN_QR_CODE", "STARTING"):
            try:
                qr = cliente.qrcode()
            except ErroWhatsapp:
                qr = None
        return {"estado": "aguardando", "qrcode": qr}

    def _marcar_conectada(self, instancia: WhatsappInstancia, info: Dict[str, Any]) -> None:
        numero = numero_de_jid((info.get("me") or {}).get("id")) or None
        mudou = (
            instancia.status != INSTANCIA_CONECTADA
            or instancia.falhas_seguidas != 0
            or (numero and instancia.numero != numero)
        )
        if not mudou:
            return  # poll conectado não vira transação de escrita
        instancia.status = INSTANCIA_CONECTADA
        instancia.falhas_seguidas = 0
        instancia.ultima_conexao_em = datetime.now(timezone.utc)
        if numero:
            instancia.numero = numero
        self.repo.salvar(instancia)
        logger.info("Sessão %s conectada", instancia.nome_instancia)

    def listar(self, user_id: int) -> List[WhatsappInstancia]:
        return self.repo.por_usuario(user_id)

    def obter(self, user_id: int, instancia_id: int) -> Optional[WhatsappInstancia]:
        """Ownership mora aqui — a rota nunca consulta o repo direto."""
        return self.repo.por_id(user_id, instancia_id)

    def remover(self, instancia: WhatsappInstancia) -> None:
        """Logout + delete no WAHA; soft-delete local (histórico preservado)."""
        try:
            cliente_da_sessao(instancia.nome_instancia).deletar_sessao()
        except ErroWhatsapp as e:
            # A sessão pode já não existir no WAHA — remover localmente mesmo
            # assim; a reconciliação diária (F6) limpa órfãs do outro lado.
            logger.warning("Falha ao deletar sessão %s no WAHA: %s",
                           instancia.nome_instancia, e.motivo)
        instancia.status = INSTANCIA_REMOVIDA
        # Devolve a vaga ao pool na MESMA gravação: proxy preso a número
        # removido esgotaria o pool com fantasmas.
        proxy_pool_service.liberar(self.db, instancia)
        self.repo.salvar(instancia)


def aplicar_proxy_na_sessao(db, instancia, webhook_url: Optional[str] = None,
                            verificar_envio: bool = True) -> None:
    """
    Faz a sessão JÁ EXISTENTE passar a sair pelo proxy fixado hoje no banco:
    `stop` → `PUT` do config inteiro → `start`.

    Usa `parar_sessao` (não `deletar_sessao`): delete faz logout e exigiria
    novo QR com certeza. Com o stop, a sessão mantém a credencial do WhatsApp.

    ⚠️ **Não confirmado** se o WAHA re-pareia mesmo assim — é o spike §1 do
    plano, a rodar em homologação. Por isso este caminho é sempre disparado
    por gente (botão do admin, com confirmação), nunca sozinho, enquanto
    `WHATSAPP_PROXY_APLICAR_AUTOMATICO` estiver desligado.

    Recusa no meio de um envio pelo mesmo motivo do `sincronizar_eventos`: o
    restart mataria o lote e a afiliada não entenderia por quê.
    """
    if verificar_envio and _ha_envio_em_andamento(db, instancia.user_id):
        raise EnvioEmAndamento(
            "Há um envio em andamento neste número. Tente de novo quando ele "
            "terminar — trocar o IP agora reiniciaria a conexão e pararia o envio."
        )
    url = webhook_url or settings.WAHA_WEBHOOK_URL or ""
    if not url.strip():
        # Sem URL, o PUT gravaria `url: ""` e a sessão perderia TODO o webhook.
        raise ErroWhatsapp("sem_config", "WAHA_WEBHOOK_URL ausente")

    from app.services.monitoramento_service import MonitoramentoService

    precisa = MonitoramentoService(db).sessoes_que_precisam_de_message(
        instancia.user_id
    ).get(instancia.id, False)
    cliente = cliente_da_sessao(instancia.nome_instancia)
    cliente.parar_sessao()
    cliente.atualizar_sessao(
        config_de_webhook(url, eventos_desejados(precisa)),
        proxy=proxy_pool_service.credenciais_da_instancia(db, instancia),
    )
    cliente.iniciar_sessao()
    logger.warning("Sessão %s reiniciada com proxy %s aplicado",
                   instancia.nome_instancia, instancia.proxy_id)


def aplicar_evento_de_status(repo, instancia: WhatsappInstancia,
                             status_waha: str, numero: Optional[str]) -> None:
    """Espelho do `session.status` do webhook. Função de módulo de propósito:
    o webhook não tem (nem deve fabricar) contexto de plano/webhook_url."""
    if instancia.status == INSTANCIA_REMOVIDA:
        # Evento atrasado (retries do WAHA após o logout do remover): tratar
        # ressuscitaria um número deletado, que voltaria a contar no limite.
        return
    if status_waha == "WORKING":
        instancia.status = INSTANCIA_CONECTADA
        instancia.falhas_seguidas = 0
        instancia.ultima_conexao_em = datetime.now(timezone.utc)
        if numero:
            instancia.numero = numero
    elif status_waha in ("STOPPED", "FAILED", "SCAN_QR_CODE"):
        # SCAN_QR_CODE depois de conectada = a sessão caiu e pede novo pareamento.
        if instancia.status == INSTANCIA_CONECTADA:
            logger.warning("Sessão %s caiu (%s)", instancia.nome_instancia, status_waha)
        instancia.status = INSTANCIA_DESCONECTADA
    else:
        return
    repo.salvar(instancia)
