"""
Feature flags compartilhadas entre backend e frontend.

Le o arquivo feature-flags.json da raiz do monorepo.
O caminho pode ser sobreescrito pela env var FEATURE_FLAGS_PATH.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_config: Optional[dict] = None


def _load_config() -> dict:
    """Carrega feature-flags.json uma vez e cacheia em memória."""
    global _config
    if _config is not None:
        return _config

    # Ordem de busca:
    # 1. Env var FEATURE_FLAGS_PATH
    # 2. /app/feature-flags.json (Docker mount)
    # 3. ../../feature-flags.json (dev local, relativo ao backend)
    candidates = []

    env_path = os.environ.get("FEATURE_FLAGS_PATH")
    if env_path:
        candidates.append(Path(env_path))

    candidates.append(Path("/app/feature-flags.json"))
    candidates.append(Path(__file__).resolve().parent.parent.parent / "feature-flags.json")
    candidates.append(Path(__file__).resolve().parent.parent.parent.parent / "feature-flags.json")

    for path in candidates:
        if path.is_file():
            try:
                _config = json.loads(path.read_text(encoding="utf-8"))
                logger.info(f"Feature flags carregadas de {path}")
                return _config
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Erro ao ler feature flags de {path}: {e}")

    logger.warning("feature-flags.json nao encontrado. Usando defaults (cakto).")
    _config = {"payment_provider": "cakto"}
    return _config


# Formatos do direct da automação de Instagram.
DM_FORMATO_BOTAO = "botao"
DM_FORMATO_TEXTO = "texto"


def instagram_dm_formato() -> str:
    """Como o direct sai: `botao` (template da Meta) ou `texto` (link inline).

    A env var vem PRIMEIRO de propósito. O pedido era poder voltar ao formato
    antigo em produção "sem redeploy grande": mudar INSTAGRAM_DM_FORMATO=texto
    no Coolify e reiniciar resolve, sem rebuild de imagem nem commit. O
    feature-flags.json fica como default versionado.
    """
    do_ambiente = (os.environ.get("INSTAGRAM_DM_FORMATO") or "").strip().lower()
    if do_ambiente in (DM_FORMATO_BOTAO, DM_FORMATO_TEXTO):
        return do_ambiente
    do_arquivo = str(_load_config().get("instagram_dm_formato", DM_FORMATO_BOTAO)).lower()
    return do_arquivo if do_arquivo in (DM_FORMATO_BOTAO, DM_FORMATO_TEXTO) else DM_FORMATO_BOTAO


def dm_com_botao() -> bool:
    return instagram_dm_formato() == DM_FORMATO_BOTAO


# --- Proxy por sessão no WAHA (anti-banimento) -------------------------------

def whatsapp_proxy_ligado() -> bool:
    """Liga a alocação de proxy nas sessões do WhatsApp.

    Mesma ordem de precedência do formato do direct: a **env var vem primeiro**
    para que dê para desligar em produção sem rebuild de imagem
    (`WHATSAPP_PROXY_LIGADO=false` no Coolify + restart). O
    `feature-flags.json` fica como default versionado.

    Desligado por padrão: sem proxy cadastrado no pool, ligar isto com
    `WHATSAPP_PROXY_OBRIGATORIO=true` impediria a criação de qualquer número.
    """
    do_ambiente = (os.environ.get("WHATSAPP_PROXY_LIGADO") or "").strip().lower()
    if do_ambiente in ("1", "true", "sim", "on"):
        return True
    if do_ambiente in ("0", "false", "nao", "não", "off"):
        return False
    return bool(_load_config().get("whatsapp_proxy", False))


def get_payment_provider() -> str:
    """Retorna o provider ativo: 'cakto' ou 'kiwify'."""
    return _load_config().get("payment_provider", "cakto")


def is_kiwify() -> bool:
    return get_payment_provider() == "kiwify"


def is_cakto() -> bool:
    return get_payment_provider() == "cakto"


# --- Módulos em beta (§ Subida para produção) --------------------------------
#
# O layout novo de Configurações sobe inteiro; o que ainda não está liberado
# some por FLAG, não por código comentado nem por `isProductionHost()` no
# frontend. Duas diferenças que motivaram a troca:
#
# 1. O gate por hostname é build-time no frontend — liberar em beta exigia
#    rebuild + redeploy. Aqui é runtime: env var no Coolify + restart.
# 2. Dá para liberar por plano ou por conta, mantendo o módulo visível nas
#    contas de teste enquanto some para o resto da produção.

MODULO_GRUPOS_WHATSAPP = "grupos_whatsapp"


def _modulos_do_ambiente() -> Optional[set]:
    """`MODULOS_BETA` (csv) força a lista inteira, ignorando o arquivo.

    Vem PRIMEIRO pelo mesmo motivo das flags acima: liberar/recolher um módulo
    em produção sem rebuild de imagem. `MODULOS_BETA=""` (definida e vazia)
    fecha tudo — diferente de não definida, que cai no arquivo.
    """
    bruto = os.environ.get("MODULOS_BETA")
    if bruto is None:
        return None
    return {parte.strip().lower() for parte in bruto.split(",") if parte.strip()}


def modulos_beta_liberados(plano: Optional[str] = None,
                           email: Optional[str] = None) -> set:
    """Módulos em beta visíveis para esta conta.

    Formato no `feature-flags.json`:

        "modulos_beta": {
          "grupos_whatsapp": {
            "liberado": false,          # true = todo mundo vê
            "planos": ["max"],          # ou só quem está nesses planos
            "emails": ["a@b.com"]       # ou estas contas nominais
          }
        }

    Ausente = fechado. É o default de propósito: módulo novo nasce invisível.
    """
    forcados = _modulos_do_ambiente()
    if forcados is not None:
        return forcados

    config = _load_config().get("modulos_beta") or {}
    if not isinstance(config, dict):
        return set()

    plano_n = (plano or "").strip().lower()
    email_n = (email or "").strip().lower()
    liberados = set()
    for nome, regra in config.items():
        # Atalho: `"grupos_whatsapp": true` vale como {"liberado": true}.
        if regra is True:
            liberados.add(nome)
            continue
        if not isinstance(regra, dict):
            continue
        if regra.get("liberado"):
            liberados.add(nome)
            continue
        planos = {str(p).lower() for p in (regra.get("planos") or [])}
        if plano_n and plano_n in planos:
            liberados.add(nome)
            continue
        emails = {str(e).lower() for e in (regra.get("emails") or [])}
        if email_n and email_n in emails:
            liberados.add(nome)
    return liberados
