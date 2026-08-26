"""
Migração das chaves do Supabase — `anon`/`service_role` → `sb_publishable_…`
/`sb_secret_…`.

O que este arquivo protege:

  * o app resolve a chave certa com QUALQUER das duas formas presentes, para a
    rotação poder acontecer sem janela de indisponibilidade;
  * a chave nova tem precedência sobre a antiga;
  * **nenhum código volta a ler `settings.SUPABASE_KEY` direto** — foi assim que
    o `.env` novo derrubou a API: quatro variáveis desconhecidas e o boot parou.

O que NÃO precisou mudar, e o porquê: a validação de token é
`auth.get_user(token)`, uma chamada ao servidor do Supabase. Verificado contra o
projeto real em 26/08/2026 — as duas chaves autenticam e um token obtido com uma
é aceito pelo client criado com a outra. Como não há decodificação local de JWT,
a mudança do algoritmo para ES256 não exige nada do nosso lado.
"""
import pathlib

import pytest

from app.core.config import settings


@pytest.fixture
def limpo(monkeypatch):
    for k in ("SUPABASE_PUBLISHABLE_KEY", "SUPABASE_KEY",
              "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_KEY"):
        monkeypatch.setattr(settings, k, None, raising=False)
    return monkeypatch


def test_so_a_chave_nova_presente(limpo):
    limpo.setattr(settings, "SUPABASE_PUBLISHABLE_KEY", "sb_publishable_x", raising=False)
    limpo.setattr(settings, "SUPABASE_SECRET_KEY", "sb_secret_y", raising=False)
    assert settings.supabase_chave_publica == "sb_publishable_x"
    assert settings.supabase_chave_admin == "sb_secret_y"


def test_so_a_chave_antiga_presente(limpo):
    """Ambiente que ainda não rotacionou continua funcionando."""
    limpo.setattr(settings, "SUPABASE_KEY", "anon-antiga", raising=False)
    limpo.setattr(settings, "SUPABASE_SERVICE_KEY", "service-antiga", raising=False)
    assert settings.supabase_chave_publica == "anon-antiga"
    assert settings.supabase_chave_admin == "service-antiga"


def test_com_as_duas_a_nova_manda(limpo):
    """Durante a rotação as duas convivem no `.env`; a nova é a que vale."""
    limpo.setattr(settings, "SUPABASE_PUBLISHABLE_KEY", "sb_publishable_x", raising=False)
    limpo.setattr(settings, "SUPABASE_KEY", "anon-antiga", raising=False)
    limpo.setattr(settings, "SUPABASE_SECRET_KEY", "sb_secret_y", raising=False)
    limpo.setattr(settings, "SUPABASE_SERVICE_KEY", "service-antiga", raising=False)
    assert settings.supabase_chave_publica == "sb_publishable_x"
    assert settings.supabase_chave_admin == "sb_secret_y"


def test_sem_nenhuma_e_none_para_o_chamador_tratar(limpo):
    assert settings.supabase_chave_publica is None
    assert settings.supabase_chave_admin is None


def test_a_publica_nunca_devolve_a_chave_de_admin(limpo):
    """Trocar as duas manda a chave que ignora RLS para o caminho comum."""
    limpo.setattr(settings, "SUPABASE_SECRET_KEY", "sb_secret_y", raising=False)
    assert settings.supabase_chave_publica is None


APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def test_ninguem_le_as_chaves_antigas_direto():
    """
    O acesso passa pelas propriedades, senão um ambiente já rotacionado (só com
    as chaves novas no `.env`) quebra no ponto que ficou para trás — e quebra
    como 401, que é o erro mais caro de diagnosticar.
    """
    infratores = []
    for caminho in APP.rglob("*.py"):
        if caminho.name == "config.py":
            continue                      # é onde as chaves são declaradas
        texto = caminho.read_text(encoding="utf-8")
        for atributo in ("settings.SUPABASE_KEY", "settings.SUPABASE_SERVICE_KEY"):
            if atributo in texto:
                infratores.append(f"{caminho.relative_to(APP)}: {atributo}")
    assert not infratores, "use settings.supabase_chave_publica/admin: " + "; ".join(infratores)


def test_env_desconhecida_nao_derruba_o_boot():
    """
    O `.env` ganhou quatro variáveis na migração e a API parou de subir — o
    padrão do pydantic-settings é RECUSAR variável não declarada. Em produção
    isso vira crash-loop a cada env acrescentada no Coolify.
    """
    from app.core.config import Settings

    assert getattr(Settings.Config, "extra", None) == "ignore"
