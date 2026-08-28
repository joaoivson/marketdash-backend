"""
O pool de proxies: afinidade, vaga, recusa e cooldown.

Os invariantes que este arquivo protege existem porque cada um deles, quebrado,
produz o MESMO sintoma final — número banido — por caminhos diferentes:

  * afinidade: chip de duas afiliadas no mesmo IP faz um banimento contaminar
    a vizinhança;
  * `max_sessoes`: IP com mais chips do que uma pessoa teria aparelhos é
    retrato de automação;
  * recusa com pool cheio: criar sessão sem proxy em produção é justamente o
    que o módulo existe para impedir;
  * cooldown: trocar de IP é o sinal mais óbvio de conta automatizada, então
    trocar duas vezes seguidas é pior que não trocar.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.models.whatsapp_proxies import (
    PROXY_OK, PROXY_QUARENTENA, WhatsappProxy,
)
from app.services import proxy_pool_service
from app.services.waha_client import ErroWhatsapp


def _proxy(id_, tipo="residencial", max_sessoes=3, status=PROXY_OK, ativo=True):
    p = WhatsappProxy(rotulo=f"BR-{id_}", tipo=tipo, host=f"10.0.0.{id_}",
                      porta=8000 + id_, pais="BR", max_sessoes=max_sessoes,
                      ativo=ativo, status=status)
    p.id = id_
    return p


class _FakeRepo:
    """Espelha o contrato do WhatsappProxyRepository sem tocar o banco."""

    def __init__(self, proxies, ocupacao=None, usuarias=None):
        self._proxies = proxies
        self._ocupacao = ocupacao or {}
        self._usuarias = usuarias or {}
        self.salvos = []

    def listar(self, ativos_apenas=False):
        return [p for p in self._proxies if p.ativo or not ativos_apenas]

    def por_id(self, proxy_id):
        return next((p for p in self._proxies if p.id == proxy_id), None)

    def contagem_de_sessoes(self):
        return dict(self._ocupacao)

    def usuarias_por_proxy(self):
        return {k: set(v) for k, v in self._usuarias.items()}

    def instancias_do_proxy(self, proxy_id):
        return []

    def salvar(self, proxy):
        self.salvos.append(proxy)
        return proxy


@pytest.fixture(autouse=True)
def pool_ligado(monkeypatch):
    """O módulo nasce DESLIGADO (feature flag). Sem isto todo teste aqui
    exercitaria o caminho 'return None'."""
    monkeypatch.setattr(proxy_pool_service, "habilitado", lambda: True)


def _instancia(user_id=1, **kw):
    base = dict(id=kw.pop("id", 10), user_id=user_id, nome_instancia="mkdaaau1xbbbb",
                proxy_id=None, proxy_fixado_em=None, proxy_trocas=0)
    base.update(kw)
    return SimpleNamespace(**base)


def _com_repo(monkeypatch, repo):
    monkeypatch.setattr(proxy_pool_service, "WhatsappProxyRepository",
                        lambda db: repo)


def test_chips_da_mesma_usuaria_compartilham_o_mesmo_ip(monkeypatch):
    """Três aparelhos na mesma casa é retrato coerente — e derruba o custo de
    3 IPs por afiliada para 1."""
    repo = _FakeRepo([_proxy(1), _proxy(2)], ocupacao={1: 1}, usuarias={1: {7}})
    _com_repo(monkeypatch, repo)
    escolhido = proxy_pool_service.alocar(None, _instancia(user_id=7))
    assert escolhido.id == 1


def test_chip_de_outra_usuaria_nunca_entra_num_ip_ja_ocupado(monkeypatch):
    """O caso que realmente importa: um banimento não pode contaminar a
    vizinhança de outra afiliada."""
    repo = _FakeRepo([_proxy(1), _proxy(2)], ocupacao={1: 1}, usuarias={1: {7}})
    _com_repo(monkeypatch, repo)
    escolhido = proxy_pool_service.alocar(None, _instancia(user_id=99))
    assert escolhido.id == 2


def test_proxy_cheio_nao_recebe_nem_da_propria_usuaria(monkeypatch):
    repo = _FakeRepo([_proxy(1, max_sessoes=2), _proxy(2)],
                     ocupacao={1: 2}, usuarias={1: {7}})
    _com_repo(monkeypatch, repo)
    assert proxy_pool_service.alocar(None, _instancia(user_id=7)).id == 2


def test_movel_ganha_de_residencial_e_datacenter(monkeypatch):
    """Datacenter é reconhecido e queimado; só deve entrar quando é o que há."""
    repo = _FakeRepo([_proxy(1, tipo="datacenter"), _proxy(2, tipo="residencial"),
                      _proxy(3, tipo="movel")])
    _com_repo(monkeypatch, repo)
    assert proxy_pool_service.alocar(None, _instancia(user_id=7)).id == 3


def test_proxy_em_quarentena_nao_e_alocado(monkeypatch):
    repo = _FakeRepo([_proxy(1, status=PROXY_QUARENTENA), _proxy(2)])
    _com_repo(monkeypatch, repo)
    assert proxy_pool_service.alocar(None, _instancia()).id == 2


def test_proxy_desativado_nao_e_alocado(monkeypatch):
    repo = _FakeRepo([_proxy(1, ativo=False), _proxy(2)])
    _com_repo(monkeypatch, repo)
    assert proxy_pool_service.alocar(None, _instancia()).id == 2


def test_pool_cheio_com_proxy_obrigatorio_recusa_a_criacao(monkeypatch):
    """Em produção, sessão sem proxy é pior do que sessão nenhuma: ela nasce
    no IP do servidor, junto com todas as outras."""
    monkeypatch.setattr(settings, "WHATSAPP_PROXY_OBRIGATORIO", True)
    repo = _FakeRepo([_proxy(1, max_sessoes=1)], ocupacao={1: 1}, usuarias={1: {7}})
    _com_repo(monkeypatch, repo)
    with pytest.raises(ErroWhatsapp) as e:
        proxy_pool_service.alocar(None, _instancia(user_id=99))
    assert e.value.motivo == "sem_proxy"


def test_pool_cheio_sem_obrigatoriedade_cria_sem_proxy(monkeypatch):
    """Local/hml: seguir sem proxy é aceitável — mas nunca em silêncio."""
    monkeypatch.setattr(settings, "WHATSAPP_PROXY_OBRIGATORIO", False)
    repo = _FakeRepo([])
    _com_repo(monkeypatch, repo)
    assert proxy_pool_service.alocar(None, _instancia()) is None


def test_liberar_devolve_a_vaga(monkeypatch):
    inst = _instancia(proxy_id=1, proxy_fixado_em=datetime.now(timezone.utc))
    proxy_pool_service.liberar(None, inst)
    assert inst.proxy_id is None and inst.proxy_fixado_em is None


def test_cooldown_bloqueia_troca_precoce(monkeypatch):
    """Trocar de IP duas vezes na mesma semana é mais suspeito que o IP ruim."""
    monkeypatch.setattr(settings, "WHATSAPP_PROXY_COOLDOWN_H", 24)
    inst = _instancia(proxy_id=1, proxy_trocas=1,
                      proxy_fixado_em=datetime.now(timezone.utc) - timedelta(hours=2))
    assert proxy_pool_service.em_cooldown(inst) is True
    with pytest.raises(proxy_pool_service.TrocaEmCooldown):
        proxy_pool_service.realocar(None, inst, motivo="teste")


def test_cooldown_nao_prende_chip_que_nunca_trocou(monkeypatch):
    """`proxy_fixado_em` também é carimbado na CRIAÇÃO. Contar isso como troca
    prenderia por 24h um número recém-criado num IP que já nasceu ruim."""
    inst = _instancia(proxy_id=1, proxy_trocas=0,
                      proxy_fixado_em=datetime.now(timezone.utc))
    assert proxy_pool_service.em_cooldown(inst) is False


class _DbFake:
    def flush(self):
        pass


def test_realocar_troca_de_ip_e_conta_a_troca(monkeypatch):
    """O cenário real: o IP atual caiu em quarentena, então a afinidade com ele
    não vale mais e o chip vai para outro IP."""
    repo = _FakeRepo([_proxy(1, status=PROXY_QUARENTENA), _proxy(2)],
                     ocupacao={1: 1}, usuarias={1: {7}})
    _com_repo(monkeypatch, repo)
    inst = _instancia(user_id=7, proxy_id=1, proxy_trocas=0,
                      proxy_fixado_em=datetime.now(timezone.utc) - timedelta(days=9))
    novo = proxy_pool_service.realocar(_DbFake(), inst, motivo="quarentena")
    assert novo.id == 2
    assert inst.proxy_id == 2 and inst.proxy_trocas == 1


def test_realocar_sem_destino_mantem_o_ip_atual(monkeypatch):
    """Sem outro IP com vaga, ficar onde está é melhor que ficar sem proxy —
    e o `proxy_id` original não pode se perder no caminho."""
    repo = _FakeRepo([_proxy(1)], ocupacao={}, usuarias={1: {7}})
    _com_repo(monkeypatch, repo)
    inst = _instancia(user_id=7, proxy_id=1, proxy_trocas=0,
                      proxy_fixado_em=datetime.now(timezone.utc) - timedelta(days=9))
    assert proxy_pool_service.realocar(_DbFake(), inst, motivo="x") is None
    assert inst.proxy_id == 1 and inst.proxy_trocas == 0


def test_escada_de_saude_degrada_e_poe_em_quarentena(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_PROXY_FALHAS_DEGRADADO", 2)
    monkeypatch.setattr(settings, "WHATSAPP_PROXY_FALHAS_QUARENTENA", 4)
    repo = _FakeRepo([])
    _com_repo(monkeypatch, repo)
    p = _proxy(1)
    p.falhas_seguidas = 0
    assert proxy_pool_service.registrar_falha(None, p, "timeout") == PROXY_OK
    assert proxy_pool_service.registrar_falha(None, p, "timeout") == "degradado"
    proxy_pool_service.registrar_falha(None, p, "timeout")
    assert proxy_pool_service.registrar_falha(None, p, "timeout") == PROXY_QUARENTENA
    proxy_pool_service.registrar_sucesso(None, p, ip="1.2.3.4", pais="BR")
    assert (p.status, p.falhas_seguidas, p.ultimo_ip) == (PROXY_OK, 0, "1.2.3.4")


def test_credenciais_montam_server_sem_esquema(monkeypatch):
    """O WAHA exige `host:porta` — `http://host:porta` é recusado."""
    monkeypatch.setattr("app.services.proxy_pool_service.decrypt_value",
                        lambda v: v.replace("cif:", ""))
    p = _proxy(1)
    p.usuario_cifrado, p.senha_cifrada = "cif:u", "cif:s"
    assert proxy_pool_service.credenciais(p) == {
        "server": "10.0.0.1:8001", "username": "u", "password": "s",
    }
    assert proxy_pool_service.credenciais(None) is None


def test_modulo_desligado_nao_aloca(monkeypatch):
    """A flag desliga o módulo inteiro: sem ela, ligar o código em produção
    sem pool cadastrado travaria a criação de qualquer número."""
    monkeypatch.setattr(proxy_pool_service, "habilitado", lambda: False)
    assert proxy_pool_service.alocar(None, _instancia()) is None
