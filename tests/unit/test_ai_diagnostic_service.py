"""
Orquestração do Diagnóstico IA.

Dois invariantes valem mais que tudo aqui:
  1. a sessão NUNCA fica em "gerando" — sempre termina em pronto ou erro,
     não importa o tipo de exceção (ErroIA ou qualquer outra: banco,
     conexão, bug no cliente);
  2. crédito e entrega andam juntos — o débito acontece com a resposta da
     IA já em mãos e ANTES de marcar pronto (ou expor a resposta do chat),
     então falha da IA não debita e débito que falha não entrega.
"""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.ai_diagnostic import STATUS_ERRO, STATUS_PRONTO
from app.services.ai_credit_service import CUSTO_CHAT, CUSTO_GERACAO, SaldoInsuficiente
from app.repositories.ai_diagnostic_repository import GeracaoDuplicada
from app.services.ai_diagnostic_service import (
    TEMPO_MAXIMO_GERANDO, TETO_MENSAGENS, AiDiagnosticService,
    GeracaoEmAndamento, LimiteDeMensagens, PeriodoVazio,
)
from app.services.openai_client import ErroIA

# Relatório com seção preenchida: resumo sozinho não é entregável e o serviço
# recusa cobrar por ele (ver test_relatorio_sem_secao_nao_debita).
RELATORIO = {
    "resumo_executivo": "Operação saudável.",
    "escalar": [{"nome": "X", "motivo": "ROAS 2,4", "acao": "aumentar verba"}],
    "pausar": [], "observar": [],
    "detalhamento": [], "numeros": {}, "proximos_passos": ["Revisar a X."],
    "perguntas_sugeridas": ["Por que a X está no vermelho?"],
}


class _FakeRepo:
    def __init__(self):
        self.sessoes = {}
        self.mensagens = []
        self._seq = 0

    def criar(self, user_id, inicio, fim, snapshot):
        self._seq += 1
        s = SimpleNamespace(
            id=self._seq, user_id=user_id, periodo_inicio=inicio, periodo_fim=fim,
            snapshot=snapshot, relatorio=None, status="gerando", erro_mensagem=None,
            modelo=None, tokens_entrada=None, tokens_saida=None, concluido_em=None,
        )
        self.sessoes[self._seq] = s
        return s

    def salvar(self, sessao):
        self.sessoes[sessao.id] = sessao
        return sessao

    def buscar(self, diagnostic_id, user_id):
        s = self.sessoes.get(diagnostic_id)
        return s if s and s.user_id == user_id else None

    def expirar_travadas(self, user_id, limite):
        travadas = [s for s in self.sessoes.values()
                    if s.user_id == user_id and s.status == "gerando"
                    and getattr(s, "criado_em", None) and s.criado_em < limite]
        for s in travadas:
            s.status = "erro"
            s.erro_mensagem = "Análise interrompida. Tente de novo."
        return len(travadas)

    def em_andamento(self, user_id):
        return next((s for s in self.sessoes.values()
                     if s.user_id == user_id and s.status == "gerando"), None)

    def adicionar_mensagem(self, diagnostic_id, papel, conteudo):
        m = SimpleNamespace(id=len(self.mensagens) + 1, diagnostic_id=diagnostic_id,
                            papel=papel, conteudo=conteudo)
        self.mensagens.append(m)
        return m

    def adicionar_mensagens(self, diagnostic_id, mensagens):
        # Espelha o commit único do repository real: só entra na lista se
        # TODAS as mensagens puderem ser criadas (nada é anexado aos poucos).
        objetos = [
            SimpleNamespace(id=len(self.mensagens) + i + 1, diagnostic_id=diagnostic_id,
                            papel=papel, conteudo=conteudo)
            for i, (papel, conteudo) in enumerate(mensagens)
        ]
        self.mensagens.extend(objetos)
        return objetos

    def listar_mensagens(self, diagnostic_id):
        return [m for m in self.mensagens if m.diagnostic_id == diagnostic_id]

    def contar_mensagens_da_usuaria(self, diagnostic_id):
        return len([m for m in self.mensagens
                    if m.diagnostic_id == diagnostic_id and m.papel == "user"])


class _FakeCliente:
    def __init__(self, json_resposta=None, texto="resposta", erro=None):
        self._json = json_resposta if json_resposta is not None else RELATORIO
        self._texto = texto
        self._erro = erro
        self.chamadas = 0

    def disponivel(self):
        return True

    def completar_json(self, sistema, usuario, timeout=60.0):
        self.chamadas += 1
        if self._erro:
            raise self._erro
        return self._json, 100, 50

    def completar_texto(self, sistema, mensagens, timeout=60.0):
        self.chamadas += 1
        if self._erro:
            raise self._erro
        return self._texto, 10, 5


class _FakeSnapshot:
    def __init__(self, vazio=False):
        self._vazio = vazio

    def montar(self, user_id, inicio, fim):
        return {"periodo": {"inicio": str(inicio), "fim": str(fim)},
                "kpis": {}, "tops": {}, "campanhas": [], "tem_meta": False,
                "vazio": self._vazio}


class _FakeCredito:
    def __init__(self, saldo_inicial=200):
        self._saldo = saldo_inicial
        self.debitos = []

    def saldo(self, user_id, plano):
        return self._saldo

    def tem_saldo(self, user_id, plano, custo):
        return self._saldo >= custo

    def debitar(self, user_id, plano, tipo, creditos, diagnostic_id=None):
        if self._saldo < creditos:
            raise SaldoInsuficiente(self._saldo, creditos)
        self._saldo -= creditos
        self.debitos.append({"tipo": tipo, "creditos": creditos, "diag": diagnostic_id})
        return self._saldo


class _FakeRepoFalhaAoSalvarPronto(_FakeRepo):
    """Simula uma falha genérica (NÃO ErroIA) na gravação final — ex.: erro de
    banco no commit. Usada para provar que o invariante "nunca fica em
    gerando" vale mesmo fora do caminho de ErroIA."""

    def salvar(self, sessao):
        if sessao.status == "pronto":
            raise RuntimeError("falha simulada ao gravar sessão pronta")
        return super().salvar(sessao)


class _FakeCreditoQueFalhaAoDebitar(_FakeCredito):
    """Simula a corrida real: tem_saldo() já passou (leitura solta), mas o
    débito em si falha — ex.: outra requisição consumiu o crédito antes, ou o
    backend de crédito caiu no meio do caminho."""

    def debitar(self, user_id, plano, tipo, creditos, diagnostic_id=None):
        raise SaldoInsuficiente(0, creditos)


class _FakeRepoFalhaAoGravarMensagens(_FakeRepo):
    """Simula a gravação atômica de pergunta+resposta falhando — nada deve
    ficar no histórico quando isso acontece, nem a pergunta sozinha."""

    def adicionar_mensagens(self, diagnostic_id, mensagens):
        raise RuntimeError("falha simulada ao gravar mensagens do chat")


def _servico(cliente=None, snapshot=None, credito=None, repo=None):
    return AiDiagnosticService(
        repo=repo or _FakeRepo(),
        cliente=cliente or _FakeCliente(),
        snapshot_svc=snapshot or _FakeSnapshot(),
        credito_svc=credito or _FakeCredito(),
    )


P = (date(2026, 8, 1), date(2026, 8, 5))


def test_geracao_com_sucesso_fica_pronta_e_debita_10():
    credito = _FakeCredito()
    s = _servico(credito=credito)
    sessao = s.gerar(1, "pro", *P)
    assert sessao.status == STATUS_PRONTO
    assert sessao.relatorio == RELATORIO
    assert credito.debitos == [{"tipo": "geracao", "creditos": CUSTO_GERACAO, "diag": sessao.id}]


def test_falha_da_ia_marca_erro_e_nao_debita():
    credito = _FakeCredito()
    s = _servico(cliente=_FakeCliente(erro=ErroIA("timeout")), credito=credito)
    sessao = s.gerar(1, "pro", *P)
    assert sessao.status == STATUS_ERRO
    assert credito.debitos == []


def test_sessao_nunca_termina_em_gerando():
    for erro in (ErroIA("timeout"), ErroIA("http"), ErroIA("formato")):
        s = _servico(cliente=_FakeCliente(erro=erro))
        assert s.gerar(1, "pro", *P).status != "gerando"


def test_periodo_vazio_nem_chama_a_ia():
    cliente = _FakeCliente()
    s = _servico(cliente=cliente, snapshot=_FakeSnapshot(vazio=True))
    with pytest.raises(PeriodoVazio):
        s.gerar(1, "pro", *P)
    assert cliente.chamadas == 0


def test_sem_saldo_nem_chama_a_ia():
    cliente = _FakeCliente()
    s = _servico(cliente=cliente, credito=_FakeCredito(saldo_inicial=3))
    with pytest.raises(SaldoInsuficiente):
        s.gerar(1, "pro", *P)
    assert cliente.chamadas == 0


def test_chat_debita_1_credito_e_grava_as_duas_pontas():
    repo = _FakeRepo()
    credito = _FakeCredito()
    s = _servico(repo=repo, credito=credito)
    sessao = s.gerar(1, "pro", *P)
    credito.debitos.clear()

    s.responder(1, "pro", sessao.id, "por quê?")
    papeis = [m.papel for m in repo.listar_mensagens(sessao.id)]
    assert papeis == ["user", "assistant"]
    assert credito.debitos == [{"tipo": "chat", "creditos": CUSTO_CHAT, "diag": sessao.id}]


def test_chat_respeita_o_teto_de_mensagens():
    repo = _FakeRepo()
    s = _servico(repo=repo)
    sessao = s.gerar(1, "pro", *P)
    for i in range(TETO_MENSAGENS):
        repo.adicionar_mensagem(sessao.id, "user", f"p{i}")
    with pytest.raises(LimiteDeMensagens):
        s.responder(1, "pro", sessao.id, "mais uma")


def test_chat_de_sessao_de_outra_usuaria_nao_responde():
    repo = _FakeRepo()
    s = _servico(repo=repo)
    sessao = s.gerar(1, "pro", *P)
    with pytest.raises(ValueError):
        s.responder(999, "pro", sessao.id, "oi")


def test_falha_no_chat_nao_debita():
    repo = _FakeRepo()
    credito = _FakeCredito()
    s = _servico(repo=repo, credito=credito)
    sessao = s.gerar(1, "pro", *P)
    credito.debitos.clear()
    s.cliente = _FakeCliente(erro=ErroIA("timeout"))
    with pytest.raises(ErroIA):
        s.responder(1, "pro", sessao.id, "por quê?")
    assert credito.debitos == []


def test_excecao_nao_erroia_durante_geracao_termina_em_erro_nunca_gerando():
    # Repositório falha ao gravar o estado "pronto" com uma exceção genérica
    # (não ErroIA) — o invariante "nunca fica em gerando" precisa valer aqui
    # também, não só quando a IA falha.
    repo = _FakeRepoFalhaAoSalvarPronto()
    s = _servico(repo=repo)
    sessao = s.gerar(1, "pro", *P)
    assert sessao.status == STATUS_ERRO
    assert sessao.status != "gerando"


def test_debito_falha_apos_resposta_da_ia_deixa_sessao_em_erro_sem_entregar_analise():
    # tem_saldo() já passou, mas o débito em si falha (corrida real). A
    # sessão não pode ficar "pronto" sem ter sido cobrada.
    credito = _FakeCreditoQueFalhaAoDebitar()
    s = _servico(credito=credito)
    sessao = s.gerar(1, "pro", *P)
    assert sessao.status == STATUS_ERRO
    assert sessao.relatorio is None


def test_falha_ao_gravar_resposta_do_chat_nao_deixa_pergunta_orfa():
    repo = _FakeRepoFalhaAoGravarMensagens()
    credito = _FakeCredito()
    s = _servico(repo=repo, credito=credito)
    sessao = s.gerar(1, "pro", *P)

    with pytest.raises(RuntimeError):
        s.responder(1, "pro", sessao.id, "por quê?")

    # Nem a pergunta da usuária pode sobrar sozinha no histórico.
    assert repo.listar_mensagens(sessao.id) == []


# --- uma geração por vez ----------------------------------------------------

class _FakeRepoQueRecusaDuplicada(_FakeRepo):
    """Espelha o índice único da migration 044: o banco recusa a segunda
    sessão "gerando" da mesma usuária mesmo quando a checagem em Python passa
    (é exatamente a janela entre em_andamento() e o insert)."""

    def criar(self, user_id, inicio, fim, snapshot):
        raise GeracaoDuplicada()


def test_geracao_simultanea_recusada_pelo_banco_vira_409_sem_debitar():
    credito = _FakeCredito()
    s = _servico(repo=_FakeRepoQueRecusaDuplicada(), credito=credito)
    with pytest.raises(GeracaoEmAndamento):
        s.gerar(1, "pro", *P)
    assert credito.debitos == []


def test_sessao_em_andamento_bloqueia_nova_geracao():
    repo = _FakeRepo()
    repo.criar(1, *P, {})   # fica em "gerando"
    with pytest.raises(GeracaoEmAndamento):
        _servico(repo=repo).gerar(1, "pro", *P)


def test_sessao_travada_ha_muito_tempo_nao_bloqueia_para_sempre():
    # Processo morreu no meio: sem expirar, a usuária levava 409 eterno.
    repo = _FakeRepo()
    presa = repo.criar(1, *P, {})
    presa.criado_em = datetime.now(timezone.utc) - TEMPO_MAXIMO_GERANDO - timedelta(minutes=1)

    sessao = _servico(repo=repo).gerar(1, "pro", *P)
    assert sessao.status == STATUS_PRONTO
    assert presa.status == STATUS_ERRO


def test_sessao_gerando_recente_continua_bloqueando():
    repo = _FakeRepo()
    recente = repo.criar(1, *P, {})
    recente.criado_em = datetime.now(timezone.utc)
    with pytest.raises(GeracaoEmAndamento):
        _servico(repo=repo).gerar(1, "pro", *P)


# --- relatório fora de forma ------------------------------------------------

def test_relatorio_sem_secao_nao_debita_e_termina_em_erro():
    # JSON válido, relatório inútil: a tela renderizaria quase em branco e os
    # 10 créditos já teriam saído.
    credito = _FakeCredito()
    cliente = _FakeCliente(json_resposta={"resumo_executivo": "Tudo certo."})
    sessao = _servico(cliente=cliente, credito=credito).gerar(1, "pro", *P)
    assert sessao.status == STATUS_ERRO
    assert credito.debitos == []


def test_relatorio_que_nem_e_objeto_nao_debita():
    credito = _FakeCredito()
    cliente = _FakeCliente(json_resposta=["lista", "no", "lugar", "errado"])
    sessao = _servico(cliente=cliente, credito=credito).gerar(1, "pro", *P)
    assert sessao.status == STATUS_ERRO
    assert credito.debitos == []


def test_relatorio_gravado_vem_normalizado():
    cliente = _FakeCliente(json_resposta={
        "resumo_executivo": "Resumo.", "escalar": [{"nome": "X"}], "pausar": None,
    })
    sessao = _servico(cliente=cliente).gerar(1, "pro", *P)
    assert sessao.status == STATUS_PRONTO
    assert sessao.relatorio["pausar"] == []
