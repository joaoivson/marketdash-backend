"""
Orquestra o Diagnóstico IA: saldo, snapshot, chamada, persistência.

Síncrono de propósito. A chamada leva ~5-15s, o que cabe numa requisição — e
fila já provou perder trabalho em silêncio neste projeto. Numa feature que
debita crédito por clique, sumir calado depois de cobrar é o pior resultado.

Dois invariantes:
  1. a sessão nunca fica em "gerando" — TODA saída de gerar() termina em
     "pronto" ou "erro", não importa o tipo de exceção (ErroIA ou qualquer
     outra: banco, conexão, bug no cliente);
  2. crédito e entrega andam juntos: o débito acontece com a resposta da IA
     já em mãos e ANTES de marcar a sessão como pronta (ou de expor a
     resposta do chat), então nunca existe análise/resposta visível que não
     foi cobrada.
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
            mensagem = MENSAGEM_POR_MOTIVO.get(e.motivo, "Falha ao gerar a análise.")
            return self._marcar_erro(sessao, mensagem)
        except Exception:
            # Qualquer outra exceção (erro de banco, conexão caída, bug no
            # cliente) não pode subir crua: já tivemos linha presa em estado
            # intermediário para sempre neste produto (uploads em "pending"
            # por meses) e o custo de repetir isso aqui é alto.
            logger.exception("Diagnóstico %s: erro inesperado ao chamar a IA", sessao.id)
            return self._marcar_erro(sessao, "Falha ao gerar a análise. Tente de novo.")

        # Débito ANTES de marcar pronto: a resposta da IA já está em mãos, e é
        # aqui que a corrida real acontece (tem_saldo() é leitura solta; duas
        # requisições simultâneas podem passar nela antes de qualquer débito).
        # Se o débito falhar agora, a sessão termina em erro — a usuária não
        # pode ver uma análise que não foi cobrada.
        try:
            self.credito_svc.debitar(user_id, plano, "geracao", CUSTO_GERACAO,
                                     diagnostic_id=sessao.id)
        except Exception as e:
            logger.warning("Diagnóstico %s: débito falhou após resposta da IA (%s)", sessao.id, e)
            return self._marcar_erro(sessao, "Não foi possível concluir a cobrança da análise. Tente de novo.")

        sessao.relatorio = relatorio
        sessao.status = STATUS_PRONTO
        sessao.modelo = getattr(self.cliente, "modelo", None)
        sessao.tokens_entrada = entrada
        sessao.tokens_saida = saida
        sessao.concluido_em = datetime.now(timezone.utc)
        try:
            return self.repo.salvar(sessao)
        except Exception:
            # Já debitamos e a IA já respondeu — só a gravação final falhou.
            # Mesmo assim a sessão não pode ficar em "gerando": tenta encerrar
            # em erro como melhor esforço (ver _marcar_erro).
            logger.exception("Diagnóstico %s: falha ao gravar sessão pronta", sessao.id)
            return self._marcar_erro(sessao, "Falha ao gravar a análise. Tente de novo.")

    def _marcar_erro(self, sessao, mensagem: str):
        sessao.status = STATUS_ERRO
        sessao.erro_mensagem = mensagem
        sessao.concluido_em = datetime.now(timezone.utc)
        try:
            return self.repo.salvar(sessao)
        except Exception:
            # Se até gravar o estado de erro falha, relançar não resolve nada
            # — só troca "presa em gerando por bug" por "presa em gerando por
            # banco fora do ar", e o segundo caso já vai aparecer no log de
            # qualquer forma. Registra e segue; a sessão fica para
            # investigação/reprocessamento manual.
            logger.exception("Diagnóstico %s: falha ao gravar estado de erro", sessao.id)
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

        # Chamada à IA antes de qualquer gravação: se ela falhar, nada é
        # gravado e nada é debitado — a pergunta da usuária nunca chega a
        # existir sozinha no histórico.
        texto, _, _ = self.cliente.completar_texto(
            montar_contexto_chat(sessao.snapshot, sessao.relatorio), historico
        )

        # Débito ANTES de gravar (mesma regra da geração): a resposta só fica
        # visível no histórico depois de cobrada. Se o débito falhar aqui
        # (mesma corrida de tem_saldo() solto), a exceção sobe e nenhuma
        # mensagem é gravada.
        self.credito_svc.debitar(user_id, plano, "chat", CUSTO_CHAT,
                                 diagnostic_id=diagnostic_id)

        # Pergunta e resposta num único commit: se a gravação falhar no meio
        # do caminho, nenhuma das duas fica no histórico — nunca sobra
        # pergunta sem resposta.
        _, resposta = self.repo.adicionar_mensagens(
            diagnostic_id, [(PAPEL_USUARIA, pergunta), (PAPEL_IA, texto)]
        )
        return resposta
