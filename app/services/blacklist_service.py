"""
Blacklist de números — item 17 da spec.

A tabela nasceu na migration 060 e ficou **inerte** até 26/08/2026: sem
repository, service, rota ou tela, e ninguém a lia no envio. Este módulo é o
que faltava para ela valer alguma coisa.

Onde a lista efetivamente age hoje:

  * **resumo diário (DM)** — é o único ponto do produto que manda mensagem para
    um número individual; número bloqueado não recebe, mesmo com opt-in ligado;
  * **entrada em grupo** — ao detectar a entrada de um número bloqueado, ele é
    removido do grupo, desde que a afiliada seja admin ali;
  * **menções ("marcar todos")** — o gancho existe (`bloqueados_entre`), mas as
    menções em si NÃO estão implementadas. Quando forem, a blacklist já vale
    sem ninguém precisar lembrar.

O número é guardado como `HMAC-SHA256(segredo, número)` — a mesma construção
irreversível dos eventos de grupo. A máscara (`+55 11 ****-4321`) existe porque
uma lista onde ela não reconhece ninguém é inútil, e o número inteiro
transformaria a tabela numa lista de telefones se o banco vazasse.
"""
import logging
import re
from typing import Iterable, List, Optional, Set

from sqlalchemy.orm import Session

from app.models.roteiro import BlacklistNumero
from app.services.grupo_evento_service import _segredo_do_hash
from app.services.waha_client import (
    normalizar_numero, numero_de_jid as _numero_de_jid_do_waha,
)

logger = logging.getLogger(__name__)


class NumeroInvalido(ValueError):
    pass


def hash_do_numero(numero_e164: str) -> str:
    """
    Mesmo segredo e mesma construção dos eventos de grupo.

    A entrada é o número em E.164 sem '+' (`5511999998888`), NÃO o JID: o
    webhook entrega `5511999998888@c.us` e quem chama normaliza antes. Hashear
    formas diferentes do mesmo número daria hashes diferentes e a lista nunca
    casaria.
    """
    import hashlib
    import hmac

    return hmac.new(_segredo_do_hash().encode(), (numero_e164 or "").encode(),
                    hashlib.sha256).hexdigest()


def mascarar(numero_e164: str) -> str:
    """`5511987654321` → `+55 11 ****-4321`."""
    d = re.sub(r"\D", "", numero_e164 or "")
    if len(d) < 6:
        return "número inválido"
    return f"+{d[:2]} {d[2:4]} ****-{d[-4:]}"


def numero_de_jid(jid: str) -> Optional[str]:
    """
    `5511999998888@c.us` → `5511999998888`. None quando não sobra dígito.

    O parse do JID vem do `waha_client`, que se declara fonte ÚNICA dele — se o
    formato mudar (sufixos `@lid` do GOWS, por exemplo), muda lá e este segue
    junto. Aqui só resta tirar o que não é dígito, para casar com a forma que
    `normalizar_numero` produz na hora de gravar.
    """
    digitos = re.sub(r"\D", "", _numero_de_jid_do_waha(jid or ""))
    return digitos or None


class BlacklistService:
    def __init__(self, db: Session):
        self.db = db

    # --- CRUD ---------------------------------------------------------------

    def listar(self, user_id: int) -> List[BlacklistNumero]:
        return (
            self.db.query(BlacklistNumero)
            .filter(BlacklistNumero.user_id == user_id)
            .order_by(BlacklistNumero.criado_em.desc())
            .all()
        )

    def adicionar(self, user_id: int, numero: str, motivo: Optional[str] = None,
                  remover_dos_grupos: bool = True) -> BlacklistNumero:
        """
        Adiciona (ou atualiza) um número na lista.

        Repetir o mesmo número não é erro: ela pode estar corrigindo o motivo ou
        ligando a remoção. Devolver 409 aqui faria a afiliada apagar e recriar
        para mudar uma palavra.
        """
        try:
            e164 = normalizar_numero(numero)
        except ValueError as e:
            raise NumeroInvalido(str(e))

        alvo = hash_do_numero(e164)
        existente = (
            self.db.query(BlacklistNumero)
            .filter(BlacklistNumero.user_id == user_id,
                    BlacklistNumero.numero_hash == alvo)
            .first()
        )
        if existente:
            existente.motivo = (motivo or "").strip()[:500] or None
            existente.remover_dos_grupos = remover_dos_grupos
            existente.numero_mascarado = mascarar(e164)
            self.db.add(existente)
            self.db.flush()
            return existente

        item = BlacklistNumero(
            user_id=user_id, numero_hash=alvo, numero_mascarado=mascarar(e164),
            motivo=(motivo or "").strip()[:500] or None,
            remover_dos_grupos=remover_dos_grupos,
        )
        self.db.add(item)
        self.db.flush()
        return item

    def remover(self, user_id: int, item_id: int) -> bool:
        item = (
            self.db.query(BlacklistNumero)
            .filter(BlacklistNumero.id == item_id,
                    BlacklistNumero.user_id == user_id)
            .first()
        )
        if not item:
            return False
        self.db.delete(item)
        return True

    # --- consulta (caminho quente) ------------------------------------------

    def bloqueado(self, user_id: int, numero: str) -> Optional[BlacklistNumero]:
        """`numero` em E.164 sem '+'. Devolve a entrada, para quem precisa saber
        se deve remover do grupo também."""
        if not numero:
            return None
        return (
            self.db.query(BlacklistNumero)
            .filter(BlacklistNumero.user_id == user_id,
                    BlacklistNumero.numero_hash == hash_do_numero(numero))
            .first()
        )

    def bloqueados_entre(self, user_id: int, numeros: Iterable[str]) -> Set[str]:
        """
        Quais destes números estão bloqueados. Uma query para o lote todo.

        É o gancho das menções ("marcar todos"), que ainda não existem: quando
        existirem, filtrar por aqui já basta.
        """
        por_hash = {hash_do_numero(n): n for n in numeros if n}
        if not por_hash:
            return set()
        achados = (
            self.db.query(BlacklistNumero.numero_hash)
            .filter(BlacklistNumero.user_id == user_id,
                    BlacklistNumero.numero_hash.in_(list(por_hash)))
            .all()
        )
        return {por_hash[h] for (h,) in achados if h in por_hash}
