"""
Convenção de "ilimitado" no MAX: -1 em plan_limit(), sem mudar a assinatura
para Optional[int]. Consumidores precisam checar is_unlimited() antes de
comparar o valor numericamente (ver capture_site_service/custom_link_service).
"""
from app.core.plans import UNLIMITED, is_unlimited, plan_limit


def test_max_tem_links_e_paginas_captura_ilimitados():
    assert plan_limit("max", "links") == UNLIMITED
    assert plan_limit("max", "paginas_captura") == UNLIMITED


def test_essencial_tem_limites_zerados_e_nao_ilimitados():
    # 0 e -1 são coisas diferentes: o Essencial não libera nada, o MAX libera tudo.
    assert plan_limit("essencial", "links") == 0
    assert plan_limit("essencial", "paginas_captura") == 0
    assert not is_unlimited(plan_limit("essencial", "links"))


def test_recurso_desconhecido_cai_em_zero_e_nao_em_ilimitado():
    # Chaves removidas do mapa (ex.: a antiga "creditos_ia") caem no default 0,
    # nunca em -1 — remover um limite não pode liberar o recurso sem querer.
    assert plan_limit("max", "recurso_inexistente") == 0


def test_is_unlimited():
    assert is_unlimited(-1) is True
    assert is_unlimited(30) is False
    assert is_unlimited(0) is False


def test_pro_continua_com_limites_numericos():
    assert plan_limit("pro", "links") == 30
    assert plan_limit("pro", "paginas_captura") == 15
    assert not is_unlimited(plan_limit("pro", "links"))
