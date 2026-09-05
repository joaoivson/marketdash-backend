"""Acesso aos grupos sincronizados e ao vínculo N:N grupo↔instância."""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from sqlalchemy import exists

from app.models.whatsapp_grupos import (
    GrupoParticipante, WhatsappGrupo, WhatsappGrupoInstancia,
)


class WhatsappGrupoRepository:
    def __init__(self, db: Session):
        self.db = db

    def por_usuario(self, user_id: int, apenas_ativos: bool = True,
                    instancia_id: Optional[int] = None,
                    busca: Optional[str] = None,
                    apenas_ativados: bool = False) -> List[WhatsappGrupo]:
        q = self.db.query(WhatsappGrupo).filter(WhatsappGrupo.user_id == user_id)
        if apenas_ativos:
            q = q.filter(WhatsappGrupo.ativo.is_(True))
        if apenas_ativados:
            # Toggle da usuária (074) — eixo separado do `ativo` do sync.
            q = q.filter(WhatsappGrupo.ativado.is_(True))
        if instancia_id is not None:
            q = q.join(
                WhatsappGrupoInstancia,
                WhatsappGrupoInstancia.grupo_id == WhatsappGrupo.id,
            ).filter(WhatsappGrupoInstancia.instancia_id == instancia_id)
        if busca:
            q = q.filter(WhatsappGrupo.nome.ilike(f"%{busca}%"))
        return q.order_by(WhatsappGrupo.nome).all()

    def por_id(self, user_id: int, grupo_id: int) -> Optional[WhatsappGrupo]:
        """Ownership embutida: id de outra dona devolve None (vira 404)."""
        return (
            self.db.query(WhatsappGrupo)
            .filter(WhatsappGrupo.user_id == user_id, WhatsappGrupo.id == grupo_id)
            .first()
        )

    def total_ativados(self, user_id: int) -> int:
        """Denominador do limite `whatsapp_grupos` do plano."""
        return (
            self.db.query(WhatsappGrupo)
            .filter(WhatsappGrupo.user_id == user_id,
                    WhatsappGrupo.ativado.is_(True))
            .count()
        )

    def por_jid(self, user_id: int, jid: str) -> Optional[WhatsappGrupo]:
        return (
            self.db.query(WhatsappGrupo)
            .filter(WhatsappGrupo.user_id == user_id, WhatsappGrupo.jid == jid)
            .first()
        )

    def por_jids(self, user_id: int) -> Dict[str, WhatsappGrupo]:
        """Mapa jid→grupo do usuário — preload que mata o SELECT-por-grupo do sync."""
        return {
            g.jid: g
            for g in self.db.query(WhatsappGrupo).filter(WhatsappGrupo.user_id == user_id)
        }

    def vinculos_da_instancia(self, instancia_id: int) -> Dict[int, WhatsappGrupoInstancia]:
        """grupo_id→vínculo da sessão — preload do upsert N:N."""
        return {
            v.grupo_id: v
            for v in self.db.query(WhatsappGrupoInstancia).filter(
                WhatsappGrupoInstancia.instancia_id == instancia_id
            )
        }

    def adicionar(self, grupo: WhatsappGrupo) -> WhatsappGrupo:
        """add + flush (garante o id) SEM commit — o sync é uma transação só."""
        self.db.add(grupo)
        self.db.flush()
        return grupo

    def vincular_instancia(self, grupo_id: int, instancia_id: int, sou_admin: bool,
                           vinculo: Optional[WhatsappGrupoInstancia] = None) -> None:
        """Upsert do vínculo N:N — o mesmo grupo pode ter 2 números da afiliada."""
        if vinculo is not None:
            vinculo.sou_admin = sou_admin
        else:
            self.db.add(WhatsappGrupoInstancia(
                grupo_id=grupo_id, instancia_id=instancia_id, sou_admin=sou_admin,
            ))

    def desvincular_instancia(self, instancia_id: int, exceto_grupo_ids: List[int]) -> int:
        """Remove vínculos de grupos que a sessão não vê mais neste sync."""
        q = self.db.query(WhatsappGrupoInstancia).filter(
            WhatsappGrupoInstancia.instancia_id == instancia_id
        )
        if exceto_grupo_ids:
            q = q.filter(~WhatsappGrupoInstancia.grupo_id.in_(exceto_grupo_ids))
        n = q.delete(synchronize_session=False)
        return n

    def instancias_por_grupo(self, user_id: int) -> Dict[int, List[int]]:
        """grupo_id → [instancia_id] para a tela mostrar por qual número enviar."""
        linhas = (
            self.db.query(WhatsappGrupoInstancia.grupo_id, WhatsappGrupoInstancia.instancia_id)
            .join(WhatsappGrupo, WhatsappGrupo.id == WhatsappGrupoInstancia.grupo_id)
            .filter(WhatsappGrupo.user_id == user_id)
            .all()
        )
        resultado: Dict[int, List[int]] = {}
        for grupo_id, instancia_id in linhas:
            resultado.setdefault(grupo_id, []).append(instancia_id)
        return resultado

    def desativar_sem_vinculo(self, user_id: int) -> int:
        """UPDATE único: grupos do usuário sem NENHUMA sessão viram inativos."""
        n = (
            self.db.query(WhatsappGrupo)
            .filter(
                WhatsappGrupo.user_id == user_id,
                WhatsappGrupo.ativo.is_(True),
                ~exists().where(WhatsappGrupoInstancia.grupo_id == WhatsappGrupo.id),
            )
            .update(
                {"ativo": False, "atualizado_em": datetime.now(timezone.utc)},
                synchronize_session=False,
            )
        )
        return int(n or 0)

    def marcar_tocado(self, grupo: WhatsappGrupo) -> None:
        grupo.atualizado_em = datetime.now(timezone.utc)
        self.db.add(grupo)

    # --- participantes (080) -------------------------------------------------

    def substituir_participantes(self, grupo_id: int, itens: List[dict]) -> int:
        """
        Substitui a lista de membros do grupo pela que o sync acabou de ver.

        SUBSTITUI, não acumula: a tabela responde "quem está no grupo AGORA".
        Quem saiu já está em `grupo_eventos` — deixar o histórico aqui faria a
        exportação devolver gente que não está mais no grupo, que é exatamente
        o defeito do modelo antigo (exportar eventos de entrada).

        `visto_em` é preservado no upsert: ele é a primeira vez que vimos a
        pessoa, e reescrevê-lo a cada sync transformaria "entrou em 12/08" em
        "entrou hoje" para o grupo inteiro, todo dia.
        """
        agora = datetime.now(timezone.utc)
        atuais = {
            p.identificador: p
            for p in self.db.query(GrupoParticipante)
            .filter(GrupoParticipante.grupo_id == grupo_id)
            .all()
        }
        desejados = set()
        for item in itens:
            ident = (item.get("identificador") or "")[:64]
            if not ident:
                continue
            desejados.add(ident)
            atual = atuais.get(ident)
            if atual is None:
                self.db.add(GrupoParticipante(
                    grupo_id=grupo_id,
                    identificador=ident,
                    telefone=item.get("telefone"),
                    identificador_hash=item.get("identificador_hash"),
                    admin=bool(item.get("admin")),
                    visto_em=agora,
                    confirmado_em=agora,
                ))
                continue
            # O telefone pode chegar num sync e faltar no seguinte (o engine
            # varia). Não apagar o que já se sabe: `or atual.telefone`.
            atual.telefone = item.get("telefone") or atual.telefone
            atual.identificador_hash = (
                item.get("identificador_hash") or atual.identificador_hash
            )
            atual.admin = bool(item.get("admin"))
            atual.confirmado_em = agora
            self.db.add(atual)

        sumiram = [i for i in atuais if i not in desejados]
        if sumiram:
            (
                self.db.query(GrupoParticipante)
                .filter(GrupoParticipante.grupo_id == grupo_id,
                        GrupoParticipante.identificador.in_(sumiram))
                .delete(synchronize_session=False)
            )
        return len(desejados)

    def participantes_de(self, grupo_ids: List[int]) -> List[GrupoParticipante]:
        if not grupo_ids:
            return []
        return (
            self.db.query(GrupoParticipante)
            .filter(GrupoParticipante.grupo_id.in_(grupo_ids))
            .order_by(GrupoParticipante.grupo_id, GrupoParticipante.visto_em)
            .all()
        )

    def preencher_telefone_dos_eventos(self, grupo_id: int) -> int:
        """
        Dá telefone aos eventos do grupo, casando pelo HMAC dos participantes.

        **Por que precisa existir.** O webhook `group.v2.participants` NÃO manda
        o telefone — medido em homologação: 191 eventos gravados depois da
        correção que passou a ler `PhoneNumber` separado do `JID` continuaram
        todos `identificador_tipo='lid'`. O campo simplesmente não vem nesse
        evento. Quem tem o número é o payload REST de `/groups`, que o sync já
        consome — e é de lá que `grupo_participantes` vem preenchido.

        Então o caminho é este: o sync grava os participantes com telefone e,
        no mesmo passo, preenche os eventos que só tinham o LID. O
        `identificador_hash` é o que casa os dois, e ele é estável por
        construção — é exatamente para isso que ele continuou existindo quando
        a 079 passou a guardar o número.

        Só toca evento que ainda NÃO tem telefone: rodar de novo é inofensivo.
        """
        from sqlalchemy import text

        resultado = self.db.execute(text("""
            UPDATE grupo_eventos e
               SET identificador = p.telefone,
                   identificador_tipo = 'telefone'
              FROM grupo_participantes p
             WHERE p.grupo_id = :grupo_id
               AND e.grupo_id = :grupo_id
               AND e.identificador_hash = p.identificador_hash
               AND p.telefone IS NOT NULL
               AND (e.identificador_tipo IS DISTINCT FROM 'telefone')
        """), {"grupo_id": grupo_id})
        return int(resultado.rowcount or 0)
