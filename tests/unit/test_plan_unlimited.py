"""
Convenção de "ilimitado" no MAX: -1 em plan_limit(), sem mudar a assinatura
para Optional[int]. Consumidores precisam checar is_unlimited() antes de
comparar o valor numericamente (ver capture_site_service/custom_link_service).
"""
from app.core.plans import UNLIMITED, is_unlimited, plan_limit


def test_max_tem_links_e_paginas_captura_ilimitados():
    assert plan_limit("max", "links") == UNLIMITED
    assert plan_limit("max", "paginas_captura") == UNLIMITED


def test_max_creditos_ia_continua_numerico():
    # Créditos de IA do MAX são feature futura — não vira ilimitado agora.
    assert plan_limit("max", "creditos_ia") == 1000


def test_is_unlimited():
    assert is_unlimited(-1) is True
    assert is_unlimited(30) is False
    assert is_unlimited(0) is False


def test_pro_continua_com_limites_numericos():
    assert plan_limit("pro", "links") == 30
    assert plan_limit("pro", "paginas_captura") == 15
    assert not is_unlimited(plan_limit("pro", "links"))
