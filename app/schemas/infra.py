"""
Contrato do painel de infraestrutura (`GET /admin/infra`).

Existe por dois motivos além da regra de camadas. Primeiro, é aqui que o
frontend lê o que pode aparecer na tela — o painel tem cinco blocos e nenhum
deles é obrigatório. Segundo, e mais importante: **todo bloco tem `erro` e
quase todo campo é opcional de propósito**. Um painel de status que devolve
500 quando o Coolify está fora do ar é inútil justamente na hora em que ele
seria usado; o desenho é entregar o resto e dizer, no lugar do dado, o que
falhou.
"""
from typing import Any, Optional

from pydantic import BaseModel


class ServidorOut(BaseModel):
    """O host — VPS único onde roda produção E homologação (ver o bloco 23 de
    `STATUS-login-producao-fora.md`: separar os dois foi adiado por custo)."""

    nome: Optional[str] = None
    ip: Optional[str] = None
    alcancavel: Optional[bool] = None
    utilizavel: Optional[bool] = None
    eh_host_coolify: Optional[bool] = None
    proxy_tipo: Optional[str] = None
    proxy_status: Optional[str] = None
    traefik_versao: Optional[str] = None
    #: Sentinel parado = a métrica que a UI do Coolify mostra está congelada.
    sentinel_em: Optional[str] = None
    #: Quantos builds o servidor deixa rodar juntos. Cinco builds simultâneos
    #: foram o gatilho do apagão de 11/09; o CI serializa por fora, mas este
    #: número diz o que o servidor permitiria se a serialização saísse.
    builds_simultaneos: Optional[int] = None
    fila_de_deploy_limite: Optional[int] = None
    alerta_disco_pct: Optional[int] = None
    disco_cheio_avisado: Optional[bool] = None


class RecursoOut(BaseModel):
    """Um container. `status` é o campo do Coolify (`running:healthy`), e
    `estado`/`saude` são ele partido em dois — `running:unknown` é o normal de
    worker sem healthcheck, não um problema."""

    uuid: Optional[str] = None
    rotulo: str
    ambiente: str
    papel: str
    tipo: str
    nome_coolify: Optional[str] = None
    status: Optional[str] = None
    estado: str
    saude: Optional[str] = None
    #: `None` = sem teto. Os tetos de hml nasceram em 12/09 para que um worker
    #: de homologação não possa mais roubar a CPU de produção.
    limite_cpu: Optional[str] = None
    limite_memoria: Optional[str] = None
    reinicios: Optional[int] = None
    reiniciado_em: Optional[str] = None
    online_em: Optional[str] = None
    branch: Optional[str] = None
    commit: Optional[str] = None
    fqdn: Optional[str] = None
    healthcheck_ligado: Optional[bool] = None
    atualizado_em: Optional[str] = None
    #: Onde a nossa medição discorda do Coolify — nos dois sentidos: verde do
    #: Coolify com URL pública fora (o caso de 11/09) e vermelho do Coolify
    #: com o serviço respondendo (o caso das duas instâncias de Redis, visto
    #: em 15/09). `None` é o normal: as duas fontes concordam.
    contradicao: Optional[str] = None


class DeployOut(BaseModel):
    aplicacao: Optional[str] = None
    status: Optional[str] = None
    commit: Optional[str] = None


class CoolifyOut(BaseModel):
    configurado: bool
    erro: Optional[str] = None
    #: O que fazer para o bloco existir. Preenchido só quando falta o token.
    instrucao: Optional[str] = None
    url: str
    versao: Optional[str] = None
    servidor: Optional[ServidorOut] = None
    recursos: list[RecursoOut] = []
    #: Deploy em andamento AGORA. Lista vazia é o estado normal.
    fila_de_deploy: list[DeployOut] = []


class HostingerOut(BaseModel):
    configurado: bool
    erro: Optional[str] = None
    instrucao: Optional[str] = None
    vps: Optional[dict[str, Any]] = None
    #: Forma tolerante de propósito: é o único bloco que não pôde ser
    #: exercitado contra a API real (não há token ainda). Formato diferente do
    #: esperado chega como `{"formato_inesperado": true}` em vez de derrubar
    #: a resposta inteira na validação.
    metricas: Optional[dict[str, Any]] = None


class PontaOut(BaseModel):
    """`GET` na URL pública. O bloco que pega o modo de falha de 11/09:
    container de pé, rota do Traefik perdida, usuária sem acesso."""

    rotulo: str
    ambiente: str
    url: str
    tipo: str
    http: Optional[int] = None
    #: `ok` NÃO é `http == 200`: é a nossa resposta reconhecida no corpo.
    ok: bool
    detalhe: Optional[str] = None
    latencia_ms: Optional[int] = None
    #: O que o `/health` diz de dentro da app (`database`, `redis`). Só nas
    #: pontas de API. Container no ar com Redis caído aceita upload e nunca
    #: processa — e só este campo mostra isso.
    saude_interna: Optional[dict[str, str]] = None


class FilaOut(BaseModel):
    nome: str
    tamanho: int


class FilasOut(BaseModel):
    configurado: bool
    erro: Optional[str] = None
    #: Qual ambiente é ESTA api — o Redis é compartilhado, então o painel
    #: mostra as filas dos dois e é preciso saber quem está respondendo.
    ambiente_desta_api: str
    ping_ms: Optional[float] = None
    filas: list[FilaOut] = []


class InfraOut(BaseModel):
    gerado_em: str
    #: Constante `True`. Está na resposta para o painel poder dizer na tela que
    #: não há nada a apertar aqui — restart e deploy seguem no Coolify.
    somente_leitura: bool
    coolify: CoolifyOut
    hostinger: HostingerOut
    pontas: list[PontaOut]
    filas: FilasOut
