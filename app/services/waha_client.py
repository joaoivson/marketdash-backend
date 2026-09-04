"""
Única fronteira com o WAHA (WhatsApp HTTP API — waha.devlike.pro).

Substitui o EvolutionClient (decisão de 25/08: engine GOWS/whatsmeow, ~60MB
por sessão contra 300-500MB do Baileys, projeto Apache-2.0 sem o licenciamento
da Evolution 2.4). O desenho é o mesmo: um ponto único HTTP (`_pedir`) trocável
por MockTransport nos testes, e todo erro sai tipado (`ErroWhatsapp.motivo`)
para quem chama decidir entre "tenta de novo amanhã" e "para o lote inteiro".

A diferença que importa continua a mesma: aqui o efeito colateral é uma
mensagem no celular de alguém. Não existe "tentar de novo por precaução" — na
dúvida, não manda.

Sessões: cada número conectado é uma sessão nomeada. O nome carrega o prefixo
do ambiente (`mkd{ref4}...`, via identidade_do_banco) porque homologação e
produção podem compartilhar o mesmo servidor WAHA — sem o prefixo, um ambiente
derruba a sessão do outro.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# Motivos que valem parar o lote inteiro: a sessão está fora do ar, e insistir
# só gasta tentativa (ou piora a situação do número com a Meta).
MOTIVOS_FATAIS = {"sem_config", "desconectado", "auth"}

# Estados de sessão do WAHA que significam "dá para enviar".
ESTADO_CONECTADO = "WORKING"

JID_DE_GRUPO = re.compile(r"^\d+(-\d+)?@g\.us$")


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


def chat_id_de_numero(numero: str) -> str:
    """E.164 sem '+' → chatId de conversa individual no WAHA."""
    return f"{numero}@c.us"


def campo(dados: Any, *nomes: str) -> Any:
    """
    Primeiro campo presente no payload, ignorando maiúsculas/minúsculas.

    Existe porque o WAHA **não tem um formato só**: o engine GOWS devolve as
    structs do whatsmeow como o Go serializa (`JID`, `Name`, `Participants`,
    `IsAdmin`), enquanto NOWEB/WEBJS usam `id`, `subject`, `participants`,
    `role`. Em 26/08/2026 essa diferença fez o sync trazer 499 grupos e gravar
    zero, sem erro nenhum. Ler as duas formas é a defesa barata contra a
    próxima variação.
    """
    if not isinstance(dados, dict):
        return None
    indice = {str(k).lower(): v for k, v in dados.items()}
    for nome in nomes:
        valor = indice.get(nome.lower())
        if valor is not None:
            return valor
    return None


def tem_campo(dados: Any, *nomes: str) -> bool:
    """Presença do campo (mesmo valendo False/0), sem diferenciar caixa."""
    if not isinstance(dados, dict):
        return False
    presentes = {str(k).lower() for k in dados}
    return any(n.lower() in presentes for n in nomes)


def numero_de_jid(bruto) -> str:
    """
    JID/chatId do WAHA → número cru. Inverso de chat_id_de_numero — fonte
    ÚNICA deste parse: se o formato mudar (ex.: sufixos @lid do GOWS), muda
    aqui e todos os call-sites acompanham.
    """
    return str(bruto or "").split("@")[0].split(":")[0]


def validar_jid_de_grupo(jid: str) -> str:
    """
    JID de grupo (120363...@g.us). NUNCA passa por normalizar_numero — ela
    rejeitaria o formato, e um JID "normalizado" viraria mensagem para um
    número desconhecido.
    """
    if not JID_DE_GRUPO.match(jid or ""):
        raise ValueError(f"JID de grupo inválido: {jid!r}")
    return jid


def _lista_de_grupos(dados: Any) -> List[Dict[str, Any]]:
    """
    Normaliza a resposta de `/groups` para uma lista.

    A documentação do WAHA diz, com todas as letras, que a resposta **depende do
    engine**. A versão anterior fazia `dados if isinstance(dados, list) else []`:
    qualquer formato diferente virava zero grupos, com o sync marcado como
    SUCESSO. Foi exatamente o que aconteceu — quatro sincronizações seguidas
    "bem-sucedidas" com `vistos=0` e nenhum log dizendo por quê.

    Aqui: lista é lista; envelope conhecido é desembrulhado; e qualquer outra
    coisa vira ERRO, não vazio. Zero grupos precisa ser um fato do WhatsApp,
    nunca um formato que não soubemos ler.
    """
    if isinstance(dados, list):
        return dados
    if isinstance(dados, dict):
        for chave in ("groups", "data", "items", "results"):
            valor = dados.get(chave)
            if isinstance(valor, list):
                logger.info("Grupos vieram embrulhados em %r — desembrulhado", chave)
                return valor
    raise ErroWhatsapp(
        "grupos",
        f"formato inesperado de /groups: {type(dados).__name__} "
        f"{str(dados)[:120]}",
    )

def mascarar_proxy(config: Any) -> Any:
    """Cópia de um `config` de sessão com a credencial do proxy trocada por
    `***`. Log de `config.proxy` cru vazaria usuário e senha do IP — a mesma
    regra dos tokens da Shopee."""
    if not isinstance(config, dict):
        return config
    limpo = dict(config)
    if "proxy" in limpo:
        limpo["proxy"] = "***"
    return limpo


def _config_de_sessao(webhooks: Optional[List[Dict[str, Any]]],
                      proxy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Monta o `config` INTEIRO da sessão.

    Existe porque `webhooks` e `proxy` moram na mesma chave: escrever um
    sobrescreve o outro. Foi por isso que o método passou a montar o dict
    completo em vez de `{"webhooks": ...}` direto.
    """
    config: Dict[str, Any] = {}
    if webhooks:
        config["webhooks"] = webhooks
    if proxy:
        config["proxy"] = proxy
    return config


class WahaClient:
    def __init__(self, base_url: Optional[str], api_key: Optional[str],
                 sessao: Optional[str], timeout: float = 20.0):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.sessao = sessao
        self.timeout = timeout
        self._transport = None  # trocado por MockTransport nos testes
        self._client = None  # httpx.Client persistente (lazy) — 1 handshake por cliente, não por chamada

    def configurado(self) -> bool:
        return bool(self.base_url and self.api_key and self.sessao)

    def fechar(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __del__(self):
        try:
            self.fechar()
        except Exception:
            pass

    def _pedir(self, metodo: str, caminho: str,
               corpo: Optional[Dict[str, Any]] = None,
               params: Optional[Dict[str, Any]] = None,
               auth_em_403: bool = True) -> Tuple[int, Any]:
        if not self.configurado():
            raise ErroWhatsapp("sem_config", "WAHA_URL/WAHA_API_KEY/sessão ausentes")
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, transport=self._transport)
        try:
            r = self._client.request(
                metodo, f"{self.base_url}{caminho}",
                json=corpo, params=params,
                headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
            )
        except httpx.TimeoutException as e:
            raise ErroWhatsapp("timeout", str(e)[:120]) from e
        except httpx.HTTPError as e:
            raise ErroWhatsapp("rede", str(e)[:120]) from e

        # 403 no ENVIO é ambíguo (grupo que nos removeu também responde 403);
        # quem envia classifica por destino. Fora do envio, 401/403 = credencial.
        if r.status_code == 401 or (r.status_code == 403 and auth_em_403):
            raise ErroWhatsapp("auth", f"status {r.status_code}")
        try:
            dados = r.json()
        except ValueError:
            dados = {"texto": r.text[:200]}
        return r.status_code, dados

    # --- sessão ------------------------------------------------------------

    def sessao_info(self) -> Dict[str, Any]:
        """GET /api/sessions/{sessao} — status, engine e o número conectado."""
        status, dados = self._pedir("GET", f"/api/sessions/{self.sessao}")
        if status == 404:
            return {}
        if status >= 400:
            raise ErroWhatsapp("sessao", f"status {status}: {str(dados)[:150]}")
        return dados if isinstance(dados, dict) else {}

    def estado(self) -> str:
        """WORKING = conectado; SCAN_QR_CODE/STARTING/STOPPED/FAILED = não envia."""
        info = self.sessao_info()
        return str(info.get("status") or "inexistente")

    def conectado(self) -> bool:
        try:
            return self.estado() == ESTADO_CONECTADO
        except ErroWhatsapp:
            return False

    def numero_conectado(self) -> Optional[str]:
        """O número da sessão (me.id = '5511...@c.us'), quando pareada."""
        me = self.sessao_info().get("me") or {}
        return numero_de_jid(me.get("id")) or None

    def sessao_existe(self) -> bool:
        return bool(self.sessao_info())

    def criar_sessao(self, webhooks: Optional[List[Dict[str, Any]]] = None,
                     start: bool = True,
                     proxy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Cria (ou reaproveita) a sessão. Sessão já existente é sucesso para o
        nosso propósito — o método roda toda vez que a tela de conexão abre.

        `proxy` é o `config.proxy` do WAHA já decifrado
        (`proxy_pool_service.credenciais`): `{"server": "host:porta",
        "username": ..., "password": ...}`, com o server SEM esquema.
        """
        corpo: Dict[str, Any] = {"name": self.sessao, "start": start}
        config = _config_de_sessao(webhooks, proxy)
        if config:
            corpo["config"] = config
        status, dados = self._pedir("POST", "/api/sessions", corpo)
        detalhe = str(dados).lower()
        if status == 409 or (
            status >= 400 and any(p in detalhe for p in ("already", "exists", "in use"))
        ):
            return {"ja_existia": True}
        if status >= 400:
            raise ErroWhatsapp("criar_sessao", f"status {status}: {str(dados)[:150]}")
        return dados if isinstance(dados, dict) else {}

    def atualizar_sessao(self, webhooks: List[Dict[str, Any]],
                         proxy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        PUT /api/sessions/{sessao} — reconfigura a sessão (reinicia, mas não
        re-pareia).

        ⚠️ O `config` é enviado INTEIRO: mandar só `webhooks` apaga o
        `config.proxy` que já estava lá (e a sessão volta a sair pelo IP do
        servidor, em silêncio). Quem chama precisa passar o proxy vigente —
        ver `whatsapp_instancia_service.sincronizar_eventos`.
        """
        status, dados = self._pedir(
            "PUT", f"/api/sessions/{self.sessao}",
            {"config": _config_de_sessao(webhooks, proxy)},
        )
        if status >= 400:
            raise ErroWhatsapp("webhook", f"status {status}: {str(dados)[:150]}")
        return dados if isinstance(dados, dict) else {}

    def parar_sessao(self) -> None:
        """
        POST /api/sessions/{sessao}/stop — para sem deslogar.

        É o que permite aplicar um proxy novo (stop → PUT → start) sem
        `deletar_sessao`, que faz logout e exigiria novo QR.
        """
        status, dados = self._pedir("POST", f"/api/sessions/{self.sessao}/stop")
        # 404 = sessão não existe; 422 = já parada. Nenhum dos dois é erro para
        # quem só quer garantir que ela NÃO está rodando.
        if status >= 400 and status not in (404, 422):
            raise ErroWhatsapp("sessao", f"stop {status}: {str(dados)[:150]}")

    def iniciar_sessao(self) -> None:
        status, dados = self._pedir("POST", f"/api/sessions/{self.sessao}/start")
        if status >= 400 and status != 422:  # 422 = já iniciada
            raise ErroWhatsapp("sessao", f"start {status}: {str(dados)[:150]}")

    def qrcode(self) -> Optional[str]:
        """
        QR em base64 para parear, ou None quando não há QR a mostrar (sessão
        conectada, ainda subindo, ou parada). Ausência de QR é estado normal,
        não erro — quem chama decide o que exibir.
        """
        status, dados = self._pedir("GET", f"/api/{self.sessao}/auth/qr")
        if status >= 400 or not isinstance(dados, dict):
            return None
        base64 = dados.get("data") or dados.get("base64") or dados.get("qrCode")
        if not base64:
            return None
        if not str(base64).startswith("data:"):
            mime = dados.get("mimetype") or "image/png"
            base64 = f"data:{mime};base64,{base64}"
        return str(base64)

    def codigo_de_pareamento(self, numero: str) -> Optional[str]:
        """Código de 8 dígitos para parear SEM QR (`WhatsApp → Conectar com
        número de telefone`).

        Existe porque a afiliada abre o MarketDash NO CELULAR e o WhatsApp que
        ela vai conectar é o do mesmo aparelho — não há como escanear um QR da
        própria tela. Sem isso, parte do público simplesmente não conecta.

        Só sai com a sessão pedindo pareamento (SCAN_QR_CODE): sessão parada ou
        já conectada devolve None, que é estado normal e não erro.
        """
        limpo = normalizar_numero(numero)
        status, dados = self._pedir(
            "POST", f"/api/{self.sessao}/auth/request-code",
            {"phoneNumber": limpo},
        )
        if status >= 400 or not isinstance(dados, dict):
            return None
        codigo = dados.get("code") or dados.get("pairingCode") or dados.get("Code")
        return str(codigo) if codigo else None

    def deletar_sessao(self) -> None:
        """Logout + remoção da sessão no WAHA (a afiliada removeu o número)."""
        self._pedir("POST", f"/api/sessions/{self.sessao}/logout")
        status, dados = self._pedir("DELETE", f"/api/sessions/{self.sessao}")
        if status >= 400 and status != 404:
            raise ErroWhatsapp("sessao", f"delete {status}: {str(dados)[:150]}")

    # --- grupos ------------------------------------------------------------

    def listar_grupos(self, limite: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Uma página de grupos da sessão, COM participantes no payload.

        Dados pessoais — mudou em 04/09/2026 (migration 080). Para grupo que a
        afiliada ATIVOU, a lista de membros passa a ser persistida em
        `grupo_participantes` (ver whatsapp_grupo_sync_service): sem ela não
        existe "exportar os leads deste grupo", só "exportar os eventos de
        entrada", que num grupo de 946 pessoas devolve 8 linhas.

        Para todo o resto — grupo não ativado — segue valendo o de antes: a
        lista é usada em memória (contar participantes e descobrir se o número
        é admin) e descartada; vão para o banco só os agregados.
        """
        status, dados = self._pedir(
            "GET", f"/api/{self.sessao}/groups",
            params={"limit": limite, "offset": offset, "sortBy": "id", "sortOrder": "asc"},
        )
        if status >= 400:
            detalhe = str(dados)[:150]
            if any(p in detalhe.lower() for p in ("not working", "stopped", "scan_qr", "starting")):
                raise ErroWhatsapp("desconectado", detalhe)
            raise ErroWhatsapp("grupos", f"status {status}: {detalhe}")
        return _lista_de_grupos(dados)


    def convite_do_grupo(self, jid: str) -> Optional[str]:
        """Link de convite (só funciona quando o número é admin do grupo)."""
        # `auth_em_403=False` pelo mesmo motivo de renomear_grupo: 403 aqui é
        # "não sou admin DESTE grupo" ou "o grupo não permite convite", não
        # credencial inválida. Como `auth` é motivo FATAL, um único grupo assim
        # marcaria o número inteiro como desconectado — e o sync passa por
        # centenas de grupos de uma vez.
        status, dados = self._pedir(
            "GET", f"/api/{self.sessao}/groups/{validar_jid_de_grupo(jid)}/invite-code",
            auth_em_403=False,
        )
        if status >= 400:
            # Devolver None calado aqui foi o que escondeu "169 grupos de admin,
            # zero convites" — quem chama precisa poder dizer POR QUE falhou.
            raise ErroWhatsapp("convite", f"status {status}: {str(dados)[:120]}")
        # A doc do WAHA não publica o shape desta resposta. Medido contra o
        # servidor real em 26/08: vem o LINK inteiro em **texto puro**, não JSON
        # — e cai no invólucro {"texto": ...} do _pedir. `code`/`InviteCode`
        # ficam aceitos porque outros engines podem devolver JSON.
        codigo = (campo(dados, "code", "InviteCode", "inviteCode", "Code", "texto")
                  if isinstance(dados, dict) else dados)
        if not codigo:
            raise ErroWhatsapp("convite", f"resposta sem código: {str(dados)[:120]}")
        codigo = str(codigo)
        if codigo.startswith("http"):
            return codigo
        return f"https://chat.whatsapp.com/{codigo}"

    def renomear_grupo(self, jid: str, nome: str) -> None:
        """
        PUT /api/{sessao}/groups/{id}/subject — só funciona como admin.

        `auth_em_403=False`: 403 aqui é "não sou admin DESTE grupo", não
        credencial inválida — subir como `auth` (fatal) desconectaria o número
        inteiro por causa de um grupo.
        """
        status, dados = self._pedir(
            "PUT", f"/api/{self.sessao}/groups/{validar_jid_de_grupo(jid)}/subject",
            {"subject": (nome or "").strip()[:100]},
            auth_em_403=False,
        )
        if status < 400:
            return
        detalhe = str(dados)[:150]
        d = detalhe.lower()
        if status == 403 or any(p in d for p in ("admin", "forbidden", "not authorized")):
            raise ErroWhatsapp("sem_permissao", detalhe)
        if any(p in d for p in ("not exist", "not found", "jid", "no longer")):
            raise ErroWhatsapp("grupo_invalido", detalhe)
        # 5xx / erro transitório NÃO é grupo inválido: classificar assim
        # desativaria grupos bons em lote (um passo, N grupos).
        raise ErroWhatsapp("acao", f"status {status}: {detalhe}")

    def remover_participante(self, jid_grupo: str, jid_participante: str) -> None:
        """
        POST /api/{sessao}/groups/{id}/participants/remove — só como admin.

        Usado pela blacklist quando um número bloqueado entra num grupo. Mesma
        classificação de erro do `renomear_grupo`: 403 é "não sou admin DESTE
        grupo", nunca credencial inválida — tratar como `auth` desconectaria o
        número inteiro por causa de um grupo em que ela não manda.
        """
        status, dados = self._pedir(
            "POST",
            f"/api/{self.sessao}/groups/{validar_jid_de_grupo(jid_grupo)}"
            "/participants/remove",
            {"participants": [{"id": jid_participante}]},
            auth_em_403=False,
        )
        if status < 400:
            return
        detalhe = str(dados)[:150]
        d = detalhe.lower()
        if status == 403 or any(p in d for p in ("admin", "forbidden", "not authorized")):
            raise ErroWhatsapp("sem_permissao", detalhe)
        if any(p in d for p in ("not exist", "not found", "jid", "no longer")):
            raise ErroWhatsapp("grupo_invalido", detalhe)
        raise ErroWhatsapp("acao", f"status {status}: {detalhe}")

    # --- envio -------------------------------------------------------------

    def _classificar_erro_envio(self, status: int, detalhe: str,
                                destino_grupo: bool) -> ErroWhatsapp:
        d = detalhe.lower()
        if destino_grupo and any(p in d for p in ("jid", "not exist", "group", "forbidden")):
            # Problema de UM grupo (fomos removidas, grupo apagado) — pula a
            # linha, nunca aborta o lote nem desliga a sessão.
            return ErroWhatsapp("grupo_invalido", detalhe)
        if any(p in d for p in ("not exist", "invalid number", "jid", "not registered")):
            return ErroWhatsapp("numero_invalido", detalhe)
        if any(p in d for p in ("not connected", "disconnect", "stopped", "scan_qr", "not working")):
            return ErroWhatsapp("desconectado", detalhe)
        return ErroWhatsapp("envio", f"status {status}: {detalhe}")

    def enviar_texto(self, chat_id: str, texto: str) -> Dict[str, Any]:
        """chat_id: '5511...@c.us' (DM) ou '12036...@g.us' (grupo)."""
        destino_grupo = chat_id.endswith("@g.us")
        if destino_grupo:
            validar_jid_de_grupo(chat_id)
        status, dados = self._pedir(
            "POST", "/api/sendText",
            {"session": self.sessao, "chatId": chat_id, "text": texto},
            auth_em_403=False,
        )
        if status >= 400:
            raise self._classificar_erro_envio(status, str(dados)[:200], destino_grupo)
        return dados if isinstance(dados, dict) else {}

    def enviar_imagem(self, chat_id: str, url_imagem: str, legenda: str = "",
                      mimetype: str = "image/jpeg",
                      nome_arquivo: str = "imagem.jpeg") -> Dict[str, Any]:
        destino_grupo = chat_id.endswith("@g.us")
        if destino_grupo:
            validar_jid_de_grupo(chat_id)
        status, dados = self._pedir(
            "POST", "/api/sendImage",
            {
                "session": self.sessao,
                "chatId": chat_id,
                "file": {"mimetype": mimetype, "url": url_imagem, "filename": nome_arquivo},
                "caption": legenda or None,
            },
            auth_em_403=False,
        )
        if status >= 400:
            raise self._classificar_erro_envio(status, str(dados)[:200], destino_grupo)
        return dados if isinstance(dados, dict) else {}
