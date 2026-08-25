"""
Opt-in e lote diário.

Dois invariantes carregam esta feature:
  1. mensagem só sai para quem confirmou o número;
  2. o lote nunca vira rajada nem insiste num número que está fora do ar —
     é o que separa "automação" de "número banido".
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.models.whatsapp import (
    ENVIO_OK, ORIGEM_FALHA, STATUS_CONFIRMADO, STATUS_DESLIGADO,
    STATUS_PENDENTE, TIPO_RESUMO,
)
from app.services.waha_client import ErroWhatsapp
from app.services.whatsapp_envio_service import WhatsappEnvioService
from app.services.whatsapp_optin_service import (
    CodigoInvalido, TentativasEsgotadas, WhatsappIndisponivel, WhatsappOptinService,
)

ONTEM = date(2026, 8, 6)
HOJE = date(2026, 8, 7)


class _FakeRepo:
    def __init__(self, optins=None):
        self.optins = {o.user_id: o for o in (optins or [])}
        self.envios = []

    def por_usuario(self, user_id):
        return self.optins.get(user_id)

    def por_numero(self, numero):
        return [o for o in self.optins.values() if o.numero == numero]

    def salvar(self, optin):
        self.optins[optin.user_id] = optin
        return optin

    def confirmados_dos_planos(self, planos):
        return [(o, "pro") for o in self.optins.values() if o.status == STATUS_CONFIRMADO]

    def ja_enviou(self, user_id, tipo, referencia):
        return any(e for e in self.envios
                   if e["user_id"] == user_id and e["tipo"] == tipo
                   and e["referencia"] == referencia and e["status"] == ENVIO_OK)

    def registrar_envio(self, user_id, tipo, status, referencia=None, erro=None):
        if status == ENVIO_OK and referencia and self.ja_enviou(user_id, tipo, referencia):
            return False   # espelha o índice único
        self.envios.append({"user_id": user_id, "tipo": tipo, "status": status,
                            "referencia": referencia, "erro": erro})
        return True

    def enviados_no_dia(self, dia):
        return len([e for e in self.envios if e["status"] == ENVIO_OK])


class _FakeCliente:
    def __init__(self, erro=None, conectado=True, config=True):
        self.erro = erro
        self._conectado = conectado
        self._config = config
        self.enviadas = []

    def configurado(self):
        return self._config

    def conectado(self):
        return self._conectado

    def enviar_texto(self, chat_id, texto):
        if self.erro:
            raise self.erro
        self.enviadas.append((chat_id, texto))
        return {"ok": True}


def _optin(user_id=1, status=STATUS_CONFIRMADO, numero="5511999998888", **extra):
    base = dict(user_id=user_id, numero=numero, status=status, codigo=None,
                codigo_expira_em=None, tentativas=0, confirmado_em=None,
                desligado_em=None, desligado_por=None, atualizado_em=None)
    base.update(extra)
    return SimpleNamespace(**base)


# --- opt-in -----------------------------------------------------------------

def test_registrar_manda_codigo_e_deixa_pendente():
    repo, cli = _FakeRepo(), _FakeCliente()
    WhatsappOptinService(repo, cli).registrar(1, "(11) 99999-8888")

    assert repo.optins[1].status == STATUS_PENDENTE
    assert repo.optins[1].numero == "5511999998888"
    chat_id, texto = cli.enviadas[0]
    assert chat_id == "5511999998888@c.us"   # WAHA fala em chatId, não em número cru
    assert repo.optins[1].codigo in texto


def test_codigo_que_nao_sai_nao_deixa_optin_pendente_orfao():
    repo, cli = _FakeRepo(), _FakeCliente(erro=ErroWhatsapp("numero_invalido"))
    with pytest.raises(ErroWhatsapp):
        WhatsappOptinService(repo, cli).registrar(1, "11999998888")
    assert repo.por_usuario(1) is None


def test_sem_waha_configurado_nem_tenta():
    repo, cli = _FakeRepo(), _FakeCliente(config=False)
    with pytest.raises(WhatsappIndisponivel):
        WhatsappOptinService(repo, cli).registrar(1, "11999998888")
    assert cli.enviadas == []


def test_confirmar_com_codigo_certo_libera_o_envio():
    repo, cli = _FakeRepo(), _FakeCliente()
    svc = WhatsappOptinService(repo, cli)
    svc.registrar(1, "11999998888")
    svc.confirmar(1, repo.optins[1].codigo)

    assert repo.optins[1].status == STATUS_CONFIRMADO
    assert repo.optins[1].codigo is None   # não fica guardado depois de usado


def test_codigo_errado_conta_tentativa_e_esgota():
    repo, cli = _FakeRepo(), _FakeCliente()
    svc = WhatsappOptinService(repo, cli)
    svc.registrar(1, "11999998888")

    for _ in range(5):
        with pytest.raises(CodigoInvalido):
            svc.confirmar(1, "000000")
    # Sem contar tentativa, seis dígitos são um milhão de chutes à vontade.
    with pytest.raises(TentativasEsgotadas):
        svc.confirmar(1, "000000")


def test_codigo_expirado_nao_confirma():
    repo, cli = _FakeRepo(), _FakeCliente()
    svc = WhatsappOptinService(repo, cli)
    svc.registrar(1, "11999998888")
    repo.optins[1].codigo_expira_em = datetime.now(timezone.utc) - timedelta(minutes=1)
    with pytest.raises(CodigoInvalido):
        svc.confirmar(1, repo.optins[1].codigo)


def test_sair_desliga_todas_as_contas_com_aquele_numero():
    # Casal ou sócias no mesmo celular: desligar uma e continuar mandando pela
    # outra é pior do que não ter SAIR.
    repo = _FakeRepo([_optin(1), _optin(2), _optin(3, numero="5521888887777")])
    n = WhatsappOptinService(repo, _FakeCliente()).desligar_por_numero("5511999998888")
    assert n == 2
    assert repo.optins[3].status == STATUS_CONFIRMADO


# --- lote diário ------------------------------------------------------------

def _envio(repo, cliente, dormir=None):
    return WhatsappEnvioService(
        db=None, repo=repo, cliente=cliente,
        buscar_usuario=lambda uid: SimpleNamespace(name="Maria Silva"),
        dormir=dormir or (lambda s: None),
    )


class _EnvioComResumoFixo(WhatsappEnvioService):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.resumo_svc = SimpleNamespace(
            montar=lambda uid, nome, dia: SimpleNamespace(texto=f"resumo {uid}",
                                                          tem_movimento=True)
        )


def _servico(repo, cliente, dormir=None):
    return _EnvioComResumoFixo(
        db=None, repo=repo, cliente=cliente,
        buscar_usuario=lambda uid: SimpleNamespace(name="Maria Silva"),
        dormir=dormir or (lambda s: None),
    )


def test_manda_para_quem_confirmou_e_ignora_o_resto():
    repo = _FakeRepo([_optin(1), _optin(2, status=STATUS_PENDENTE),
                      _optin(3, status=STATUS_DESLIGADO)])
    cli = _FakeCliente()
    r = _servico(repo, cli).enviar_lote(hoje=HOJE)

    assert r.enviados == 1
    assert len(cli.enviadas) == 1


def test_nao_manda_duas_vezes_no_mesmo_dia():
    repo = _FakeRepo([_optin(1)])
    cli = _FakeCliente()
    svc = _servico(repo, cli)
    svc.enviar_lote(hoje=HOJE)
    r = svc.enviar_lote(hoje=HOJE)   # cron rodou de novo

    assert (r.enviados, r.pulados) == (0, 1)
    assert len(cli.enviadas) == 1


def test_espera_entre_mensagens_mas_nao_antes_da_primeira():
    repo = _FakeRepo([_optin(i) for i in range(1, 4)])
    esperas = []
    _servico(repo, _FakeCliente(), dormir=esperas.append).enviar_lote(hoje=HOJE)

    assert len(esperas) == 2   # 3 mensagens, 2 intervalos
    assert all(settings.WHATSAPP_INTERVALO_MIN_S <= e <= settings.WHATSAPP_INTERVALO_MAX_S
               for e in esperas)


def test_instancia_desconectada_para_antes_de_tentar():
    repo = _FakeRepo([_optin(i) for i in range(1, 6)])
    cli = _FakeCliente(conectado=False)
    r = _servico(repo, cli).enviar_lote(hoje=HOJE)

    assert r.interrompido_por == "desconectado"
    assert cli.enviadas == []
    assert repo.envios == []   # nem log de falha por afiliada


def test_falhas_seguidas_derrubam_o_lote():
    repo = _FakeRepo([_optin(i) for i in range(1, 21)])
    cli = _FakeCliente(erro=ErroWhatsapp("envio", "500"))
    r = _servico(repo, cli).enviar_lote(hoje=HOJE)

    assert r.interrompido_por == "falhas_seguidas"
    assert r.falhas == settings.WHATSAPP_FALHAS_PARA_PARAR


def test_erro_fatal_para_na_primeira():
    repo = _FakeRepo([_optin(i) for i in range(1, 6)])
    r = _servico(repo, _FakeCliente(erro=ErroWhatsapp("auth", "401"))).enviar_lote(hoje=HOJE)
    assert (r.interrompido_por, r.falhas) == ("auth", 1)


def test_numero_invalido_desliga_so_aquela_afiliada():
    # Problema de UMA pessoa não pode derrubar o lote nem repetir todo dia.
    repo = _FakeRepo([_optin(1)])
    r = _servico(repo, _FakeCliente(erro=ErroWhatsapp("numero_invalido"))).enviar_lote(hoje=HOJE)

    assert r.desligados == 1
    assert r.interrompido_por is None
    assert repo.optins[1].status == STATUS_DESLIGADO
    assert repo.optins[1].desligado_por == ORIGEM_FALHA


def test_teto_diario_interrompe_o_lote(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_TETO_DIARIO", 2)
    repo = _FakeRepo([_optin(i) for i in range(1, 11)])
    r = _servico(repo, _FakeCliente()).enviar_lote(hoje=HOJE)

    assert r.enviados == 2
    assert r.interrompido_por == "teto_diario"


def test_lote_cobre_o_dia_anterior():
    repo = _FakeRepo([_optin(1)])
    r = _servico(repo, _FakeCliente()).enviar_lote(hoje=HOJE)
    assert r.dia == ONTEM
    assert repo.envios[0]["referencia"] == ONTEM


def test_pode_mandar_para_uma_afiliada_so():
    repo = _FakeRepo([_optin(1), _optin(2)])
    cli = _FakeCliente()
    r = _servico(repo, cli).enviar_lote(hoje=HOJE, apenas_user_id=2)

    assert r.enviados == 1
    assert repo.envios[0]["user_id"] == 2
