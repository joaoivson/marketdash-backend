"""
Variações de template: sorteio ponderado + placeholders (spec §4.10).

Variar o texto é anti-ban — string idêntica em 20 grupos é assinatura de bot.
O sorteio acontece POR MENSAGEM no disparo; a IA (F4) só cria variações novas
na tela de templates, nunca no caminho do envio.
"""
import random
import re
from typing import Dict, List, Optional

from app.models.roteiro import TemplateVariacao

PLACEHOLDERS = ("produto", "preco_de", "preco_por", "desconto", "loja", "link", "cupom")
_RE_PLACEHOLDER = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")


def sortear_variacao(variacoes: List[TemplateVariacao],
                     rng: Optional[random.Random] = None) -> Optional[TemplateVariacao]:
    ativas = [v for v in variacoes if v.ativa and (v.corpo or "").strip()]
    if not ativas:
        return None
    pesos = [max(int(v.peso or 1), 1) for v in ativas]
    return (rng or random).choices(ativas, weights=pesos, k=1)[0]


def preencher(corpo: str, valores: Dict[str, str]) -> str:
    """Placeholder sem valor vira vazio — nunca vaza '{preco_de}' cru na mensagem."""
    def _sub(m: re.Match) -> str:
        return str(valores.get(m.group(1)) or "")
    return _RE_PLACEHOLDER.sub(_sub, corpo)


def montar_texto(corpo: str, valores: Dict[str, str],
                 prefixo: Optional[str], sufixo: Optional[str]) -> str:
    partes = [p for p in ((prefixo or "").strip(), preencher(corpo, valores).strip(),
                          (sufixo or "").strip()) if p]
    return "\n\n".join(partes) if len(partes) > 1 else (partes[0] if partes else "")


# --- CRUD (F4) ---------------------------------------------------------------

class TemplateInvalido(Exception):
    pass


class TemplateMensagemService:
    """Templates e suas variações. O sorteio (acima) é função pura; aqui mora
    a persistência e as regras de composição."""

    def __init__(self, db):
        self.db = db

    def _repo(self):
        from app.repositories.template_repository import TemplateRepository
        return TemplateRepository(self.db)

    def listar(self, user_id: int):
        repo = self._repo()
        templates = repo.por_usuario(user_id)
        return templates, repo.variacoes_por_template([t.id for t in templates])

    def obter(self, user_id: int, template_id: int):
        return self._repo().por_id(user_id, template_id)

    def criar(self, user_id: int, nome: str, tipo: str = "oferta"):
        from app.models.roteiro import TemplateMensagem

        nome = (nome or "").strip()[:120]
        if not nome:
            raise TemplateInvalido("Informe um nome para o template.")
        template = self._repo().adicionar(TemplateMensagem(
            user_id=user_id, nome=nome,
            tipo=tipo if tipo in ("oferta", "livre") else "oferta",
        ))
        self.db.commit()
        return template

    def atualizar(self, template, mudancas: dict):
        if mudancas.get("nome") is not None:
            nome = str(mudancas["nome"]).strip()[:120]
            if nome:
                template.nome = nome
        if mudancas.get("tipo") in ("oferta", "livre"):
            template.tipo = mudancas["tipo"]
        if isinstance(mudancas.get("ativo"), bool):
            template.ativo = mudancas["ativo"]
        self._repo().marcar_tocado(template)
        self.db.commit()
        return template

    def definir_variacoes(self, template, itens: list) -> None:
        """Substitui o conjunto de variações: [(corpo, peso, ativa)].

        Peso mínimo 1 — peso 0 tiraria a variação do sorteio sem ninguém ver
        por quê (para isso existe `ativa`).
        """
        from app.models.roteiro import TemplateVariacao

        limpos = []
        for corpo, peso, ativa in itens:
            corpo = (corpo or "").strip()
            if not corpo:
                continue
            limpos.append((corpo[:4000], max(int(peso or 1), 1), bool(ativa)))
        if not limpos:
            raise TemplateInvalido("O template precisa de ao menos uma variação.")

        repo = self._repo()
        repo.remover_variacoes(template.id)
        for corpo, peso, ativa in limpos:
            repo.adicionar(TemplateVariacao(
                template_id=template.id, corpo=corpo, peso=peso, ativa=ativa,
            ))
        repo.marcar_tocado(template)
        self.db.commit()

    def acrescentar_variacoes(self, template, corpos: list) -> int:
        """Usado pelo 'gerar com IA': soma ao que já existe, não substitui."""
        from app.models.roteiro import TemplateVariacao

        repo = self._repo()
        existentes = {v.corpo for v in repo.variacoes(template.id)}
        n = 0
        for corpo in corpos:
            corpo = (corpo or "").strip()[:4000]
            if corpo and corpo not in existentes:
                repo.adicionar(TemplateVariacao(template_id=template.id, corpo=corpo))
                existentes.add(corpo)
                n += 1
        if n:
            repo.marcar_tocado(template)
            self.db.commit()
        return n

    def remover(self, template) -> None:
        """Soft-delete: o template pode estar referenciado por passos de
        roteiro (FK SET NULL), e sumir a variação de um roteiro pronto seria
        mudança silenciosa de conteúdo."""
        template.ativo = False
        self._repo().marcar_tocado(template)
        self.db.commit()
