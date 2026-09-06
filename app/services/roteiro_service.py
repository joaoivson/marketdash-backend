"""
Roteiros: resolução de tempos, materialização da execução, preview, status por
passo e duplicação — o que a spec chama de "coração do módulo" (§9).

Regras de tempo (rodada 06/09 — a data-âncora GLOBAL saiu):
  * passo `ancora`: data_fixa + hora_fixa, em BRT. Os DOIS obrigatórios.
    Abertura de carrinho, virada de lote e fechamento são data e hora
    absolutas; não existe offset que resolva, e derivar tudo de uma âncora
    única não atendia lançamento;
  * passo `relativo`: horário do passo ANTERIOR + offset_segundos;
  * passo já no passado BLOQUEIA salvar e agendar. É a trava que sustenta a
    duplicação: duplicar 22 passos e esquecer uma das datas fixas agendava a
    mensagem para o lançamento passado — que dispara na hora ou falha calada.

Materialização: TODAS as mensagens nascem com `agendado_para` absoluto no
momento do agendar — o tick só compara timestamps, nunca recalcula. Envio
rápido é um roteiro de 1 passo com âncora "agora": mesma tabela, mesmo motor.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.roteiro import (
    ACOES_VALIDAS, BLOCO_IMAGEM, BLOCO_TEXTO, CONTEUDO_ACAO, CONTEUDO_MENSAGEM,
    EXEC_AGENDADA, EXEC_ATIVAS, EXEC_CONCLUIDA, MSG_ENVIADA, MSG_ENVIANDO,
    MSG_PENDENTE, MSG_PULADA, ORIGEM_ENVIO_RAPIDO, PassoBloco, Roteiro,
    RoteiroExecucao, RoteiroMensagem, RoteiroPasso, TEMPO_ANCORA, TEMPO_RELATIVO,
    UNIDADE_MINUTOS, UNIDADES,
)
from app.repositories.campanha_grupos_repository import CampanhaGruposRepository
from app.repositories.roteiro_repository import RoteiroRepository
from app.repositories.whatsapp_grupo_repository import WhatsappGrupoRepository
from app.services.janela_envio_service import BRT

logger = logging.getLogger(__name__)


class RoteiroInvalido(Exception):
    pass


class PassosNoPassado(RoteiroInvalido):
    """Passos cujo horário resolvido já passou — a tela pinta de vermelho e
    aponta QUAIS. Erro estruturado (não string) porque a lista é a informação:
    "algum passo está no passado" num roteiro de 22 é inútil."""

    def __init__(self, ordens: List[int]):
        self.ordens = ordens
        alvo = ", ".join(str(o) for o in ordens)
        plural = "os passos" if len(ordens) > 1 else "o passo"
        super().__init__(f"Ajuste {plural} {alvo}: o horário já passou.")


class PassoJaEnviado(RoteiroInvalido):
    """Tentativa de editar, mover ou excluir passo que já saiu (ou está
    saindo). Antes desta rodada a edição era ACEITA e ignorada em silêncio —
    pior: o `definir_passos` deletava os passos, o CASCADE levava junto as
    `roteiro_mensagens` ainda pendentes, e o resto do lançamento simplesmente
    não saía."""

    def __init__(self, ordens: List[int]):
        self.ordens = ordens
        alvo = ", ".join(str(o) for o in ordens)
        plural = "Os passos" if len(ordens) > 1 else "O passo"
        super().__init__(f"{plural} {alvo} já saiu — não dá para alterar.")


class ExecucaoJaAtiva(RoteiroInvalido):
    """Agendar um roteiro que já tem execução em andamento. Em 06/09 o mesmo
    roteiro foi agendado três vezes em 16 segundos porque a listagem continuava
    dizendo "Rascunho" e o botão "Agendar" continuava na linha."""

    def __init__(self, execucao_id: int):
        self.execucao_id = execucao_id
        super().__init__("Este roteiro já está agendado.")


class CampanhaInvalida(Exception):
    """campanha_id inexistente ou de outra usuária."""


# Motivos técnicos → o que a afiliada lê. Erro cru na tela de quem opera o
# lançamento não é diagnóstico, é ruído.
MOTIVOS_EM_PORTUGUES = {
    "grupo_inativo": "Grupo inativo",
    "grupo_desativado": "Grupo desativado por você",
    "sem_permissao": "Sem permissão para enviar no grupo",
    "sem_admin": "Você não é admin deste grupo",
    "sem_instancia": "Nenhum número conectado",
    "sem_instancia_no_grupo": "Nenhum número seu está neste grupo",
    "short_link": "Não foi possível gerar o link da oferta",
    "grupo_invalido": "Grupo não encontrado no WhatsApp",
    "numero_invalido": "Número inválido",
    "desconectado": "Número desconectado",
    "timeout": "O WhatsApp não respondeu a tempo",
    "rede": "Falha de conexão",
    "interrompida": "Interrompida no meio do envio",
    "acao": "A ação no grupo falhou",
    "acao_descontinuada": "Esta ação foi removida do produto",
    "bloco_nao_suportado": "Tipo de bloco ainda não suportado",
    "envio": "O WhatsApp recusou a mensagem",
    "jid": "Endereço do grupo inválido",
}


def motivo_em_portugues(motivo: Optional[str]) -> Optional[str]:
    if not motivo:
        return None
    if motivo in MOTIVOS_EM_PORTUGUES:
        return MOTIVOS_EM_PORTUGUES[motivo]
    # `jid: <detalhe>` é o único motivo composto que o motor grava.
    cabeca = motivo.partition(":")[0].strip()
    return MOTIVOS_EM_PORTUGUES.get(cabeca, "Falha no envio")


def _horario_brt(dia: date, hora) -> datetime:
    return datetime(dia.year, dia.month, dia.day, hora.hour, hora.minute,
                    tzinfo=BRT)


def offset_para_segundos(valor: Optional[int], unidade: Optional[str]) -> int:
    """(valor, unidade) → segundos. A unidade é só INTENÇÃO de exibição; o que
    o motor usa é sempre o canônico em segundos."""
    return int(valor or 0) * UNIDADES.get(unidade or UNIDADE_MINUTOS, 60)


def segundos_para_offset(segundos: Optional[int],
                         unidade: Optional[str]) -> Tuple[int, str]:
    """Inverso do de cima, para devolver ao editor o que ela digitou. 90s tem
    que voltar como "+90 segundos", não como "+1,5 min" — por isso a unidade é
    guardada, e não deduzida."""
    total = int(segundos or 0)
    u = unidade or UNIDADE_MINUTOS
    divisor = UNIDADES.get(u, 60)
    if total % divisor != 0:
        # Unidade gravada não divide o valor (edição por outra via): cai para a
        # maior unidade exata, nunca para um número quebrado.
        for candidata in ("horas", "minutos", "segundos"):
            if total % UNIDADES[candidata] == 0:
                u, divisor = candidata, UNIDADES[candidata]
                break
    return total // divisor, u


def resolver_horarios(passos: Sequence[RoteiroPasso],
                      data_ancora: Optional[date] = None
                      ) -> Tuple[List[Tuple[RoteiroPasso, datetime]], List[str]]:
    """[(passo, horário absoluto BRT)] na ordem + avisos de configuração.

    `data_ancora` é aceita só como rede para linha pré-082 que ainda não tenha
    `data_fixa` (a migration faz o backfill; um passo criado por caminho que
    não passou por ela não pode derrubar a leitura do roteiro inteiro).
    """
    resolvidos: List[Tuple[RoteiroPasso, datetime]] = []
    avisos: List[str] = []
    anterior: Optional[datetime] = None

    for passo in passos:
        if passo.tipo_tempo == TEMPO_ANCORA:
            if passo.hora_fixa is None:
                raise RoteiroInvalido(f"O passo {passo.ordem} está sem horário.")
            dia = passo.data_fixa or data_ancora
            if dia is None:
                raise RoteiroInvalido(f"O passo {passo.ordem} está sem data.")
            momento = _horario_brt(dia, passo.hora_fixa)
        elif passo.tipo_tempo == TEMPO_RELATIVO:
            if anterior is None:
                raise RoteiroInvalido("O primeiro passo do roteiro precisa ter hora fixa.")
            momento = anterior + timedelta(
                seconds=int(passo.offset_segundos or 0)
            )
        else:
            raise RoteiroInvalido(f"tipo_tempo desconhecido: {passo.tipo_tempo!r}")

        if anterior is not None and momento < anterior:
            avisos.append(
                f"O passo {passo.ordem} ficou ANTES do passo anterior "
                f"({momento:%d/%m %H:%M}) — confira as datas."
            )
        resolvidos.append((passo, momento))
        anterior = momento

    # Relativo que "atravessa" a âncora seguinte (spec §9.2). Continua valendo
    # com data por passo — e vale mais: agora o passo seguinte pode estar em
    # OUTRO dia, e um offset grande passa por cima dele sem nada na tela.
    for i, (passo, momento) in enumerate(resolvidos[:-1]):
        proxima_ancora = next(
            (m for p, m in resolvidos[i + 1:] if p.tipo_tempo == TEMPO_ANCORA), None
        )
        if (passo.tipo_tempo == TEMPO_RELATIVO and proxima_ancora
                and momento > proxima_ancora):
            avisos.append(
                f"O passo {passo.ordem} (relativo, {momento:%d/%m %H:%M}) cai "
                f"depois do passo de hora fixa seguinte ({proxima_ancora:%d/%m %H:%M})."
            )
    return resolvidos, avisos


def ordens_no_passado(resolvidos: Sequence[Tuple[RoteiroPasso, datetime]],
                      agora: Optional[datetime] = None) -> List[int]:
    agora = agora or datetime.now(timezone.utc)
    return [p.ordem for p, momento in resolvidos if momento < agora]


def estimativa_de_duracao_s(total_mensagens: int) -> int:
    """O que a afiliada VÊ (o ritmo em si é config de sistema, nunca UI)."""
    pausa_media = (settings.WHATSAPP_GRUPO_PAUSA_MIN_S
                   + settings.WHATSAPP_GRUPO_PAUSA_MAX_S) / 2
    jitter_media = (settings.WHATSAPP_GRUPO_JITTER_MIN_S
                    + settings.WHATSAPP_GRUPO_JITTER_MAX_S) / 2
    por_msg = jitter_media + pausa_media / max(settings.WHATSAPP_RODADA_TAMANHO, 1)
    return int(total_mensagens * por_msg)


#: Campos do passo que, se mudarem, mudam o que sai ou quando sai. É o que
#: separa "ela reabriu o passo e fechou" de "ela editou o passo".
_CAMPOS_DO_PASSO = (
    "tipo_tempo", "hora_fixa", "data_fixa", "offset_segundos", "offset_unidade",
    "tipo_conteudo", "texto", "midia_url", "oferta_url", "template_id",
    "acao", "acao_parametro", "grupos_alvo", "marcar_todos",
)


class RoteiroService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = RoteiroRepository(db)
        self.repo_grupos = WhatsappGrupoRepository(db)

    # --- ownership ----------------------------------------------------------

    def validar_campanha(self, user_id: int, campanha_id: Optional[int]) -> None:
        """Ownership do vínculo roteiro↔campanha — sem isso, prefixo/sufixo de
        OUTRA usuária vazaria para dentro das mensagens enviadas."""
        if campanha_id is None:
            return
        if CampanhaGruposRepository(self.db).por_id(user_id, campanha_id) is None:
            raise CampanhaInvalida("Campanha não encontrada.")

    def _validar_templates(self, user_id: int, ids: Sequence[Optional[int]]) -> None:
        """
        `template_id` vem do cliente e o id é sequencial.

        Sem esta checagem, apontar o passo para o template de OUTRA usuária
        fazia o texto dela sair nos grupos de quem copiou o id. Aqui é a
        primeira barreira; a segunda está em
        `roteiro_envio_service._texto_final`, que filtra por dono no disparo.
        """
        from app.repositories.template_repository import TemplateRepository

        alvo = {i for i in ids if i}
        if not alvo:
            return
        repo = TemplateRepository(self.db)
        for template_id in alvo:
            if repo.por_id(user_id, template_id) is None:
                raise RoteiroInvalido("Template não encontrado.")

    # --- execução ativa -----------------------------------------------------

    def execucao_ativa(self, roteiro_id: int) -> Optional[RoteiroExecucao]:
        return (
            self.db.query(RoteiroExecucao)
            .filter(RoteiroExecucao.roteiro_id == roteiro_id,
                    RoteiroExecucao.status.in_(EXEC_ATIVAS))
            .order_by(RoteiroExecucao.criado_em.desc(), RoteiroExecucao.id.desc())
            .first()
        )

    def ultima_execucao(self, roteiro_id: int) -> Optional[RoteiroExecucao]:
        """A execução cujo resultado a tela mostra. O roteiro é TEMPLATE: o
        mesmo vai rodar no próximo lançamento, e o status é da última corrida —
        senão ela reusa o roteiro do lançamento passado e ele aparece todo
        verde antes de mandar qualquer coisa."""
        return (
            self.db.query(RoteiroExecucao)
            .filter(RoteiroExecucao.roteiro_id == roteiro_id)
            # Desempate por id: duas execuções no mesmo segundo empatam em
            # `criado_em`, e "a última" viraria sorteio — justo no campo que
            # decide qual status a tela mostra.
            .order_by(RoteiroExecucao.criado_em.desc(), RoteiroExecucao.id.desc())
            .first()
        )

    def passos_intocaveis(self, execucao: Optional[RoteiroExecucao]) -> set:
        """passo_ids que já saíram (ou estão saindo) nesta execução."""
        if execucao is None:
            return set()
        linhas = (
            self.db.query(RoteiroMensagem.passo_id)
            .filter(RoteiroMensagem.execucao_id == execucao.id,
                    RoteiroMensagem.status.in_((MSG_ENVIADA, MSG_ENVIANDO)))
            .distinct()
            .all()
        )
        return {linha[0] for linha in linhas}

    def passos_com_historico(self, roteiro_id: int) -> set:
        """passo_ids que já entregaram em QUALQUER execução, inclusive as
        concluídas.

        Excluir um passo continua fazendo `DELETE`, e o CASCADE de
        `roteiro_mensagens.passo_id` leva junto o histórico — inclusive as
        linhas `enviada` de lançamentos passados. Apagar o passo é decisão dela;
        reescrever o que já aconteceu no grupo, não.
        """
        linhas = (
            self.db.query(RoteiroMensagem.passo_id)
            .join(RoteiroPasso, RoteiroPasso.id == RoteiroMensagem.passo_id)
            .filter(RoteiroPasso.roteiro_id == roteiro_id,
                    RoteiroMensagem.status == MSG_ENVIADA)
            .distinct()
            .all()
        )
        return {linha[0] for linha in linhas}

    # --- salvar passos (diff, NUNCA delete-and-recreate) --------------------

    def definir_passos(self, roteiro: Roteiro, passos_in: List) -> None:
        """Persiste a lista de passos preservando os ids.

        **Por que diff e não `DELETE` + `INSERT`.** A versão anterior apagava
        todos os passos a cada salvar. `roteiro_mensagens.passo_id` é
        `ON DELETE CASCADE`, então o salvar levava junto TODA a fila já
        materializada — inclusive as mensagens ainda pendentes de uma execução
        em andamento. Em 06/09, às 12:04:39, um salvar apagou a mensagem do
        passo 2 que sairia às 12:05: a execução virou `concluida` com
        `total = 0` e nada avisou ninguém. Preservar o id é o que mantém a fila
        viva — e é também o que permite bloquear a edição do que já saiu.
        """
        self._validar_templates(
            roteiro.user_id,
            [p.template_id for p in passos_in]
            + [b.template_id for p in passos_in for b in (p.blocos or [])],
        )

        atuais = {p.id: p for p in self.repo.passos(roteiro.id)}
        execucao = self.execucao_ativa(roteiro.id)
        intocaveis = self.passos_intocaveis(execucao)

        chegando = {p.id for p in passos_in if getattr(p, "id", None)}
        desconhecidos = chegando - set(atuais)
        if desconhecidos:
            raise RoteiroInvalido("Passo de outro roteiro na lista.")

        # (1) Exclusão de passo que já entregou — em QUALQUER execução, não só
        #     na ativa: o CASCADE apagaria o histórico de lançamentos passados.
        removidos = [pid for pid in atuais if pid not in chegando]
        com_historico = self.passos_com_historico(roteiro.id) | intocaveis
        travados = [atuais[pid].ordem for pid in removidos if pid in com_historico]
        if travados:
            raise PassoJaEnviado(sorted(travados))

        # (2) Edição/movimento de passo já enviado.
        alterados: List[int] = []
        for i, entrada in enumerate(passos_in, start=1):
            pid = getattr(entrada, "id", None)
            if pid is None or pid not in intocaveis:
                continue
            atual = atuais[pid]
            if atual.ordem != i or self._passo_mudou(atual, entrada):
                alterados.append(atual.ordem)
        if alterados:
            raise PassoJaEnviado(sorted(set(alterados)))

        # (3) Aplicar. Update in place preserva o id — e com ele a fila.
        for i, entrada in enumerate(passos_in, start=1):
            pid = getattr(entrada, "id", None)
            passo = atuais[pid] if pid else RoteiroPasso(roteiro_id=roteiro.id)
            passo.ordem = i
            self._aplicar_campos(passo, entrada)
            if pid is None:
                self.repo.adicionar(passo)
            else:
                self.db.add(passo)
            self.db.flush()
            self._definir_blocos(passo, entrada)

        for pid in removidos:
            self.db.delete(atuais[pid])

        self.db.flush()
        self._validar_horarios_para_salvar(roteiro, intocaveis)
        self.db.commit()

        # (4) Reagendar SOMENTE as pendentes. As já enviadas não são tocadas —
        # a cadeia é relativa: editar o passo 1 recalcula o passo 2, e o passo 1
        # pode já ter saído.
        if execucao is not None:
            self.resincronizar_execucao(roteiro, execucao)

    def _passo_mudou(self, atual: RoteiroPasso, entrada) -> bool:
        for campo in _CAMPOS_DO_PASSO:
            novo = getattr(entrada, campo, None)
            if campo == "offset_segundos":
                novo = offset_para_segundos(
                    getattr(entrada, "offset_valor", None),
                    getattr(entrada, "offset_unidade", None),
                ) if entrada.tipo_tempo == TEMPO_RELATIVO else None
            if campo == "offset_unidade" and entrada.tipo_tempo != TEMPO_RELATIVO:
                novo = None
            if (getattr(atual, campo, None) or None) != (novo or None):
                return True
        alvo_atual = sorted(atual.grupos_alvo_ids or [])
        alvo_novo = sorted(getattr(entrada, "grupos_alvo_ids", None) or [])
        if alvo_atual != alvo_novo:
            return True
        return self._blocos_atuais(atual.id) != self._blocos_de(entrada)

    @staticmethod
    def _blocos_de(entrada) -> List[Tuple]:
        return [
            (i, b.tipo, (b.conteudo or None), (b.legenda or None), b.template_id)
            for i, b in enumerate(entrada.blocos or [], start=1)
        ]

    def _blocos_atuais(self, passo_id: int) -> List[Tuple]:
        return [
            (b.ordem, b.tipo, (b.conteudo or None), (b.legenda or None), b.template_id)
            for b in self.repo.blocos(passo_id)
        ]

    @staticmethod
    def _aplicar_campos(passo: RoteiroPasso, entrada) -> None:
        relativo = entrada.tipo_tempo == TEMPO_RELATIVO
        passo.tipo_tempo = entrada.tipo_tempo
        passo.hora_fixa = None if relativo else entrada.hora_fixa
        passo.data_fixa = None if relativo else entrada.data_fixa
        passo.offset_segundos = offset_para_segundos(
            entrada.offset_valor, entrada.offset_unidade) if relativo else None
        passo.offset_unidade = entrada.offset_unidade if relativo else None
        passo.tipo_conteudo = entrada.tipo_conteudo
        passo.texto = entrada.texto
        passo.midia_url = entrada.midia_url
        passo.oferta_url = entrada.oferta_url
        passo.template_id = entrada.template_id
        passo.acao = entrada.acao
        passo.acao_parametro = entrada.acao_parametro
        passo.acao_descontinuada = bool(
            entrada.tipo_conteudo == CONTEUDO_ACAO
            and entrada.acao not in ACOES_VALIDAS
        )
        passo.grupos_alvo = entrada.grupos_alvo
        passo.grupos_alvo_ids = entrada.grupos_alvo_ids
        passo.marcar_todos = entrada.marcar_todos

    def _definir_blocos(self, passo: RoteiroPasso, entrada) -> None:
        """Ação é EXCLUSIVA: uma por passo, sem blocos. Só passo de mensagem
        aceita múltiplos.

        `blocos_enviados` é um índice POSICIONAL na lista de blocos, e aqui a
        lista é substituída. Uma linha que parou no bloco 2 de uma lista antiga
        retomaria do "bloco 3" de uma lista nova — que pode ser outro conteúdo.
        Trocar os blocos zera a retomada: pior repetir do começo (a linha está
        `falhou` e o reenvio é manual) do que mandar o bloco errado.
        """
        if self._blocos_atuais(passo.id) != self._blocos_de(entrada):
            self.repo.zerar_retomada_dos_blocos(passo.id)
        self.repo.remover_blocos(passo.id)
        if passo.tipo_conteudo != CONTEUDO_MENSAGEM:
            return
        for i, bloco in enumerate(entrada.blocos or [], start=1):
            self.db.add(PassoBloco(
                passo_id=passo.id, ordem=i, tipo=bloco.tipo,
                conteudo=bloco.conteudo, legenda=bloco.legenda,
                template_id=bloco.template_id,
            ))

    def _validar_horarios_para_salvar(self, roteiro: Roteiro,
                                      intocaveis: Optional[set] = None) -> None:
        """Passo no passado bloqueia o SALVAR, não só o agendar. Sem isso a
        tela aceitaria a alteração e só reclamaria no fim, depois de 22 passos
        digitados.

        **Passo que JÁ SAIU não conta.** Ele está legitimamente no passado — foi
        entregue. Contá-lo congelava o roteiro inteiro assim que o primeiro
        passo disparava: qualquer salvar seguinte era recusado com
        `PassosNoPassado` apontando um passo que a regra nem deixa editar. Isto
        é, a funcionalidade que esta rodada existe para entregar (editar o
        roteiro agendado) ficava impossível na segunda hora do lançamento.
        """
        passos = self.repo.passos(roteiro.id)
        if not passos:
            return
        travados = intocaveis if intocaveis is not None else self.passos_intocaveis(
            self.execucao_ativa(roteiro.id)
        )
        resolvidos, _ = resolver_horarios(passos)
        atrasados = [p.ordem for p, momento in resolvidos
                     if p.id not in travados
                     and momento < datetime.now(timezone.utc)]
        if atrasados:
            self.db.rollback()
            raise PassosNoPassado(atrasados)

    # --- leitura / composição ----------------------------------------------

    def grupos_do_passo(self, roteiro: Roteiro, passo: RoteiroPasso) -> List:
        """Grupos-alvo do passo. Sem permissão de envio ≠ excluído: a linha
        nasce `pulado` (transparência no relatório) — decidido na materialização."""
        if roteiro.campanha_id:
            vinculos = CampanhaGruposRepository(self.db).vinculos(roteiro.campanha_id)
            ids_da_campanha = [v.grupo_id for v in vinculos]
            grupos = [
                g for g in self.repo_grupos.por_usuario(roteiro.user_id,
                                                        apenas_ativos=False)
                if g.id in set(ids_da_campanha)
            ]
            ordem = {gid: i for i, gid in enumerate(ids_da_campanha)}
            grupos.sort(key=lambda g: ordem.get(g.id, 1_000_000))
        else:
            # apenas_ativos=False: inativo entra como linha `pulado` no
            # relatório em vez de sumir em silêncio.
            grupos = self.repo_grupos.por_usuario(roteiro.user_id,
                                                  apenas_ativos=False)

        if passo.grupos_alvo == "selecao":
            alvo = set(passo.grupos_alvo_ids or [])
            grupos = [g for g in grupos if g.id in alvo]
        return grupos

    def preview(self, roteiro: Roteiro, data_ancora: Optional[date] = None) -> Dict:
        passos = self.repo.passos(roteiro.id)
        if not passos:
            raise RoteiroInvalido("O roteiro não tem passos.")
        resolvidos, avisos = resolver_horarios(passos, data_ancora)
        atrasados = ordens_no_passado(resolvidos)
        linhas = []
        total = 0
        for passo, momento in resolvidos:
            n = len(self.grupos_do_passo(roteiro, passo))
            blocos = max(len(self.repo.blocos(passo.id)), 1)
            total += n
            linhas.append({
                "passo_id": passo.id,
                "ordem": passo.ordem,
                "tipo_conteudo": passo.tipo_conteudo,
                "quando": momento.isoformat(),
                "grupos": n,
                "blocos": blocos if passo.tipo_conteudo == CONTEUDO_MENSAGEM else 1,
                "no_passado": passo.ordem in atrasados,
            })
        return {
            "passos": linhas,
            "total_mensagens": total,
            "duracao_estimada_s": estimativa_de_duracao_s(total),
            "avisos": avisos,
            "passos_no_passado": atrasados,
        }

    # --- status por passo (da ÚLTIMA execução) ------------------------------

    def status_dos_passos(self, roteiro: Roteiro) -> Dict:
        """Status de cada passo na última execução, com os grupos que falharam.

        Antes de rodar, o passo NÃO tem status — mostra só o horário previsto.
        Duplicar gera cópia sem status (execução nova); reagendar substitui os
        status da execução anterior (a última passa a ser a nova).
        """
        execucao = self.ultima_execucao(roteiro.id)
        if execucao is None:
            return {"execucao": None, "passos": {}}

        nomes = {g.id: g.nome for g in
                 self.repo_grupos.por_usuario(roteiro.user_id, apenas_ativos=False)}
        linhas = (
            self.db.query(RoteiroMensagem)
            .filter(RoteiroMensagem.execucao_id == execucao.id)
            .order_by(RoteiroMensagem.id)
            .all()
        )
        por_passo: Dict[int, Dict] = {}
        for m in linhas:
            info = por_passo.setdefault(m.passo_id, {
                "enviados": 0, "pendentes": 0, "falhas": [],
            })
            if m.status == MSG_ENVIADA:
                info["enviados"] += 1
            elif m.status in (MSG_PENDENTE, MSG_ENVIANDO):
                info["pendentes"] += 1
            else:
                info["falhas"].append({
                    "grupo_id": m.grupo_id,
                    "nome": nomes.get(m.grupo_id) or "(grupo sem nome)",
                    "motivo": motivo_em_portugues(m.erro_motivo),
                })

        resultado: Dict[int, Dict] = {}
        for passo_id, info in por_passo.items():
            enviados, falhas = info["enviados"], info["falhas"]
            # "Antes de rodar, o passo NÃO tem status." Enquanto sobrar linha
            # pendente o passo não terminou — e ele pode nascer com linhas
            # `pulado` na MATERIALIZAÇÃO (grupo desativado, sem admin), horas
            # antes de disparar. Sem esta guarda o passo aparecia "Falhou" ou
            # "Concluído com falhas" na hora em que ela agendava.
            if info["pendentes"]:
                continue
            if falhas and not enviados:
                status = "falhou"
            elif falhas:
                status = "concluido_com_falhas"
            elif enviados:
                status = "concluido"
            else:
                continue
            resultado[passo_id] = {
                "status": status,
                "enviados": enviados,
                "pendentes": info["pendentes"],
                "falhas": falhas,
            }
        return {"execucao": execucao, "passos": resultado}

    # --- agendar ------------------------------------------------------------

    def agendar(self, roteiro: Roteiro, data_ancora: Optional[date] = None,
                ignorar_avisos: bool = False,
                agora: Optional[datetime] = None
                ) -> Tuple[Optional[RoteiroExecucao], List[str]]:
        ativa = self.execucao_ativa(roteiro.id)
        if ativa is not None:
            raise ExecucaoJaAtiva(ativa.id)

        passos = self.repo.passos(roteiro.id)
        if not passos:
            raise RoteiroInvalido("O roteiro não tem passos.")
        resolvidos, avisos = resolver_horarios(passos, data_ancora)

        # Agendar cria execução NOVA: aqui nenhum passo "já saiu" nesta corrida,
        # e todos precisam estar no futuro.
        atrasados = ordens_no_passado(resolvidos, agora)
        if atrasados:
            raise PassosNoPassado(atrasados)
        if avisos and not ignorar_avisos:
            return None, avisos   # a rota devolve 422 com os avisos

        execucao = RoteiroExecucao(
            roteiro_id=roteiro.id,
            user_id=roteiro.user_id,
            data_ancora=data_ancora or min(m for _, m in resolvidos).astimezone(BRT).date(),
            status=EXEC_AGENDADA,
        )
        self.repo.adicionar(execucao)

        mensagens = self._materializar(roteiro, execucao, resolvidos)
        if not mensagens:
            raise RoteiroInvalido("Nenhum grupo-alvo para este roteiro.")
        self.repo.materializar_mensagens(mensagens)
        self._fechar_agendamento(execucao, mensagens)

        # A listagem para de mentir "Rascunho": o chip passa a refletir a
        # execução e o botão "Agendar" sai da linha.
        roteiro.status = "pronto"
        self.db.add(roteiro)
        self.db.commit()
        return execucao, avisos

    def _materializar(self, roteiro: Roteiro, execucao: RoteiroExecucao,
                      resolvidos) -> List[RoteiroMensagem]:
        admin_por_grupo = self._admin_por_grupo(roteiro.user_id)
        mensagens: List[RoteiroMensagem] = []
        for passo, momento in resolvidos:
            for grupo in self.grupos_do_passo(roteiro, passo):
                status, motivo = self._destino_inicial(passo, grupo, admin_por_grupo)
                mensagens.append(RoteiroMensagem(
                    execucao_id=execucao.id,
                    passo_id=passo.id,
                    grupo_id=grupo.id,
                    user_id=roteiro.user_id,
                    agendado_para=momento,
                    status=status,
                    erro_motivo=motivo,
                ))
        return mensagens

    def _destino_inicial(self, passo: RoteiroPasso, grupo,
                         admin_por_grupo: Dict[int, bool]) -> Tuple[str, Optional[str]]:
        if not grupo.ativo:
            return MSG_PULADA, "grupo_inativo"
        if not grupo.ativado:
            # Toggle da usuária (spec §6.3): grupo desativado NUNCA recebe
            # envio — nem ação. A linha nasce `pulado` para constar no relatório.
            return MSG_PULADA, "grupo_desativado"
        if passo.tipo_conteudo == CONTEUDO_ACAO:
            if passo.acao_descontinuada or passo.acao not in ACOES_VALIDAS:
                return MSG_PULADA, "acao_descontinuada"
            if not admin_por_grupo.get(grupo.id, False):
                # Toda ação de grupo exige admin — e "ser admin" é POR NÚMERO
                # (whatsapp_grupo_instancias), não o flag agregado do grupo:
                # com 2 números, o último sync sobrescreveria. Basta UM número
                # admin: o motor faz failover entre eles.
                return MSG_PULADA, "sem_admin"
            return MSG_PENDENTE, None
        if not grupo.permite_envio:
            return MSG_PULADA, "sem_permissao"
        return MSG_PENDENTE, None

    def _fechar_agendamento(self, execucao: RoteiroExecucao,
                            mensagens: Sequence[RoteiroMensagem]) -> None:
        pendentes = [m for m in mensagens if m.status == MSG_PENDENTE]
        execucao.total = len(mensagens)
        execucao.pulados = len(mensagens) - len(pendentes)
        if pendentes:
            execucao.proxima_execucao_em = min(m.agendado_para for m in pendentes)
        else:
            # Tudo pulado (ex.: nenhum grupo permite envio): concluir JÁ —
            # "agendada" com proxima NULL seria invisível ao tick para sempre.
            execucao.status = EXEC_CONCLUIDA
            execucao.concluido_em = datetime.now(timezone.utc)

    def _admin_por_grupo(self, user_id: int) -> Dict[int, bool]:
        """grupo_id → algum número da afiliada é admin ali (vínculo N:N)."""
        from app.models.whatsapp_grupos import WhatsappGrupo, WhatsappGrupoInstancia

        linhas = (
            self.db.query(WhatsappGrupoInstancia.grupo_id,
                          WhatsappGrupoInstancia.sou_admin)
            .join(WhatsappGrupo, WhatsappGrupo.id == WhatsappGrupoInstancia.grupo_id)
            .filter(WhatsappGrupo.user_id == user_id)
            .all()
        )
        mapa: Dict[int, bool] = {}
        for grupo_id, sou_admin in linhas:
            mapa[grupo_id] = mapa.get(grupo_id, False) or bool(sou_admin)
        return mapa

    # --- reagendar as pendentes de uma execução viva ------------------------

    def resincronizar_execucao(self, roteiro: Roteiro,
                               execucao: RoteiroExecucao) -> None:
        """Aplica os passos atuais à execução em andamento, mexendo SÓ no que
        ainda não saiu.

        Cobre os três casos reais: adicionar passo no fim, corrigir o texto de
        uma mensagem que ainda não saiu, e empurrar o resto do lançamento
        quando algo atrasa.
        """
        passos = self.repo.passos(roteiro.id)
        if not passos:
            return
        resolvidos, _ = resolver_horarios(passos)
        admin_por_grupo = self._admin_por_grupo(roteiro.user_id)

        vivas = (
            self.db.query(RoteiroMensagem)
            .filter(RoteiroMensagem.execucao_id == execucao.id)
            .all()
        )
        por_chave = {(m.passo_id, m.grupo_id): m for m in vivas}
        mantidas = set()
        novas: List[RoteiroMensagem] = []

        for passo, momento in resolvidos:
            for grupo in self.grupos_do_passo(roteiro, passo):
                chave = (passo.id, grupo.id)
                atual = por_chave.get(chave)
                if atual is None:
                    status, motivo = self._destino_inicial(passo, grupo, admin_por_grupo)
                    novas.append(RoteiroMensagem(
                        execucao_id=execucao.id, passo_id=passo.id,
                        grupo_id=grupo.id, user_id=roteiro.user_id,
                        agendado_para=momento, status=status, erro_motivo=motivo,
                    ))
                    mantidas.add(chave)
                    continue
                mantidas.add(chave)
                if atual.status == MSG_PENDENTE:
                    # O texto é congelado no disparo, então basta o horário.
                    atual.agendado_para = momento
                    self.db.add(atual)

        # Grupo que saiu do alvo: só a linha ainda pendente some. Enviada é
        # histórico — apagar reescreveria o que já aconteceu no grupo.
        for chave, m in por_chave.items():
            if chave not in mantidas and m.status == MSG_PENDENTE:
                self.db.delete(m)

        if novas:
            self.repo.materializar_mensagens(novas)
        self.db.flush()

        proxima = self.repo.proxima_pendente_em(execucao.id)
        if proxima is None:
            if execucao.status == EXEC_AGENDADA:
                execucao.status = EXEC_CONCLUIDA
                execucao.concluido_em = datetime.now(timezone.utc)
                execucao.proxima_execucao_em = None
        else:
            execucao.proxima_execucao_em = proxima
            if execucao.status == EXEC_CONCLUIDA:
                execucao.status = EXEC_AGENDADA
                execucao.concluido_em = None
        self._recontar(execucao)
        self.db.commit()

    def _recontar(self, execucao: RoteiroExecucao) -> None:
        from app.models.roteiro import MSG_FALHOU
        c = self.repo.contadores(execucao.id)
        execucao.enviados = c.get(MSG_ENVIADA, 0)
        execucao.erros = c.get(MSG_FALHOU, 0)
        execucao.pulados = c.get(MSG_PULADA, 0)
        execucao.total = sum(c.values())
        self.db.add(execucao)

    # --- reenvio manual -----------------------------------------------------

    def reenviar(self, roteiro: Roteiro, execucao: RoteiroExecucao,
                 passo_id: int, grupo_ids: Sequence[int]) -> int:
        """Reenvia um passo aos grupos escolhidos.

        Manual, NUNCA automático: mensagem duplicada em grupo é erro que ela vê
        e que o WhatsApp pune — quem decide repetir é quem olhou o motivo.

        A linha é RESSUSCITADA (volta a `pendente`) em vez de duplicada: o
        `uq_roteiro_msg` existe justamente para que (execução, passo, grupo)
        seja único, e o status que a tela mostra é o da última tentativa.
        `blocos_enviados` é preservado — retomar do bloco 3 é o que evita
        reenviar os blocos 1 e 2 no grupo.
        """
        passo = self.repo.passo_por_id(passo_id)
        if passo is None or passo.roteiro_id != roteiro.id:
            raise RoteiroInvalido("Passo não encontrado.")
        if not grupo_ids:
            raise RoteiroInvalido("Escolha ao menos um grupo.")

        agora = datetime.now(timezone.utc)
        linhas = (
            self.db.query(RoteiroMensagem)
            .filter(RoteiroMensagem.execucao_id == execucao.id,
                    RoteiroMensagem.passo_id == passo_id,
                    RoteiroMensagem.grupo_id.in_(list(grupo_ids)))
            .all()
        )
        reabertas = 0
        for m in linhas:
            if m.status in (MSG_ENVIADA, MSG_ENVIANDO):
                continue        # já saiu: reenviar daqui seria duplicar
            m.status = MSG_PENDENTE
            m.erro_motivo = None
            m.agendado_para = agora
            self.db.add(m)
            reabertas += 1
        if not reabertas:
            raise RoteiroInvalido("Nenhum grupo para reenviar.")

        execucao.status = EXEC_AGENDADA
        execucao.proxima_execucao_em = agora
        execucao.concluido_em = None
        self._recontar(execucao)
        self.db.commit()
        return reabertas

    # --- envio rápido -------------------------------------------------------

    def criar_envio_rapido(self, user_id: int, texto: Optional[str],
                           midia_url: Optional[str], oferta_url: Optional[str],
                           grupo_ids: List[int],
                           campanha_id: Optional[int] = None) -> Roteiro:
        """Roteiro de 1 passo — mesma tabela, mesmo motor (spec §9.4)."""
        if not (texto or midia_url or oferta_url):
            raise RoteiroInvalido("Informe um texto, uma imagem ou um link de oferta.")
        if not grupo_ids:
            raise RoteiroInvalido("Escolha ao menos um grupo.")
        agora_brt = datetime.now(BRT)
        roteiro = self.repo.adicionar(Roteiro(
            user_id=user_id,
            campanha_id=campanha_id,
            nome=f"Envio rápido {agora_brt:%d/%m %H:%M}",
            status="pronto",
            origem=ORIGEM_ENVIO_RAPIDO,
        ))
        oferta = bool(oferta_url)
        passo = self.repo.adicionar(RoteiroPasso(
            roteiro_id=roteiro.id, ordem=1,
            tipo_tempo=TEMPO_ANCORA, hora_fixa=agora_brt.time(),
            data_fixa=agora_brt.date(),
            tipo_conteudo="oferta" if oferta else CONTEUDO_MENSAGEM,
            texto=texto, midia_url=midia_url, oferta_url=oferta_url,
            grupos_alvo="selecao", grupos_alvo_ids=list(grupo_ids),
        ))
        self.db.flush()
        if not oferta:
            self.db.add(PassoBloco(
                passo_id=passo.id, ordem=1,
                tipo=BLOCO_IMAGEM if midia_url else BLOCO_TEXTO,
                conteudo=midia_url or texto,
                legenda=texto if midia_url else None,
            ))
        self.db.commit()
        return roteiro

    # --- duplicar (funcionalidade central p/ lançamento, spec §9.3) ---------

    def duplicar(self, roteiro: Roteiro) -> Roteiro:
        """Cópia SEM status: a execução é da corrida, não do template.

        As datas fixas vêm junto, de propósito. Antes elas eram zeradas porque
        a âncora global as preenchia; agora um passo sem data é passo inválido.
        Copiadas, o roteiro nasce coerente e com os passos no passado em
        vermelho — que é o empurrão para a tela de ajuste em bloco.
        """
        novo = self.repo.adicionar(Roteiro(
            user_id=roteiro.user_id,
            campanha_id=roteiro.campanha_id,
            nome=f"{roteiro.nome} (cópia)"[:120],
            status="rascunho",
            origem=roteiro.origem,
        ))
        for p in self.repo.passos(roteiro.id):
            copia = self.repo.adicionar(RoteiroPasso(
                roteiro_id=novo.id, ordem=p.ordem, tipo_tempo=p.tipo_tempo,
                hora_fixa=p.hora_fixa, data_fixa=p.data_fixa,
                offset_segundos=p.offset_segundos, offset_unidade=p.offset_unidade,
                tipo_conteudo=p.tipo_conteudo, texto=p.texto,
                midia_url=p.midia_url, oferta_url=p.oferta_url,
                template_id=p.template_id, acao=p.acao,
                acao_parametro=p.acao_parametro,
                acao_descontinuada=p.acao_descontinuada,
                grupos_alvo=p.grupos_alvo, grupos_alvo_ids=p.grupos_alvo_ids,
                marcar_todos=p.marcar_todos,
            ))
            self.db.flush()
            for b in self.repo.blocos(p.id):
                self.db.add(PassoBloco(
                    passo_id=copia.id, ordem=b.ordem, tipo=b.tipo,
                    conteudo=b.conteudo, legenda=b.legenda,
                    template_id=b.template_id,
                ))
        self.db.commit()
        return novo

    # --- ajuste das datas em bloco (o que torna duplicar barato) ------------

    def ajustar_datas(self, roteiro: Roteiro, datas: Dict[int, Tuple]) -> None:
        """Troca a data (e opcionalmente a hora) de vários passos de âncora de
        uma vez.

        Existe porque duplicar um roteiro de 22 passos e abrir modal por modal
        para trocar 4 ou 5 datas é onde o erro acontece — e o erro aqui agenda
        o lançamento para uma data que já passou.
        """
        execucao = self.execucao_ativa(roteiro.id)
        intocaveis = self.passos_intocaveis(execucao)
        atuais = {p.id: p for p in self.repo.passos(roteiro.id)}

        travados = sorted(atuais[pid].ordem for pid in datas
                          if pid in intocaveis and pid in atuais)
        if travados:
            raise PassoJaEnviado(travados)

        for passo_id, (data_fixa, hora_fixa) in datas.items():
            passo = atuais.get(passo_id)
            if passo is None:
                raise RoteiroInvalido("Passo não encontrado.")
            if passo.tipo_tempo != TEMPO_ANCORA:
                raise RoteiroInvalido(
                    f"O passo {passo.ordem} não tem hora fixa — nada a ajustar."
                )
            passo.data_fixa = data_fixa
            if hora_fixa is not None:
                passo.hora_fixa = hora_fixa
            self.db.add(passo)

        self.db.flush()
        self._validar_horarios_para_salvar(roteiro, intocaveis)
        self.db.commit()
        if execucao is not None:
            self.resincronizar_execucao(roteiro, execucao)
