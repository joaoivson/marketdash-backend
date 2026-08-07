"""
O lote diário: percorre quem optou por receber e manda o resumo.

Aqui moram as travas anti-banimento. A ordem importa — cada uma protege de um
jeito diferente de perder o número:

  ritmo     mensagem em rajada é o padrão que o WhatsApp reconhece como robô;
  teto      limita o estrago de um bug que resolva mandar para todo mundo;
  disjuntor número desconectado ou banido devolve erro em série — insistir
            gasta tentativa e piora a situação dele.

Idempotência é do banco: o índice único de (user_id, tipo, dia) impede que uma
segunda execução do cron mande a mesma mensagem de novo.
"""
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, List, Optional

from app.core.config import settings
from app.models.whatsapp import (
    ENVIO_FALHOU, ENVIO_OK, ORIGEM_FALHA, STATUS_DESLIGADO, TIPO_RESUMO,
)
from app.services.evolution_client import ErroWhatsapp, mascarar
from app.services.whatsapp_resumo_service import WhatsappResumoService, dia_do_resumo

logger = logging.getLogger(__name__)

PLANOS_COM_RESUMO = ("pro", "max")


@dataclass
class ResultadoDoLote:
    dia: Optional[date] = None
    enviados: int = 0
    pulados: int = 0
    falhas: int = 0
    desligados: int = 0
    interrompido_por: Optional[str] = None
    detalhes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "dia": self.dia.isoformat() if self.dia else None,
            "enviados": self.enviados,
            "pulados": self.pulados,
            "falhas": self.falhas,
            "desligados": self.desligados,
            "interrompido_por": self.interrompido_por,
        }


class WhatsappEnvioService:
    def __init__(self, db, repo, cliente, buscar_usuario: Callable[[int], object],
                 dormir: Callable[[float], None] = time.sleep):
        self.db = db
        self.repo = repo
        self.cliente = cliente
        self.buscar_usuario = buscar_usuario
        self.dormir = dormir   # injetável: nos testes não se espera 8 segundos
        self.resumo_svc = WhatsappResumoService(db)

    def _primeiro_nome(self, user_id: int) -> str:
        try:
            u = self.buscar_usuario(user_id)
            nome = (getattr(u, "name", None) or "").strip()
            return nome.split()[0] if nome else "tudo bem"
        except Exception:
            return "tudo bem"

    def enviar_lote(self, hoje: Optional[date] = None,
                    apenas_user_id: Optional[int] = None) -> ResultadoDoLote:
        dia = dia_do_resumo(hoje)
        r = ResultadoDoLote(dia=dia)

        if not self.cliente.configurado():
            r.interrompido_por = "sem_config"
            return r
        if not self.cliente.conectado():
            # Sem esta checagem, cada afiliada do lote geraria uma tentativa
            # falha própria e o log encheria de ruído pelo mesmo motivo.
            r.interrompido_por = "desconectado"
            logger.error("Resumo diário: instância do WhatsApp não está conectada")
            return r

        candidatos = self.repo.confirmados_dos_planos(PLANOS_COM_RESUMO)
        if apenas_user_id is not None:
            candidatos = [c for c in candidatos if c[0].user_id == apenas_user_id]

        ja_hoje = self.repo.enviados_no_dia(date.today() if hoje is None else hoje)
        falhas_seguidas = 0

        for indice, (optin, _plano) in enumerate(candidatos):
            if ja_hoje + r.enviados >= settings.WHATSAPP_TETO_DIARIO:
                r.interrompido_por = "teto_diario"
                logger.warning("Resumo diário: teto de %s mensagens atingido",
                               settings.WHATSAPP_TETO_DIARIO)
                break

            if self.repo.ja_enviou(optin.user_id, TIPO_RESUMO, dia):
                r.pulados += 1
                continue

            try:
                resumo = self.resumo_svc.montar(
                    optin.user_id, self._primeiro_nome(optin.user_id), dia
                )
            except Exception:
                logger.exception("Resumo %s: falha ao montar o texto", optin.user_id)
                r.falhas += 1
                self.repo.registrar_envio(optin.user_id, TIPO_RESUMO, ENVIO_FALHOU,
                                          referencia=dia, erro="falha ao montar")
                continue

            # Ritmo humano — antes do envio, não depois, para que a primeira
            # mensagem do lote também não saia colada no gatilho do cron.
            if indice:
                self.dormir(random.uniform(settings.WHATSAPP_INTERVALO_MIN_S,
                                           settings.WHATSAPP_INTERVALO_MAX_S))

            try:
                self.cliente.enviar_texto(optin.numero, resumo.texto)
            except ErroWhatsapp as e:
                r.falhas += 1
                self.repo.registrar_envio(optin.user_id, TIPO_RESUMO, ENVIO_FALHOU,
                                          referencia=dia, erro=e.motivo)

                if e.motivo == "numero_invalido":
                    # Problema de UMA afiliada, não do número do MarketDash.
                    # Desliga para não repetir o erro todo dia; ela religa no app.
                    optin.status = STATUS_DESLIGADO
                    optin.desligado_por = ORIGEM_FALHA
                    self.repo.salvar(optin)
                    r.desligados += 1
                    falhas_seguidas = 0
                    continue

                falhas_seguidas += 1
                if e.fatal or falhas_seguidas >= settings.WHATSAPP_FALHAS_PARA_PARAR:
                    r.interrompido_por = e.motivo if e.fatal else "falhas_seguidas"
                    logger.error("Resumo diário interrompido: %s (%s falhas seguidas)",
                                 e.motivo, falhas_seguidas)
                    break
                continue

            falhas_seguidas = 0
            if self.repo.registrar_envio(optin.user_id, TIPO_RESUMO, ENVIO_OK, referencia=dia):
                r.enviados += 1
            else:
                # O índice único recusou: outra execução mandou primeiro. A
                # mensagem saiu duas vezes nesta rodada, mas o contador não
                # mente sobre isso.
                r.pulados += 1
                logger.warning("Envio duplicado detectado para %s em %s",
                               mascarar(optin.numero), dia)

        logger.info("Resumo diário de %s: %s", dia, r.to_dict())
        return r
