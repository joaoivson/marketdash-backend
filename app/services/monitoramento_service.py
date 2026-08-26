"""
Monitoramento de grupos — F8.

A afiliada acompanha um grupo (dela ou de terceiro, desde que o número dela
seja membro) e replica as ofertas que aparecem lá para os grupos dela, com o
link trocado pelo dela.

**O filtro roda ANTES de qualquer persistência.** Mensagem que não passa é
descartada na memória — não existe "grava e depois filtra". É o que faz a
promessa da política de privacidade ser verdadeira em vez de retórica: sem
monitoramento ativo o evento `message` sequer é assinado na sessão, e com ele
ativo só o que é oferta encosta no banco.

**Nada identifica quem escreveu.** Não guardamos JID, telefone nem hash de
autor — só o texto e o hash do próprio texto, para deduplicar repost.
"""
import hashlib
import logging
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.monitoramento import (
    CAPTURA_CAPTURADA, CAPTURA_ERRO, CAPTURA_REPLICADA, CAPTURA_REPLICANDO,
    Monitoramento, MonitoramentoCaptura,
)

logger = logging.getLogger(__name__)

# Reconhece URL com ou sem esquema — o dono do grupo cola dos dois jeitos.
#
# `(?:[a-z0-9-]+\.)+` sem a alternativa `www\.`: as duas casavam "www." e cada
# segmento ganhava dois caminhos, o que dava backtracking EXPONENCIAL quando o
# casamento falhava no fim ("www."×22 levava 1,1s; ×30, minutos). O texto vem de
# um grupo de TERCEIRO — qualquer membro poderia travar o webhook.
_URL = re.compile(r"(https?://\S+|(?<![\w.])(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*)", re.I)

# Teto de texto analisado. Mensagem de WhatsApp não passa disso, e um corpo
# gigante não pode custar CPU do webhook.
MAX_TEXTO = 8000


class MonitoramentoInvalido(ValueError):
    pass


class LimiteDeMonitoramentos(Exception):
    pass


def hash_da_mensagem(texto: str) -> str:
    """
    Hash do texto NORMALIZADO.

    O mesmo anúncio reposta com espaçamento ou emoji de contagem diferente é a
    mesma oferta — normalizar é o que faz a dedup valer na prática, e não só
    contra um `Ctrl+C/Ctrl+V` byte a byte.
    """
    limpo = re.sub(r"\s+", " ", (texto or "").strip().lower())
    return hashlib.sha256(limpo.encode("utf-8")).hexdigest()


def extrair_links(texto: str) -> List[str]:
    """
    Todos os links, **exatamente como aparecem no texto**.

    A forma crua é o que importa: é ela que precisa ser encontrada e trocada no
    texto original. Guardar a forma normalizada (`https://` na frente) fazia o
    `replace` não casar quando o dono do grupo colava sem esquema — e aí a
    mensagem saía para os grupos dela com o link do CONCORRENTE, marcada como
    "replicada". Normalizar é só para descobrir o marketplace.
    """
    vistos, links = set(), []
    for achado in _URL.finditer((texto or "")[:MAX_TEXTO]):
        bruto = achado.group(0).rstrip(").,;!?\"'")
        if bruto and bruto not in vistos:
            vistos.add(bruto)
            links.append(bruto)
    return links


def com_esquema(url: str) -> str:
    """Forma normalizada, usada só para resolver o marketplace."""
    return url if url.lower().startswith("http") else f"https://{url}"


def extrair_link(texto: str) -> Optional[str]:
    """Primeiro link (cru). Mantido para o filtro, que só quer saber se há um."""
    links = extrair_links(texto)
    return links[0] if links else None


class MonitoramentoService:
    def __init__(self, db: Session, plan_limit_monitoramentos: int = 0):
        self.db = db
        self.plan_limit = plan_limit_monitoramentos

    # --- CRUD ---------------------------------------------------------------

    def listar(self, user_id: int) -> List[Monitoramento]:
        return (
            self.db.query(Monitoramento)
            .filter(Monitoramento.user_id == user_id)
            .order_by(Monitoramento.criado_em.desc())
            .all()
        )

    def obter(self, user_id: int, monitoramento_id: int) -> Optional[Monitoramento]:
        return (
            self.db.query(Monitoramento)
            .filter(Monitoramento.id == monitoramento_id,
                    Monitoramento.user_id == user_id)
            .first()
        )

    def validar_destinos(self, user_id: int, campanha_id: Optional[int],
                         grupo_ids: Optional[List[int]]) -> None:
        """
        Origem E destino são ids vindos do cliente — os dois precisam de dono.

        Sem isto, um id de grupo de OUTRA usuária no destino faria a replicação
        apontar para dentro da conta dela. O motor não conseguiria enviar (não
        há instância desta usuária no grupo), mas o nome do grupo alheio
        apareceria na tela de progresso — vazamento com outra roupa.
        """
        from app.models.campanha_grupos import Campanha
        from app.models.whatsapp_grupos import WhatsappGrupo

        if campanha_id is not None:
            dona = (
                self.db.query(Campanha)
                .filter(Campanha.id == campanha_id, Campanha.user_id == user_id)
                .first()
            )
            if not dona:
                raise MonitoramentoInvalido("Campanha de destino não encontrada.")
        if grupo_ids:
            achados = (
                self.db.query(WhatsappGrupo.id)
                .filter(WhatsappGrupo.id.in_(list(grupo_ids)),
                        WhatsappGrupo.user_id == user_id)
                .all()
            )
            if len({g for (g,) in achados}) != len(set(grupo_ids)):
                raise MonitoramentoInvalido("Grupo de destino não encontrado.")

    def criar(self, user_id: int, nome: str, grupo_origem_id: int,
              **campos) -> Monitoramento:
        from app.core.plans import is_unlimited
        from app.models.whatsapp_grupos import WhatsappGrupo

        nome = (nome or "").strip()
        if not nome:
            raise MonitoramentoInvalido("Dê um nome ao monitoramento.")

        # Ownership do grupo de origem: id é sequencial e sem esta checagem
        # daria para monitorar grupo de outra usuária.
        grupo = (
            self.db.query(WhatsappGrupo)
            .filter(WhatsappGrupo.id == grupo_origem_id,
                    WhatsappGrupo.user_id == user_id)
            .first()
        )
        if not grupo:
            raise MonitoramentoInvalido("Grupo de origem não encontrado.")

        self.validar_destinos(user_id, campos.get("destino_campanha_id"),
                              campos.get("destino_grupo_ids"))

        if not is_unlimited(self.plan_limit):
            usados = (
                self.db.query(Monitoramento)
                .filter(Monitoramento.user_id == user_id).count()
            )
            if usados >= max(self.plan_limit, 0):
                raise LimiteDeMonitoramentos(
                    f"Seu plano permite {self.plan_limit} monitoramento(s)."
                )

        campos.setdefault("instancia_id",
                          self.escolher_instancia_ouvinte(user_id, grupo_origem_id))
        m = Monitoramento(user_id=user_id, nome=nome[:120],
                          grupo_origem_id=grupo_origem_id, **campos)
        self.db.add(m)
        self.db.flush()
        return m

    def escolher_instancia_ouvinte(self, user_id: int,
                                   grupo_id: int) -> Optional[int]:
        """
        UMA sessão escuta o grupo, não todas as que estão nele.

        Com dois números no mesmo grupo, deixar as duas assinando `message`
        dobraria o conteúdo que chega ao backend sem ganho nenhum — a dedup por
        hash já descartaria a segunda cópia. Menos sessão escutando é menos
        conteúdo de terceiro trafegando, que é o ponto do módulo.

        Prefere sessão conectada; se nenhuma estiver, fica com qualquer membro
        (o realinhamento diário conserta quando ela voltar).
        """
        from app.models.whatsapp_grupos import (
            INSTANCIA_CONECTADA, WhatsappGrupoInstancia, WhatsappInstancia,
        )

        candidatas = (
            self.db.query(WhatsappInstancia.id, WhatsappInstancia.status)
            .join(WhatsappGrupoInstancia,
                  WhatsappGrupoInstancia.instancia_id == WhatsappInstancia.id)
            .filter(WhatsappGrupoInstancia.grupo_id == grupo_id,
                    WhatsappInstancia.user_id == user_id)
            .order_by(WhatsappInstancia.id)
            .all()
        )
        if not candidatas:
            return None
        for iid, status in candidatas:
            if status == INSTANCIA_CONECTADA:
                return iid
        return candidatas[0][0]

    # --- captura ------------------------------------------------------------

    def ativos_do_grupo(self, grupo_id: int,
                        user_id: Optional[int] = None) -> List[Monitoramento]:
        """Hoje `whatsapp_grupos` tem UNIQUE(user_id, jid), então o grupo já
        identifica a dona. O filtro por `user_id` é a segunda linha: a regra 1
        do isolamento não abre exceção para "seguro por construção"."""
        q = (
            self.db.query(Monitoramento)
            .filter(Monitoramento.grupo_origem_id == grupo_id,
                    Monitoramento.ativo.is_(True))
        )
        if user_id is not None:
            q = q.filter(Monitoramento.user_id == user_id)
        return q.all()

    def interessa(self, m: Monitoramento, texto: str) -> Tuple[bool, Optional[str]]:
        """(passa no filtro?, link). Roda ANTES de gravar qualquer coisa."""
        link = extrair_link(texto)
        if m.somente_com_link and not link:
            return False, None
        palavras = [p.lower() for p in (m.palavras_chave or []) if p]
        if palavras and not any(p in (texto or "").lower() for p in palavras):
            return False, link
        return True, link

    def capturar(self, m: Monitoramento, texto: str,
                 link: Optional[str]) -> Optional[MonitoramentoCaptura]:
        """
        Grava a captura. Devolve None quando é repost já conhecido.

        A dedup é por constraint, não por SELECT-then-INSERT: duas mensagens
        iguais chegando ao mesmo tempo passariam as duas pela checagem prévia.
        """
        from sqlalchemy.exc import IntegrityError

        captura = MonitoramentoCaptura(
            monitoramento_id=m.id,
            mensagem_hash=hash_da_mensagem(texto),
            texto_original=texto,
            link_original=link,
            status=CAPTURA_CAPTURADA,
        )
        # SAVEPOINT, não rollback da transação inteira: o webhook chama isto em
        # laço sobre vários monitoramentos, e um repost no terceiro não pode
        # desfazer as capturas dos dois primeiros.
        try:
            with self.db.begin_nested():
                self.db.add(captura)
                self.db.flush()
        except IntegrityError:
            return None
        return captura

    # --- replicação ---------------------------------------------------------

    def grupos_de_destino(self, m: Monitoramento) -> List[int]:
        """Grupos para onde replicar, SEM o grupo de origem.

        Replicar de volta para a origem é como um monitoramento vira eco de si
        mesmo — e, se o grupo for de terceiro, como a afiliada anuncia para o
        concorrente dentro do grupo dele.
        """
        from app.models.campanha_grupos import CampanhaGrupo

        if m.destino_grupo_ids:
            ids = [int(g) for g in m.destino_grupo_ids]
        elif m.destino_campanha_id:
            ids = [
                v.grupo_id for v in
                self.db.query(CampanhaGrupo)
                .filter(CampanhaGrupo.campanha_id == m.destino_campanha_id)
                .order_by(CampanhaGrupo.posicao).all()
            ]
        else:
            return []
        return [g for g in ids if g != m.grupo_origem_id]

    def texto_para_envio(self, captura: MonitoramentoCaptura,
                         conversoes: Dict[str, str]) -> str:
        """
        Texto original com CADA link trocado pelo dela.

        Ordena do link mais longo para o mais curto: com `s.shopee.com.br/AbC`
        e `s.shopee.com.br/AbCdEf` no mesmo texto, trocar o curto primeiro
        corromperia o longo pela metade e enviaria uma URL quebrada.
        """
        texto = captura.texto_original or ""
        for bruto in sorted(conversoes, key=len, reverse=True):
            texto = texto.replace(bruto, conversoes[bruto])
        return texto

    def reivindicar(self, captura_id: int) -> bool:
        """
        `capturada` → `replicando` numa única instrução atômica.

        Só quem consegue a transição replica. É o mesmo claim do motor de
        envio: SELECT-depois-UPDATE deixaria dois workers passarem pela
        checagem e a mesma oferta sairia duas vezes.
        """
        from sqlalchemy import text as _sql

        linha = self.db.execute(
            _sql("""UPDATE monitoramento_capturas
                       SET status = :novo
                     WHERE id = :id AND status = :atual
                 RETURNING id"""),
            {"id": captura_id, "novo": CAPTURA_REPLICANDO, "atual": CAPTURA_CAPTURADA},
        ).fetchone()
        self.db.commit()
        return linha is not None

    def devolver_para_fila(self, captura: MonitoramentoCaptura) -> None:
        """Desfaz o claim quando a replicação não chegou a acontecer."""
        captura.status = CAPTURA_CAPTURADA
        self.db.add(captura)

    def destravar_replicando(self, minutos: int = 30) -> int:
        """
        Devolve à fila as capturas presas em `replicando`.

        `task_acks_late` reentrega a task se o worker morrer, mas o claim já foi
        feito e a reentrega não consegue reivindicar de novo: a captura fica
        presa para sempre, e a rota de replicar responde 409 eternamente. Um
        deploy no meio de um `generate_short_link` lento já produz isso.
        """
        from sqlalchemy import text as _sql

        linhas = self.db.execute(
            _sql("""UPDATE monitoramento_capturas
                       SET status = :fila
                     WHERE status = :preso
                       AND criado_em < NOW() - (:minutos * INTERVAL '1 minute')
                 RETURNING id"""),
            {"fila": CAPTURA_CAPTURADA, "preso": CAPTURA_REPLICANDO,
             "minutos": minutos},
        ).fetchall()
        self.db.commit()
        if linhas:
            logger.warning("%s captura(s) destravadas de `replicando`", len(linhas))
        return len(linhas)

    def reabrir(self, captura: MonitoramentoCaptura) -> None:
        """`erro` volta para `capturada` — a tentativa de novo é da afiliada.

        Sem isto, uma indisponibilidade passageira da Shopee matava a oferta em
        definitivo: o status trava em `erro` e o repost da mesma mensagem cai na
        dedup, então ela nunca mais seria capturada.
        """
        captura.status = CAPTURA_CAPTURADA
        captura.motivo = None
        self.db.add(captura)

    def marcar_replicada(self, captura: MonitoramentoCaptura, roteiro_id: int,
                         texto_final: str, link_convertido: Optional[str]) -> None:
        """`link_convertido` é o primeiro link convertido — só para a tela."""
        from datetime import datetime, timezone

        captura.status = CAPTURA_REPLICADA
        captura.roteiro_id = roteiro_id
        captura.texto_final = texto_final
        captura.link_convertido = link_convertido
        captura.replicado_em = datetime.now(timezone.utc)
        self.db.add(captura)

    def marcar_erro(self, captura: MonitoramentoCaptura, motivo: str) -> None:
        captura.status = CAPTURA_ERRO
        captura.motivo = (motivo or "")[:200]
        self.db.add(captura)

    def resumo(self, m: Monitoramento) -> Dict:
        """Dados de apresentação de um monitoramento (nome do grupo + total)."""
        from app.models.whatsapp_grupos import WhatsappGrupo

        grupo = (
            self.db.query(WhatsappGrupo)
            .filter(WhatsappGrupo.id == m.grupo_origem_id).first()
        )
        total = (
            self.db.query(MonitoramentoCaptura)
            .filter(MonitoramentoCaptura.monitoramento_id == m.id).count()
        )
        return {"grupo_origem": (grupo.nome if grupo else None),
                "total_capturas": total}

    def captura_de(self, monitoramento_id: int,
                   captura_id: int) -> Optional[MonitoramentoCaptura]:
        """Captura por id, presa ao monitoramento — id é sequencial."""
        return (
            self.db.query(MonitoramentoCaptura)
            .filter(MonitoramentoCaptura.id == captura_id,
                    MonitoramentoCaptura.monitoramento_id == monitoramento_id)
            .first()
        )

    def remover(self, m: Monitoramento) -> None:
        self.db.delete(m)

    def capturas(self, monitoramento_id: int, limite: int = 50
                 ) -> List[MonitoramentoCaptura]:
        return (
            self.db.query(MonitoramentoCaptura)
            .filter(MonitoramentoCaptura.monitoramento_id == monitoramento_id)
            .order_by(MonitoramentoCaptura.criado_em.desc())
            .limit(limite)
            .all()
        )

    # --- eventos da sessão --------------------------------------------------

    def sessoes_que_precisam_de_message(self, user_id: int) -> Dict[int, bool]:
        """instancia_id → precisa do evento `message`.

        Uma sessão precisa se tiver QUALQUER monitoramento ativo apontando para
        ela. Desligar o último monitoramento é o que devolve a sessão ao estado
        em que conteúdo de grupo não chega ao backend.
        """
        from app.models.whatsapp_grupos import WhatsappGrupo, WhatsappGrupoInstancia

        precisa: Dict[int, bool] = {}
        for m in (self.db.query(Monitoramento)
                  .filter(Monitoramento.user_id == user_id).all()):
            # `instancia_id` é definido na criação. O fallback cobre o
            # monitoramento criado antes disso e o caso de a sessão escolhida
            # ter sido removida (FK com SET NULL).
            alvos = [m.instancia_id] if m.instancia_id else [
                v.instancia_id for v in
                self.db.query(WhatsappGrupoInstancia)
                .join(WhatsappGrupo, WhatsappGrupo.id == WhatsappGrupoInstancia.grupo_id)
                .filter(WhatsappGrupoInstancia.grupo_id == m.grupo_origem_id).all()
            ]
            for iid in alvos:
                if iid:
                    precisa[iid] = precisa.get(iid, False) or bool(m.ativo)
        return precisa
