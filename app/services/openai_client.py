"""
Única fronteira com a OpenAI.

Isolar aqui é o que permite testar todo o resto do Diagnóstico IA sem rede.
Todo erro sai tipado (ErroIA.motivo) para a camada de cima decidir o que
mostrar — e, principalmente, para NÃO debitar crédito quando a IA falha.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

URL = "https://api.openai.com/v1/chat/completions"


class ErroIA(Exception):
    def __init__(self, motivo: str, detalhe: str = ""):
        self.motivo = motivo
        self.detalhe = detalhe
        super().__init__(f"{motivo}: {detalhe}" if detalhe else motivo)


class OpenAiClient:
    def __init__(self, api_key: Optional[str], modelo: str):
        self.api_key = api_key
        self.modelo = modelo
        self._transport = None  # trocado por MockTransport nos testes

    def disponivel(self) -> bool:
        return bool(self.api_key)

    def _chamar(self, mensagens: List[Dict[str, str]], timeout: float,
                json_mode: bool) -> Tuple[str, int, int]:
        if not self.disponivel():
            raise ErroIA("sem_chave", "OPENAI_API_KEY não configurada")

        corpo: Dict[str, Any] = {"model": self.modelo, "messages": mensagens}
        if json_mode:
            corpo["response_format"] = {"type": "json_object"}

        try:
            with httpx.Client(timeout=timeout, transport=self._transport) as cliente:
                r = cliente.post(
                    URL,
                    json=corpo,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except httpx.TimeoutException as e:
            raise ErroIA("timeout", str(e))
        except httpx.HTTPError as e:
            raise ErroIA("http", str(e))

        if r.status_code >= 400:
            logger.error("OpenAI %s: %s", r.status_code, r.text[:300])
            raise ErroIA("http", f"status {r.status_code}")

        # Protege leitura de corpo da resposta contra formatos inesperados.
        # A API pode responder 200 com corpo malformado (não-JSON, estrutura alterada,
        # choices vazio ou ausente), o que violaria o contrato se escapasse como exceção crua.
        try:
            dados = r.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise ErroIA("formato", f"corpo não é JSON válido: {str(e)[:100]}") from e

        try:
            # Garante que choices existe, é lista não-vazia, e tem a estrutura esperada
            conteudo = dados["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ErroIA("formato", f"estrutura de resposta inesperada: {str(e)[:100]}") from e

        # Extrai tokens com cuidado para valor não numérico.
        # Se usage não for dict (ex: string, lista, número), trata como vazio.
        # Tokens são telemetria; a resposta é válida mesmo se falhar.
        uso = dados.get("usage")
        if not isinstance(uso, dict):
            uso = {}
        try:
            prompt_tokens = int(uso.get("prompt_tokens") or 0)
            completion_tokens = int(uso.get("completion_tokens") or 0)
        except (ValueError, TypeError) as e:
            raise ErroIA("formato", f"tokens não numéricos: {str(e)[:100]}") from e

        return conteudo, prompt_tokens, completion_tokens

    def completar_json(self, sistema: str, usuario: str,
                       timeout: float = 60.0) -> Tuple[Dict[str, Any], int, int]:
        conteudo, entrada, saida = self._chamar(
            [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
            timeout=timeout, json_mode=True,
        )
        try:
            return json.loads(conteudo), entrada, saida
        except (json.JSONDecodeError, TypeError) as e:
            raise ErroIA("formato", str(e))

    def completar_texto(self, sistema: str, mensagens: List[Dict[str, str]],
                        timeout: float = 60.0) -> Tuple[str, int, int]:
        return self._chamar(
            [{"role": "system", "content": sistema}] + mensagens,
            timeout=timeout, json_mode=False,
        )
