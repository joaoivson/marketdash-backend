"""
Opt-in do resumo diário: cadastrar o número, confirmar por código, desligar.

A confirmação existe por um motivo concreto: sem ela, um dígito errado no
cadastro faz o MarketDash mandar mensagem diária para um desconhecido. Uma
denúncia basta para o número ser restringido, e aí ninguém recebe resumo.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.whatsapp import (
    ENVIO_FALHOU, ENVIO_OK, ORIGEM_APP, ORIGEM_WHATSAPP, STATUS_CONFIRMADO,
    STATUS_DESLIGADO, STATUS_PENDENTE, TIPO_CONFIRMACAO, WhatsappOptin,
)
from app.services.evolution_client import ErroWhatsapp, mascarar, normalizar_numero

logger = logging.getLogger(__name__)

VALIDADE_DO_CODIGO = timedelta(minutes=15)
MAXIMO_DE_TENTATIVAS = 5

# Palavras que desligam o envio quando chegam pelo próprio WhatsApp.
PALAVRAS_DE_SAIDA = {"sair", "parar", "cancelar", "stop", "descadastrar"}


class WhatsappIndisponivel(Exception):
    pass


class CodigoInvalido(Exception):
    pass


class TentativasEsgotadas(Exception):
    pass


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def texto_de_confirmacao(codigo: str) -> str:
    return (
        f"Seu código do MarketDash é *{codigo}*.\n\n"
        "Digite ele no site para começar a receber o resumo diário por aqui.\n"
        "Se não foi você quem pediu, é só ignorar esta mensagem."
    )


class WhatsappOptinService:
    def __init__(self, repo, cliente):
        self.repo = repo
        self.cliente = cliente

    def status(self, user_id: int) -> dict:
        optin = self.repo.por_usuario(user_id)
        return {
            "status": optin.status if optin else STATUS_DESLIGADO,
            "numero": mascarar(optin.numero) if optin else None,
            "disponivel": self.cliente.configurado(),
            "confirmado_em": optin.confirmado_em if optin else None,
        }

    def registrar(self, user_id: int, numero_bruto: str) -> dict:
        """Guarda o número como pendente e manda o código. Erro de envio não grava nada."""
        if not self.cliente.configurado():
            raise WhatsappIndisponivel()

        numero = normalizar_numero(numero_bruto)  # ValueError vira 400 na rota
        codigo = f"{secrets.randbelow(1_000_000):06d}"

        optin = self.repo.por_usuario(user_id) or WhatsappOptin(user_id=user_id)
        optin.numero = numero
        optin.status = STATUS_PENDENTE
        optin.codigo = codigo
        optin.codigo_expira_em = _agora() + VALIDADE_DO_CODIGO
        optin.tentativas = 0
        optin.confirmado_em = None
        optin.desligado_em = None
        optin.desligado_por = None

        try:
            self.cliente.enviar_texto(numero, texto_de_confirmacao(codigo))
        except ErroWhatsapp as e:
            # Não grava opt-in que não conseguiu nem receber o código: deixaria
            # uma linha pendente eterna com um número que talvez nem exista.
            logger.warning("Código não enviado para %s (%s)", mascarar(numero), e.motivo)
            self.repo.registrar_envio(user_id, TIPO_CONFIRMACAO, ENVIO_FALHOU, erro=e.motivo)
            raise

        self.repo.salvar(optin)
        self.repo.registrar_envio(user_id, TIPO_CONFIRMACAO, ENVIO_OK)
        return {"status": STATUS_PENDENTE, "numero": mascarar(numero)}

    def confirmar(self, user_id: int, codigo: str) -> dict:
        optin = self.repo.por_usuario(user_id)
        if not optin or optin.status == STATUS_DESLIGADO or not optin.codigo:
            raise CodigoInvalido()
        if optin.tentativas >= MAXIMO_DE_TENTATIVAS:
            raise TentativasEsgotadas()
        if optin.codigo_expira_em and optin.codigo_expira_em < _agora():
            raise CodigoInvalido()

        if (codigo or "").strip() != optin.codigo:
            # Conta a tentativa ANTES de recusar: sem isso, seis dígitos são
            # 1 milhão de combinações que alguém pode varrer à vontade.
            optin.tentativas += 1
            self.repo.salvar(optin)
            raise CodigoInvalido()

        optin.status = STATUS_CONFIRMADO
        optin.confirmado_em = _agora()
        optin.codigo = None
        optin.codigo_expira_em = None
        optin.tentativas = 0
        self.repo.salvar(optin)
        return {"status": STATUS_CONFIRMADO, "numero": mascarar(optin.numero)}

    def desligar(self, user_id: int, origem: str = ORIGEM_APP) -> dict:
        optin = self.repo.por_usuario(user_id)
        if not optin:
            return {"status": STATUS_DESLIGADO}
        optin.status = STATUS_DESLIGADO
        optin.desligado_em = _agora()
        optin.desligado_por = origem
        optin.codigo = None
        self.repo.salvar(optin)
        return {"status": STATUS_DESLIGADO}

    def desligar_por_numero(self, numero: str) -> int:
        """
        Caminho do SAIR. Desliga todas as contas com aquele número — ver o
        comentário em `WhatsappRepository.por_numero`.
        """
        desligados = 0
        for optin in self.repo.por_numero(numero):
            if optin.status == STATUS_DESLIGADO:
                continue
            optin.status = STATUS_DESLIGADO
            optin.desligado_em = _agora()
            optin.desligado_por = ORIGEM_WHATSAPP
            optin.codigo = None
            self.repo.salvar(optin)
            desligados += 1
        return desligados


def pediu_para_sair(texto: Optional[str]) -> bool:
    """
    "SAIR", "sair.", "quero SAIR" — todas contam.

    Prefere errar para o lado de desligar: parar de mandar para quem queria
    receber custa um clique; continuar mandando para quem pediu para parar
    custa o número.
    """
    if not texto:
        return False
    limpo = "".join(c for c in texto.lower() if c.isalpha() or c.isspace()).strip()
    return any(p in limpo.split() for p in PALAVRAS_DE_SAIDA)
