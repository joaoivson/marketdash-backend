"""
Leads do Meta: o número que vira CPL na tela de Resultados.

Dois invariantes:
  * `lead` é o action_type AGREGADO — já contém pixel e on-site. Somar os três
    conta cada lead duas vezes e o CPL sai pela metade, na direção que faz
    anúncio ruim parecer bom.
  * ausência de `actions` é None ("configure o pixel"), nunca 0 ("ninguém
    virou lead") — são afirmações diferentes.
"""
import pytest

from app.services.facebook_integration_service import _leads_de


def _acao(tipo, valor):
    return {"action_type": tipo, "value": valor}


def test_nao_soma_agregado_com_especificas():
    """Se `lead` for o agregado do Meta, somar contaria cada lead duas vezes e
    o CPL sairia pela metade. `max` nunca infla."""
    ins = {"actions": [
        _acao("lead", "5"),
        _acao("offsite_conversion.fb_pixel_lead", "5"),   # provavelmente a MESMA
        _acao("onsite_conversion.lead_grouped", "0"),
    ]}
    assert _leads_de(ins) == 5


def test_especifica_maior_que_o_agregado_prevalece():
    """Hedge na outra direção: se o agregado vier menor que a soma das
    específicas, ficamos com o maior — nunca abaixo do sinal mais forte."""
    ins = {"actions": [
        _acao("lead", "2"),
        _acao("offsite_conversion.fb_pixel_lead", "7"),
    ]}
    assert _leads_de(ins) == 7


def test_sem_agregado_soma_as_especificas():
    ins = {"actions": [
        _acao("offsite_conversion.fb_pixel_lead", "3"),
        _acao("onsite_conversion.lead_grouped", "2"),
    ]}
    assert _leads_de(ins) == 5


def test_valor_fracionario_arredonda_em_vez_de_truncar():
    """Truncar por ação some com lead real: 0,6 + 0,6 viraria 0."""
    ins = {"actions": [
        _acao("offsite_conversion.fb_pixel_lead", "0.6"),
        _acao("onsite_conversion.lead_grouped", "0.6"),
    ]}
    assert _leads_de(ins) == 1


@pytest.mark.parametrize("ins", [
    {}, {"actions": None}, {"actions": []},
    {"actions": [_acao("link_click", "40"), _acao("post_engagement", "9")]},
])
def test_sem_conversao_reportada_e_none_nao_zero(ins):
    assert _leads_de(ins) is None


def test_conversao_reportada_com_zero_lead_e_zero_nao_none():
    """Aqui o pixel ESTÁ configurado e ninguém virou lead — a tela precisa
    mostrar 0, não 'configure o pixel'."""
    assert _leads_de({"actions": [_acao("lead", "0")]}) == 0


def test_acao_malformada_nao_derruba_a_leitura():
    ins = {"actions": ["lixo", None, _acao("lead", "abc"), _acao("lead", "2")]}
    assert _leads_de(ins) == 2
