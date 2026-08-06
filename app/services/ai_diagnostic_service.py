"""
Orquestra o Diagnóstico IA: saldo, snapshot, chamada, persistência.

Síncrono de propósito. A chamada leva ~5-15s, o que cabe numa requisição — e
fila já provou perder trabalho em silêncio neste projeto. Numa feature que
debita crédito por clique, sumir calado depois de cobrar é o pior resultado.

Dois invariantes:
  1. a sessão nunca fica em "gerando";
  2. crédito só é debitado quando a análise chega.
"""
import logging
from datetime import date, datetime, timezone

from app.models.ai_diagnostic import (
    PAPEL_IA, PAPEL_USUARIA, STATUS_ERRO, STATUS_PRONTO,
)
from app.services.ai_credit_service import CUSTO_CHAT, CUSTO_GERACAO
from app.services.ai_prompts import (
    SISTEMA_RELATORIO, montar_contexto_chat, montar_entrada_relatorio,
)
from app.services.openai_client import ErroIA

logger = logging.getLogger(__name__)

TETO_MENSAGENS = 20

MENSAGEM_POR_MOTIVO = {
    "sem_chave": "A análise por IA está indisponível no momento.",
    "timeout": "A análise demorou mais que o esperado. Tente de novo.",
    "http": "Não conseguimos falar com o serviço de IA agora. Tente de novo.",
    "formato": "A análise voltou incompleta. Tente de novo.",
}


class PeriodoVazio(Exception):
    pass


class GeracaoEmAndamento(Exception):
    pass


class LimiteDeMensagens(Exception):
    pass


class AiDiagnosticService:
    def __init__(self, repo, cliente, snapshot_svc, credito_svc):
        self.repo = repo
        self.cliente = cliente
        self.snapshot_svc = snapshot_svc
        self.credito_svc = credito_svc

    def gerar(self, user_id: int, plano: str, inicio: date, fim: date):
        em_curso = self.repo.em_andamento(user_id)
        if em_curso:
            raise GeracaoEmAndamento(em_curso.id)

        # Sem saldo: nem monta snapshot, nem chama a IA.
        if not self.credito_svc.tem_saldo(user_id, plano, CUSTO_GERACAO):
            from app.services.ai_credit_service import SaldoInsuficiente
            raise SaldoInsuficiente(self.credito_svc.saldo(user_id, plano), CUSTO_GERACAO)

        snapshot = self.snapshot_svc.montar(user_id, inicio, fim)
        if snapshot.get("vazio"):
            # Não gasta crédito para dizer "não há dados".
            raise PeriodoVazio()

        sessao = self.repo.criar(user_id, inicio, fim, snapshot)
        try:
            relatorio, entrada, saida = self.cliente.completar_json(
                SISTEMA_RELATORIO, montar_entrada_relatorio(snapshot)
            )
        except ErroIA as e:
            logger.warning("Diagnóstico %s falhou (%s)", sessao.id, e.motivo)
            sessao.status = STATUS_ERRO
            sessao.erro_mensagem = MENSAGEM_POR_MOTIVO.get(e.motivo, "Falha ao gerar a análise.")
            sessao.concluido_em = datetime.now(timezone.utc)
            return self.repo.salvar(sessao)

        sessao.relatorio = relatorio
        sessao.status = STATUS_PRONTO
        sessao.modelo = getattr(self.cliente, "modelo", None)
        sessao.tokens_entrada = entrada
        sessao.tokens_saida = saida
        sessao.concluido_em = datetime.now(timezone.utc)
        self.repo.salvar(sessao)

        # Débito só aqui: a análise chegou.
        self.credito_svc.debitar(user_id, plano, "geracao", CUSTO_GERACAO,
                                 diagnostic_id=sessao.id)
        return sessao

    def responder(self, user_id: int, plano: str, diagnostic_id: int, pergunta: str):
        sessao = self.repo.buscar(diagnostic_id, user_id)
        if not sessao:
            raise ValueError("Diagnóstico não encontrado")
        if sessao.status != STATUS_PRONTO:
            raise ValueError("Diagnóstico ainda não está pronto")
        if self.repo.contar_mensagens_da_usuaria(diagnostic_id) >= TETO_MENSAGENS:
            raise LimiteDeMensagens()
        if not self.credito_svc.tem_saldo(user_id, plano, CUSTO_CHAT):
            from app.services.ai_credit_service import SaldoInsuficiente
            raise SaldoInsuficiente(self.credito_svc.saldo(user_id, plano), CUSTO_CHAT)

        historico = [
            {"role": m.papel, "content": m.conteudo}
            for m in self.repo.listar_mensagens(diagnostic_id)
        ]
        historico.append({"role": "user", "content": pergunta})

        # Chamada ANTES de gravar: falha não deixa pergunta órfã nem debita.
        texto, _, _ = self.cliente.completar_texto(
            montar_contexto_chat(sessao.snapshot, sessao.relatorio), historico
        )

        self.repo.adicionar_mensagem(diagnostic_id, PAPEL_USUARIA, pergunta)
        resposta = self.repo.adicionar_mensagem(diagnostic_id, PAPEL_IA, texto)
        self.credito_svc.debitar(user_id, plano, "chat", CUSTO_CHAT,
                                 diagnostic_id=diagnostic_id)
        return resposta
