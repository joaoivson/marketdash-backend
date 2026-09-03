"""
Janela de horário de envio por usuária (D4 — spec §14.2).

Config em `user_settings.whatsapp_envio_config` (JSONB), shape validado aqui;
ausente/None = restrição DESLIGADA (spec §7.1: o padrão é não travar nada —
quem quiser janela liga o toggle e ganha 08:00–22:00 para editar). As regras
de borda do worker (spec §7.4):

  * a janela é decidida UMA vez, no INÍCIO da fatia: fatia que começa dentro
    CONCLUI, mesmo ultrapassando o fim;
  * fatia que começa fora da janela → a execução volta a `agendada` com
    `proxima_execucao_em` = próxima abertura;
  * execução agendada para fora da janela dispara na abertura.

Dia civil é BRT — nunca UTC, nunca o fuso da sessão do Postgres.
"""
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

BRT = ZoneInfo("America/Sao_Paulo")

PADRAO_INICIO = time(8, 0)
PADRAO_FIM = time(22, 0)


class JanelaDia(BaseModel):
    ativo: bool = True
    inicio: time = PADRAO_INICIO
    fim: time = PADRAO_FIM
    pausa_inicio: Optional[time] = None
    pausa_fim: Optional[time] = None

    @field_validator("fim")
    @classmethod
    def _fim_depois_do_inicio(cls, v, info):
        inicio = info.data.get("inicio")
        if inicio is not None and v <= inicio:
            raise ValueError("O fim da janela precisa ser depois do início.")
        return v


class ConfigJanela(BaseModel):
    # "0".."6" = segunda..domingo (isoweekday()-1)
    # Default DESLIGADO (spec §7.1): config ausente ou sem o campo = sem
    # trava de horário. `janela_aberta` devolve True enquanto `ativo=False`.
    ativo: bool = False
    dias: dict[str, JanelaDia] = Field(default_factory=dict)

    def dia(self, indice: int) -> JanelaDia:
        return self.dias.get(str(indice)) or JanelaDia()


def carregar_config(bruto: Optional[dict]) -> ConfigJanela:
    """JSONB cru → config validada. Lixo no banco degrada para o padrão
    (que agora é SEM restrição — degradar nunca pode travar envio)."""
    if not bruto:
        return ConfigJanela()
    try:
        return ConfigJanela.model_validate(bruto)
    except Exception:
        return ConfigJanela()


def _dentro(j: JanelaDia, hora: time) -> bool:
    if not j.ativo:
        return False
    if not (j.inicio <= hora < j.fim):
        return False
    if j.pausa_inicio and j.pausa_fim and j.pausa_inicio <= hora < j.pausa_fim:
        return False
    return True


def janela_aberta(config: ConfigJanela, momento: Optional[datetime] = None) -> bool:
    if not config.ativo:
        return True   # toggle geral desligado = sem restrição
    agora = (momento or datetime.now(BRT)).astimezone(BRT)
    return _dentro(config.dia(agora.isoweekday() - 1), agora.time())


def proxima_abertura(config: ConfigJanela,
                     momento: Optional[datetime] = None) -> Optional[datetime]:
    """Próximo instante em que a janela abre (>= momento); o próprio momento
    se já está aberta; **None quando a janela NUNCA abre** (todos os dias
    inativos) — devolver "agora" criaria um livelock de 5 em 5 minutos no
    tick, com a execução pingue-pongueando agendada↔enviando para sempre."""
    agora = (momento or datetime.now(BRT)).astimezone(BRT)
    if janela_aberta(config, agora):
        return agora
    for delta in range(0, 8):
        dia = agora + timedelta(days=delta)
        j = config.dia(dia.isoweekday() - 1)
        if not j.ativo:
            continue
        candidatos = [j.inicio]
        if j.pausa_fim:
            candidatos.append(j.pausa_fim)
        for hora in sorted(candidatos):
            candidato = dia.replace(hour=hora.hour, minute=hora.minute,
                                    second=0, microsecond=0)
            if candidato >= agora and _dentro(j, candidato.time()):
                return candidato
    return None


def config_da_usuaria(db, user_id: int) -> ConfigJanela:
    from app.models.user_settings import UserSettings

    us = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    return carregar_config(getattr(us, "whatsapp_envio_config", None))


def salvar_config_da_usuaria(db, user_id: int, payload: dict) -> ConfigJanela:
    from app.models.user_settings import UserSettings

    config = ConfigJanela.model_validate(payload)
    if config.ativo and config.dias and not any(
        d.ativo for d in config.dias.values()
    ):
        # Janela que nunca abre = livelock/pausa garantida — barrar no salvar.
        raise ValueError("Ative pelo menos um dia, ou desligue a restrição de horário.")
    us = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if not us:
        us = UserSettings(user_id=user_id)
        db.add(us)
    us.whatsapp_envio_config = config.model_dump(mode="json")
    db.commit()
    return config
