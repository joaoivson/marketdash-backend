"""
Única fronteira com a Evolution API.

Mesmo racional do openai_client: isolar aqui é o que permite testar o resto do
WhatsApp sem rede, e todo erro sai tipado (`ErroWhatsapp.motivo`) para quem
chama decidir entre "tenta de novo amanhã" e "para o lote inteiro".

A diferença que importa: aqui o efeito colateral é uma mensagem no celular de
alguém. Não existe "tentar de novo por precaução" — na dúvida, não manda.
"""
import logging
import re
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# Motivos que valem parar o lote inteiro: o número está fora do ar, e insistir
# só gasta tentativa (ou piora a situação dele com a Meta).
MOTIVOS_FATAIS = {"sem_config", "desconectado", "auth"}


class ErroWhatsapp(Exception):
    def __init__(self, motivo: str, detalhe: str = ""):
        self.motivo = motivo
        self.detalhe = detalhe
        super().__init__(f"{motivo}: {detalhe}" if detalhe else motivo)

    @property
    def fatal(self) -> bool:
        return self.motivo in MOTIVOS_FATAIS


def normalizar_numero(bruto: str) -> str:
    """
    Número brasileiro digitado por gente → E.164 sem '+'.

    Aceita "(11) 99999-8888", "11999998888", "+55 11 99999-8888". Assume Brasil
    quando não vem DDI: é o público inteiro do produto hoje, e adivinhar outro
    país mandaria mensagem para o número errado.
    """
    digitos = re.sub(r"\D", "", bruto or "")
    if not digitos:
        raise ValueError("Informe um número de celular.")

    if digitos.startswith("55") and len(digitos) in (12, 13):
        pass
    elif len(digitos) in (10, 11):
        digitos = "55" + digitos
    else:
        raise ValueError("Número inválido. Use DDD + número, como (11) 99999-8888.")

    corpo = digitos[2:]
    if len(corpo) == 11 and corpo[2] != "9":
        raise ValueError("Número de celular inválido — o nono dígito deve ser 9.")
    if len(corpo) == 10:
        raise ValueError("Informe um celular, não um telefone fixo.")
    return digitos


def mascarar(numero: str) -> str:
    """Para mostrar na tela e no log sem expor o número inteiro."""
    if not numero or len(numero) < 6:
        return "•••"
    return f"{numero[:4]}•••••{numero[-2:]}"


class EvolutionClient:
    def __init__(self, base_url: Optional[str], api_key: Optional[str],
                 instancia: Optional[str], timeout: float = 20.0):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.instancia = instancia
        self.timeout = timeout
        self._transport = None  # trocado por MockTransport nos testes

    def configurado(self) -> bool:
        return bool(self.base_url and self.api_key and self.instancia)

    def _pedir(self, metodo: str, caminho: str,
               corpo: Optional[Dict[str, Any]] = None,
               auth_em_403: bool = True) -> Tuple[int, Any]:
        if not self.configurado():
            raise ErroWhatsapp("sem_config", "EVOLUTION_URL/API_KEY/INSTANCIA ausentes")
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as c:
                r = c.request(
                    metodo, f"{self.base_url}{caminho}",
                    json=corpo,
                    headers={"apikey": self.api_key, "Content-Type": "application/json"},
                )
        except httpx.TimeoutException as e:
            raise ErroWhatsapp("timeout", str(e)[:120]) from e
        except httpx.HTTPError as e:
            raise ErroWhatsapp("rede", str(e)[:120]) from e

        # 403 é ambíguo na Evolution: chave errada E "instância já existe" usam
        # o mesmo código. Quem sabe distinguir é o método que chamou — criar
        # instância trata 403 como conflito, não como credencial inválida.
        if r.status_code == 401 or (r.status_code == 403 and auth_em_403):
            raise ErroWhatsapp("auth", f"status {r.status_code}")
        try:
            dados = r.json()
        except ValueError:
            dados = {"texto": r.text[:200]}
        return r.status_code, dados

    def estado(self) -> str:
        """open = número conectado; close/connecting = não dá para enviar."""
        _, dados = self._pedir("GET", f"/instance/connectionState/{self.instancia}")
        if isinstance(dados, dict):
            interno = dados.get("instance") if isinstance(dados.get("instance"), dict) else dados
            return str((interno or {}).get("state") or "desconhecido")
        return "desconhecido"

    def conectado(self) -> bool:
        try:
            return self.estado() == "open"
        except ErroWhatsapp:
            return False

    def qrcode(self) -> Dict[str, Any]:
        """
        QR para o admin parear o número.

        A Evolution v2 devolve `{"count": 0}` — sem `base64` — quando ainda não
        gerou o código (instância recém-criada, WebSocket com o WhatsApp ainda
        subindo) e quando o número já está conectado. Quem chama precisa tratar
        a ausência do QR como estado normal, não como erro.
        """
        _, dados = self._pedir("GET", f"/instance/connect/{self.instancia}")
        return dados if isinstance(dados, dict) else {}

    def instancia_existe(self) -> bool:
        status, _ = self._pedir("GET", f"/instance/connectionState/{self.instancia}")
        return status < 400

    def criar_instancia(self) -> Dict[str, Any]:
        """
        Cria a instância se ela ainda não existe.

        Antes isso era um `curl` no passo a passo de implantação, o que quer
        dizer um passo a mais para errar toda vez que o ambiente é recriado ou
        o número é trocado depois de um banimento. A tela do admin chama isto.
        """
        status, dados = self._pedir(
            "POST", "/instance/create",
            {"instanceName": self.instancia, "integration": "WHATSAPP-BAILEYS", "qrcode": True},
            auth_em_403=False,
        )
        if status >= 400:
            # Instância já existente é sucesso para o nosso propósito: o método
            # é chamado toda vez que a tela do admin abre.
            #
            # A Evolution responde este 403 ANTES de conferir a chave, então
            # aqui uma chave errada também cairia como "já existia". Não é
            # problema no fluxo real: a rota chama instancia_existe() primeiro,
            # que usa /connectionState e devolve 401 com chave inválida.
            if any(p in str(dados).lower() for p in ("already", "in use", "exists")):
                return {"ja_existia": True}
            raise ErroWhatsapp("criar_instancia", f"status {status}: {str(dados)[:150]}")
        return dados if isinstance(dados, dict) else {}

    def configurar_webhook(self, url: str, token: str) -> Dict[str, Any]:
        """
        Aponta o webhook para a nossa API. Só MESSAGES_UPSERT: é tudo que o
        SAIR precisa, e receber o resto seria guardar conversa alheia sem motivo.
        """
        status, dados = self._pedir(
            "POST", f"/webhook/set/{self.instancia}",
            {"webhook": {
                "enabled": True,
                "url": url,
                "headers": {"X-Webhook-Token": token, "Content-Type": "application/json"},
                "byEvents": False,
                "base64": False,
                "events": ["MESSAGES_UPSERT"],
            }},
        )
        if status >= 400:
            raise ErroWhatsapp("webhook", f"status {status}: {str(dados)[:150]}")
        return dados if isinstance(dados, dict) else {}

    def enviar_texto(self, numero: str, texto: str) -> Dict[str, Any]:
        status, dados = self._pedir(
            "POST", f"/message/sendText/{self.instancia}",
            {"number": numero, "text": texto},
        )
        if status >= 400:
            detalhe = str(dados)[:200]
            # A Evolution devolve 400 tanto para "número não existe no WhatsApp"
            # quanto para "instância fora do ar". A primeira é problema de UMA
            # afiliada; a segunda derruba o lote. Separar aqui evita desligar
            # todo mundo por causa de um número errado.
            if any(p in detalhe.lower() for p in ("not exist", "invalid number", "jid")):
                raise ErroWhatsapp("numero_invalido", detalhe)
            if any(p in detalhe.lower() for p in ("close", "not connected", "disconnect")):
                raise ErroWhatsapp("desconectado", detalhe)
            raise ErroWhatsapp("envio", f"status {status}: {detalhe}")
        return dados if isinstance(dados, dict) else {}
