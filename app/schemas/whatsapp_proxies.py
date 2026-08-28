"""Schemas do pool de proxies (admin).

REGRA DURA: **nenhuma resposta carrega usuário ou senha do proxy.** Nem
mascarados, nem "só os 3 primeiros". A tela do admin precisa saber qual IP é,
como ele está e quantas sessões estão nele — nada disso exige a credencial, e
uma resposta que a carrega vaza no log do navegador, no HAR do suporte e no
print que alguém cola no grupo.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ProxyCriar(BaseModel):
    rotulo: str = Field(min_length=1, max_length=80)
    tipo: str = Field(default="residencial")     # residencial | movel | datacenter
    host: str = Field(min_length=1, max_length=255)
    porta: int = Field(ge=1, le=65535)
    usuario: Optional[str] = Field(default=None, max_length=255)
    senha: Optional[str] = Field(default=None, max_length=255)
    pais: str = Field(default="BR", min_length=2, max_length=2)
    max_sessoes: Optional[int] = Field(default=None, ge=1, le=50)


class ProxyAtualizar(BaseModel):
    """Tudo opcional (PATCH). `usuario`/`senha` ausentes = mantém o que está
    gravado; string vazia = limpa (proxy que deixou de exigir autenticação)."""
    rotulo: Optional[str] = Field(default=None, min_length=1, max_length=80)
    tipo: Optional[str] = None
    host: Optional[str] = Field(default=None, min_length=1, max_length=255)
    porta: Optional[int] = Field(default=None, ge=1, le=65535)
    usuario: Optional[str] = Field(default=None, max_length=255)
    senha: Optional[str] = Field(default=None, max_length=255)
    pais: Optional[str] = Field(default=None, min_length=2, max_length=2)
    max_sessoes: Optional[int] = Field(default=None, ge=1, le=50)
    ativo: Optional[bool] = None
    # Tirar da quarentena depois de resolver o problema com o fornecedor.
    reativar_status: Optional[bool] = None


class ProxyOut(BaseModel):
    id: int
    rotulo: str
    tipo: str
    # `host:porta` sem credencial: identifica o IP para o admin sem entregar o
    # acesso a ele.
    servidor: str
    pais: str
    max_sessoes: int
    ocupacao: int
    ativo: bool
    status: str                     # ok | degradado | quarentena
    falhas_seguidas: int
    tem_credencial: bool            # só o FATO, nunca o valor
    ultimo_erro: Optional[str]
    ultimo_ip: Optional[str]
    ultimo_pais: Optional[str]
    verificado_em: Optional[datetime]
    # Quantas afiliadas distintas estão neste IP. Deve ser 0 ou 1 — 2 significa
    # que a regra de afinidade foi furada e um banimento contamina a vizinhança.
    usuarias: int
    criado_em: datetime


class ProxyVerificarOut(BaseModel):
    ok: bool
    ip: Optional[str] = None
    pais: Optional[str] = None
    detalhe: str
    status: str


class InstanciaProxyOut(BaseModel):
    """A sessão vista pelo admin — do ângulo do IP, não do da afiliada."""
    id: int
    user_id: int
    user_email: Optional[str]
    nome_exibicao: Optional[str]
    numero_mascarado: Optional[str]
    status: str
    proxy_id: Optional[int]
    proxy_rotulo: Optional[str]
    proxy_status: Optional[str]
    proxy_fixado_em: Optional[datetime]
    proxy_trocas: int
    em_cooldown: bool


class RealocarProxyIn(BaseModel):
    motivo: str = Field(min_length=3, max_length=200)
    # Cooldown existe justamente para tornar a troca rara; furá-lo é decisão
    # consciente do admin, nunca default.
    ignorar_cooldown: bool = False
    # Aplicar na sessão = stop → PUT → start. Pode exigir novo QR (não
    # confirmado — spike §1), então é escolha explícita de quem clica.
    aplicar_na_sessao: bool = False


class RealocarProxyOut(BaseModel):
    proxy_id: Optional[int]
    proxy_rotulo: Optional[str]
    aplicado_na_sessao: bool
    aviso: Optional[str] = None


class PoolOut(BaseModel):
    proxies: List[ProxyOut]
    instancias: List[InstanciaProxyOut]
    # O módulo pode estar cadastrado e DESLIGADO — sem isto a tela mostraria um
    # pool bonito que não é usado por sessão nenhuma.
    ligado: bool
    obrigatorio: bool
