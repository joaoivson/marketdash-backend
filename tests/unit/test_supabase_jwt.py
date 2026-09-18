"""Verificação local do JWT do Supabase (app/core/supabase_jwt.py).
Run: pytest tests/unit/test_supabase_jwt.py -v
"""
import time
from unittest.mock import patch

import pytest
from jose import jwt
from jose.backends import ECKey
from jose.constants import ALGORITHMS

from app.core import supabase_jwt


SEGREDO = "segredo-de-teste-com-tamanho-suficiente-para-hs256"


def _token_hs(claims: dict, segredo: str = SEGREDO, alg: str = "HS256"):
    return jwt.encode(claims, segredo, algorithm=alg)


def _claims(**extra):
    base = {
        "sub": "8d1c7c1e-0000-4000-8000-000000000001",
        "email": "maria@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
    }
    base.update(extra)
    return base


@pytest.fixture(autouse=True)
def _limpa_jwks():
    supabase_jwt._jwks.limpar()
    yield
    supabase_jwt._jwks.limpar()


def _settings(**kw):
    valores = {"SUPABASE_JWT_SECRET": None, "SUPABASE_JWKS_URL": None, "SUPABASE_URL": None}
    valores.update(kw)
    return patch.multiple(supabase_jwt.settings, **valores)


# ---- HS256 (formato legado) -------------------------------------------------

def test_hs256_valido_devolve_claims():
    with _settings(SUPABASE_JWT_SECRET=SEGREDO):
        claims = supabase_jwt.verificar_token(_token_hs(_claims()))
    assert claims["email"] == "maria@example.com"
    assert supabase_jwt.email_das_claims(claims) == "maria@example.com"


def test_hs256_assinatura_errada_rejeita():
    with _settings(SUPABASE_JWT_SECRET=SEGREDO):
        with pytest.raises(supabase_jwt.TokenInvalido):
            supabase_jwt.verificar_token(_token_hs(_claims(), segredo="outro-segredo-igualmente-longo-1234"))


def test_hs256_expirado_rejeita():
    with _settings(SUPABASE_JWT_SECRET=SEGREDO):
        with pytest.raises(supabase_jwt.TokenInvalido):
            supabase_jwt.verificar_token(_token_hs(_claims(exp=int(time.time()) - 120)))


def test_hs256_audiencia_errada_rejeita():
    """Token de service_role / anon não pode passar por usuária logada."""
    with _settings(SUPABASE_JWT_SECRET=SEGREDO):
        with pytest.raises(supabase_jwt.TokenInvalido):
            supabase_jwt.verificar_token(_token_hs(_claims(aud="anon", role="anon")))


def test_hs256_sem_segredo_configurado_pede_fallback():
    with _settings():
        with pytest.raises(supabase_jwt.TokenNaoVerificavelLocalmente):
            supabase_jwt.verificar_token(_token_hs(_claims()))


def test_token_malformado_rejeita():
    with _settings(SUPABASE_JWT_SECRET=SEGREDO):
        with pytest.raises(supabase_jwt.TokenInvalido):
            supabase_jwt.verificar_token("nao.e.jwt.valido")
        with pytest.raises(supabase_jwt.TokenInvalido):
            supabase_jwt.verificar_token("")


# ---- ES256 via JWKS (formato novo) -------------------------------------------

@pytest.fixture
def par_ec():
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    chave_priv = ECKey(priv, ALGORITHMS.ES256)
    jwk_pub = chave_priv.public_key().to_dict()
    jwk_pub["kid"] = "kid-teste-1"
    jwk_pub["alg"] = "ES256"
    return chave_priv, jwk_pub


def _token_es(claims, chave_priv, kid="kid-teste-1"):
    return jwt.encode(claims, chave_priv.to_dict(), algorithm="ES256", headers={"kid": kid})


def test_es256_valido_via_jwks(par_ec):
    chave_priv, jwk_pub = par_ec
    with _settings(SUPABASE_URL="https://abc.supabase.co"), \
         patch.object(supabase_jwt._CacheJWKS, "_baixar") as baixar:
        def _fake():
            supabase_jwt._jwks._chaves = {jwk_pub["kid"]: jwk_pub}
            supabase_jwt._jwks._baixado_em = time.monotonic()
        baixar.side_effect = _fake
        claims = supabase_jwt.verificar_token(_token_es(_claims(), chave_priv))
        assert supabase_jwt._jwks.url() == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
    assert claims["sub"].startswith("8d1c7c1e")
    assert baixar.call_count == 1


def test_es256_usa_cache_sem_rebaixar_jwks(par_ec):
    chave_priv, jwk_pub = par_ec
    with _settings(SUPABASE_JWKS_URL="https://abc.supabase.co/auth/v1/.well-known/jwks.json"), \
         patch.object(supabase_jwt._CacheJWKS, "_baixar") as baixar:
        def _fake():
            supabase_jwt._jwks._chaves = {jwk_pub["kid"]: jwk_pub}
            supabase_jwt._jwks._baixado_em = time.monotonic()
        baixar.side_effect = _fake
        supabase_jwt.verificar_token(_token_es(_claims(), chave_priv))
        supabase_jwt.verificar_token(_token_es(_claims(), chave_priv))
        supabase_jwt.verificar_token(_token_es(_claims(), chave_priv))
    assert baixar.call_count == 1, "cada requisição NÃO pode baixar o JWKS de novo"


def test_es256_kid_desconhecido_rejeita(par_ec):
    chave_priv, jwk_pub = par_ec
    with _settings(SUPABASE_URL="https://abc.supabase.co"), \
         patch.object(supabase_jwt._CacheJWKS, "_baixar") as baixar:
        baixar.side_effect = lambda: None
        with pytest.raises(supabase_jwt.TokenInvalido):
            supabase_jwt.verificar_token(_token_es(_claims(), chave_priv, kid="kid-invasor"))


def test_es256_sem_jwks_configurado_pede_fallback(par_ec):
    chave_priv, _ = par_ec
    with _settings():
        with pytest.raises(supabase_jwt.TokenNaoVerificavelLocalmente):
            supabase_jwt.verificar_token(_token_es(_claims(), chave_priv))


def test_alg_none_rejeita():
    """Ataque clássico: alg=none não pode passar."""
    import base64, json
    h = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    p = base64.urlsafe_b64encode(json.dumps(_claims()).encode()).rstrip(b"=").decode()
    with _settings(SUPABASE_JWT_SECRET=SEGREDO):
        with pytest.raises(supabase_jwt.TokenInvalido):
            supabase_jwt.verificar_token(f"{h}.{p}.")
