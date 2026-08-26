"""
Integrações de marketplace (F5): N provedores, N contas por provedor.

Duas coisas que a estrutura carrega:

1. **Sem "principal".** A integração certa vem do marketplace detectado na URL
   do produto. Só quando há 2+ ativas do MESMO provedor a afiliada escolhe —
   pelo `label`, no momento da conversão.
2. **A credencial é sempre da aluna.** A comissão segue a conta que assina a
   requisição; usar a chave da plataforma mandaria a comissão para o lugar
   errado. Já era regra do generateShortLink e continua valendo.

Migração em dois deploys: aqui a escrita já vai para as duas tabelas
(`integracoes` e a antiga `shopee_integrations`); a leitura vira no ciclo
seguinte. Enquanto isso, `credenciais_de` lê os dois formatos.
"""
import json
import logging
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.encryption import decrypt_value, encrypt_value
from app.models.integracao import PROVEDOR_SHOPEE, PROVEDORES, Integracao
from app.repositories.integracao_repository import IntegracaoRepository

logger = logging.getLogger(__name__)

# Host → provedor. Só marketplaces com API de afiliado assinada.
HOSTS_POR_PROVEDOR = {
    PROVEDOR_SHOPEE: ("shopee.com.br", "shopee."),
}


class ProvedorInvalido(Exception):
    pass


class IntegracaoNaoEncontrada(Exception):
    pass


class EscolhaNecessaria(Exception):
    """2+ integrações ativas do mesmo provedor: a afiliada escolhe o label."""

    def __init__(self, provedor: str, labels: List[str]):
        self.provedor = provedor
        self.labels = labels
        super().__init__(f"Escolha a conta {provedor}: {', '.join(labels)}")


def provedor_da_url(url: str) -> Optional[str]:
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return None
    for provedor, marcas in HOSTS_POR_PROVEDOR.items():
        if any(host == m or host.startswith(m) or f".{m}" in f".{host}" for m in marcas):
            return provedor
    return None


class IntegracaoService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = IntegracaoRepository(db)

    # --- leitura ------------------------------------------------------------

    def listar(self, user_id: int) -> List[Integracao]:
        return self.repo.por_usuario(user_id)

    def credenciais_de(self, integracao: Integracao) -> Dict[str, str]:
        """
        Decifra a credencial. Aceita os dois formatos que convivem durante a
        migração: o JSON do backfill (`{"app_id", "encrypted_password"}`, com o
        campo interno já cifrado) e o JSON cifrado inteiro dos registros novos.
        """
        bruto = integracao.credenciais or ""
        try:
            dados = json.loads(bruto)
        except (ValueError, TypeError):
            dados = json.loads(decrypt_value(bruto))
        if "encrypted_password" in dados:
            return {"app_id": str(dados.get("app_id") or ""),
                    "senha": decrypt_value(dados["encrypted_password"])}
        return {"app_id": str(dados.get("app_id") or ""),
                "senha": str(dados.get("senha") or "")}

    def resolver_para_url(self, user_id: int, url: str,
                          integracao_id: Optional[int] = None) -> Integracao:
        """A integração que assina a conversão desta URL."""
        if integracao_id is not None:
            escolhida = self.repo.por_id(user_id, integracao_id)
            if not escolhida or not escolhida.ativa:
                raise IntegracaoNaoEncontrada("Integração não encontrada.")
            return escolhida
        provedor = provedor_da_url(url)
        if not provedor:
            raise ProvedorInvalido("Não reconhecemos esse marketplace na URL.")
        return self.resolver(user_id, provedor)

    def resolver(self, user_id: int, provedor: str) -> Integracao:
        ativas = self.repo.por_usuario(user_id, provedor, apenas_ativas=True)
        if not ativas:
            raise IntegracaoNaoEncontrada(
                f"Nenhuma conta {provedor} conectada."
            )
        if len(ativas) > 1:
            raise EscolhaNecessaria(provedor, [i.label for i in ativas])
        return ativas[0]

    # --- escrita (dupla, deploy A) -----------------------------------------

    def salvar(self, user_id: int, provedor: str, label: str,
               app_id: str, senha: str) -> Integracao:
        if provedor not in PROVEDORES:
            raise ProvedorInvalido(f"Marketplace ainda não suportado: {provedor}")
        app_id = (app_id or "").strip()
        # AppID da Shopee é numérico — inválido dá falha opaca lá na frente.
        if provedor == PROVEDOR_SHOPEE and not app_id.isdigit():
            raise ProvedorInvalido("O App ID da Shopee é numérico.")
        label = (label or "").strip()[:64] or "principal"

        credenciais = encrypt_value(json.dumps({"app_id": app_id, "senha": senha}))
        integracao = self.repo.upsert(user_id, provedor, label, credenciais)

        # Dupla escrita: a leitura ainda vem da tabela antiga neste deploy.
        if provedor == PROVEDOR_SHOPEE:
            from app.repositories.shopee_integration_repository import (
                ShopeeIntegrationRepository,
            )
            ShopeeIntegrationRepository(self.db).upsert(
                user_id, app_id, encrypt_value(senha)
            )
        self.db.commit()
        return integracao

    def alternar(self, integracao: Integracao, ativa: bool) -> Integracao:
        integracao.ativa = ativa
        self.db.add(integracao)
        self.db.commit()
        return integracao

    def remover(self, integracao: Integracao) -> None:
        if integracao.provedor == PROVEDOR_SHOPEE:
            from app.repositories.shopee_integration_repository import (
                ShopeeIntegrationRepository,
            )
            # Só apaga o registro antigo quando ESTA era a última conta Shopee
            # — a tabela velha comporta uma só, e é dela que a leitura vive.
            outras = [
                i for i in self.repo.por_usuario(integracao.user_id, PROVEDOR_SHOPEE)
                if i.id != integracao.id
            ]
            if not outras:
                ShopeeIntegrationRepository(self.db).delete_by_user_id(integracao.user_id)
        self.repo.remover(integracao)
        self.db.commit()
