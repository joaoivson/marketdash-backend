"""
Aviso por WhatsApp quando a PRODUÇÃO cai — e quando volta.

## Por que este código mora aqui, e não em produção

O WhatsApp (WAHA) só existe no ambiente de **homologação**: produção tem zero
instâncias conectadas. E há uma razão melhor do que "é onde está instalado":
**uma aplicação caída não avisa que caiu**. Quem detecta é a sonda externa
(GitHub Actions) e quem envia precisa ser um processo que sobreviva à queda —
a API de homologação é outro container, com outro banco e outro worker. Em
11/09, com produção 20 h fora do ar, homologação respondeu 200 o tempo todo.

**O limite honesto:** hml roda no MESMO VPS. Se a máquina inteira cair, o
alerta não sai. Para cobrir isso é preciso um serviço de uptime externo
(UptimeRobot e afins) — este caminho cobre o modo de falha real e mais comum,
que é o container/rota de produção morrer com o resto do VPS de pé.

## Por que existe deduplicação

A sonda roda a cada ~10 min. Sem estado, uma queda de 3 h viraria 18 mensagens
iguais — e o alerta vira ruído que se aprende a ignorar. O incidente é aberto
uma vez, e a segunda mensagem só chega quando a produção volta (com a duração).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings
from app.services.waha_client import ErroWhatsapp, WahaClient, normalizar_numero

logger = logging.getLogger(__name__)

#: Chave do incidente aberto. Vive no Redis porque precisa sobreviver a
#: restart da API — e um incidente é justamente quando as coisas reiniciam.
CHAVE_INCIDENTE = "alerta_producao:incidente_aberto"

#: 24 h. Se a produção ficar caída mais do que isso, um segundo aviso é
#: bem-vindo: significa que ninguém agiu.
TTL_INCIDENTE = 24 * 3600


def _destinos() -> list[str]:
    """Números que recebem o aviso, do env (csv), em E.164 sem '+'."""
    brutos = (settings.ALERTA_WHATSAPP_NUMEROS or "").split(",")
    numeros = []
    for bruto in brutos:
        bruto = bruto.strip()
        if not bruto:
            continue
        try:
            numeros.append(normalizar_numero(bruto))
        except Exception:  # noqa: BLE001 — número mal digitado no env não derruba o alerta
            logger.warning("Destino de alerta ignorado (número inválido): %s", bruto[:6] + "…")
    return numeros


def _cliente() -> Optional[WahaClient]:
    cliente = WahaClient(
        settings.WAHA_URL, settings.WAHA_API_KEY, settings.ALERTA_WHATSAPP_SESSAO
    )
    return cliente if cliente.configurado() else None


def _texto_queda(detalhe: str) -> str:
    agora = datetime.now(timezone.utc).astimezone().strftime("%d/%m às %H:%M")
    return (
        "🔴 *MarketDash — produção fora do ar*\n\n"
        f"Detectado em {agora} pela sonda externa.\n\n"
        f"{detalhe.strip()}\n\n"
        "Primeiro passo: abrir o painel Admin › Infraestrutura "
        "(marketdash.com.br/admin/infraestrutura).\n"
        "Se a CPU do VPS estiver no teto, remover a limitação no painel da "
        "Hostinger — ela não se desfaz sozinha.\n\n"
        "Aviso automático. Só volto a mandar mensagem quando normalizar."
    )


def _texto_volta(minutos: Optional[int]) -> str:
    agora = datetime.now(timezone.utc).astimezone().strftime("%d/%m às %H:%M")
    duracao = f" Ficou {minutos} min fora." if minutos is not None else ""
    return (
        "🟢 *MarketDash — produção normalizada*\n\n"
        f"A sonda voltou a receber resposta saudável em {agora}.{duracao}"
    )


def _redis():
    from app.core import cache

    return cache.get_client()


def _minutos_desde(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        inicio = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return max(0, int((datetime.now(timezone.utc) - inicio).total_seconds() // 60))


def _enviar(texto: str) -> dict:
    """Manda para todos os destinos. Falha em um número não impede os outros."""
    cliente = _cliente()
    if cliente is None:
        return {"enviado": False, "motivo": "WAHA ou sessão de alerta não configurados."}

    destinos = _destinos()
    if not destinos:
        return {"enviado": False, "motivo": "ALERTA_WHATSAPP_NUMEROS vazio."}

    entregues, falhas = [], []
    for numero in destinos:
        try:
            cliente.enviar_texto(f"{numero}@c.us", texto)
            entregues.append(numero[-4:])
        except ErroWhatsapp as e:
            logger.error("Alerta de produção não entregue para …%s: %s", numero[-4:], e)
            falhas.append({"numero": numero[-4:], "motivo": e.motivo})
        except Exception as e:  # noqa: BLE001
            logger.error("Alerta de produção falhou para …%s: %s", numero[-4:], e)
            falhas.append({"numero": numero[-4:], "motivo": type(e).__name__})
    return {"enviado": bool(entregues), "entregues": entregues, "falhas": falhas}


def registrar(estado: str, detalhe: str = "") -> dict:
    """`estado`: `caiu` ou `voltou`. Devolve o que foi feito, para a sonda logar.

    A deduplicação é a regra de negócio aqui: *caiu* com incidente já aberto é
    silêncio deliberado, não erro — e *voltou* sem incidente aberto também. O
    caminho feliz da sonda roda a cada 10 minutos e não pode custar mensagem.
    """
    redis = _redis()
    if redis is None:
        # Sem Redis não há como deduplicar. Mandar mesmo assim é pior: a cada
        # 10 min sai uma mensagem igual até alguém silenciar o número.
        return {"enviado": False, "motivo": "Redis indisponível — sem dedup, não envio."}

    if estado == "caiu":
        try:
            aberto = redis.get(CHAVE_INCIDENTE)
        except Exception as e:  # noqa: BLE001
            return {"enviado": False, "motivo": f"Redis: {type(e).__name__}"}
        if aberto:
            return {"enviado": False, "motivo": "Incidente já avisado.",
                    "aberto_ha_min": _minutos_desde(aberto)}
        resultado = _enviar(_texto_queda(detalhe or "A sonda externa não conseguiu falar com a produção."))
        if resultado.get("enviado"):
            redis.set(CHAVE_INCIDENTE, datetime.now(timezone.utc).isoformat(), ex=TTL_INCIDENTE)
        return resultado

    if estado == "voltou":
        try:
            aberto = redis.get(CHAVE_INCIDENTE)
        except Exception as e:  # noqa: BLE001
            return {"enviado": False, "motivo": f"Redis: {type(e).__name__}"}
        if not aberto:
            return {"enviado": False, "motivo": "Nenhum incidente aberto — nada a avisar."}
        resultado = _enviar(_texto_volta(_minutos_desde(aberto)))
        # Fecha o incidente mesmo se o envio falhou: manter aberto faria a
        # próxima queda ficar MUDA, que é o pior desfecho possível.
        redis.delete(CHAVE_INCIDENTE)
        return resultado

    return {"enviado": False, "motivo": f"Estado desconhecido: {estado!r}"}


def testar(texto: Optional[str] = None) -> dict:
    """Envio de teste, sem tocar no estado do incidente."""
    return _enviar(
        texto
        or "🔧 MarketDash — teste do alerta de produção. Se você recebeu isto, o "
        "aviso automático de queda está funcionando."
    )
