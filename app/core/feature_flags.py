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


def get_payment_provider() -> str:
    """Retorna o provider ativo: 'cakto' ou 'kiwify'."""
    return _load_config().get("payment_provider", "cakto")


def is_kiwify() -> bool:
    return get_payment_provider() == "kiwify"


def is_cakto() -> bool:
    return get_payment_provider() == "cakto"
