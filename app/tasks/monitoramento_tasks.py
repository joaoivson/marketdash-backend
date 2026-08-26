"""
Task de replicação do monitoramento (F8).

priority=0 (interativo): a oferta capturada tem validade curta e a afiliada
está esperando ver o envio sair. NUNCA 1..8 — só as pontas das filas são
consumidas e uma prioridade do meio é aceita e nunca executa, em silêncio.

A task é idempotente pelo status da captura: replicar duas vezes a mesma
captura mandaria a mesma oferta duas vezes para os grupos dela.
"""
import asyncio
import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="monitoramento.replicar_captura", bind=True)
def replicar_captura(self, captura_id: int) -> dict:
    from app.db.session import SessionLocal
    from app.models.monitoramento import Monitoramento, MonitoramentoCaptura
    from app.services.monitoramento_service import MonitoramentoService
    from app.services.roteiro_service import RoteiroService

    db = SessionLocal()
    try:
        servico = MonitoramentoService(db)
        # Claim atômico ANTES de qualquer trabalho: SELECT-depois-UPDATE deixaria
        # dois workers passarem pela checagem e a mesma oferta sairia duas vezes
        # para os grupos dela.
        if not servico.reivindicar(captura_id):
            return {"ignorada": "já reivindicada"}

        captura = db.query(MonitoramentoCaptura).get(captura_id)
        m = db.query(Monitoramento).get(captura.monitoramento_id)
        if not m or not m.ativo:
            servico.devolver_para_fila(captura)
            db.commit()
            return {"ignorada": "monitoramento inativo"}
        destinos = servico.grupos_de_destino(m)
        if not destinos:
            servico.marcar_erro(captura, "Sem grupos de destino configurados.")
            db.commit()
            return {"erro": "sem destino"}

        from app.services.monitoramento_service import extrair_links

        conversoes = {}
        link_convertido = None
        if m.converter_links:
            brutos = extrair_links(captura.texto_original or "")
            for bruto in brutos:
                convertido = _converter(db, m.user_id, bruto)
                if convertido:
                    conversoes[bruto] = convertido
            faltaram = [b for b in brutos if b not in conversoes]
            if faltaram:
                # Sobrou link que não é dela. Enviar assim faria a afiliada
                # divulgar o concorrente nos grupos DELA — e antes isto passava
                # em silêncio, marcado como "replicada". Falhar com o motivo na
                # tela é a única saída honesta; quem quiser replicar cru
                # desliga `converter_links` de propósito.
                servico.marcar_erro(
                    captura,
                    f"Não conseguimos gerar seu link para: {faltaram[0][:80]}",
                )
                db.commit()
                return {"erro": "conversao", "nao_convertidos": len(faltaram)}
            link_convertido = next(iter(conversoes.values()), None)

        texto = servico.texto_para_envio(captura, conversoes)
        roteiro = RoteiroService(db).criar_envio_rapido(
            user_id=m.user_id, texto=texto, midia_url=None, oferta_url=None,
            grupo_ids=destinos, campanha_id=m.destino_campanha_id,
        )
        servico.marcar_replicada(captura, roteiro.id, texto, link_convertido)
        db.commit()

        from datetime import datetime, timezone

        execucao, _avisos = RoteiroService(db).agendar(
            roteiro, datetime.now(timezone.utc).date(), ignorar_avisos=True
        )
        # A execução fica `agendada` com hora de agora: se o enfileiramento
        # falhar (Redis fora do ar), o tick de 5 min pega assim mesmo. Marcar
        # `enviando` antes de enfileirar deixaria o envio preso nesse estado.
        execucao.proxima_execucao_em = datetime.now(timezone.utc)
        db.commit()

        # A partir daqui o trabalho está feito e commitado. Falha de
        # enfileiramento é atraso, não erro: marcar a captura como `erro`
        # depois de já ter criado o envio faria a afiliada clicar em "replicar"
        # de novo e a MESMA oferta sair duas vezes.
        try:
            from app.tasks.roteiro_tasks import processar_execucao

            processar_execucao.apply_async(args=[execucao.id], priority=0)
        except Exception:
            logger.warning("Captura %s replicada, mas o enfileiramento falhou; "
                           "o tick de 5 min assume", captura_id)
        logger.info("Captura %s replicada para %s grupo(s)", captura_id, len(destinos))
        return {"replicada": True, "grupos": len(destinos), "execucao": execucao.id}
    except Exception:
        db.rollback()
        # Devolve o claim: preso em `replicando` a captura ficaria invisível
        # para sempre, sem nem aparecer como erro na tela.
        try:
            from app.models.monitoramento import CAPTURA_REPLICANDO

            presa = db.query(MonitoramentoCaptura).get(captura_id)
            # Só marca erro se ainda estiver com o claim: se já virou
            # `replicada`, o envio existe e chamar de erro convidaria a um
            # segundo clique — e a MESMA oferta sairia duas vezes.
            if presa is not None and presa.status == CAPTURA_REPLICANDO:
                MonitoramentoService(db).marcar_erro(
                    presa, "Falha inesperada ao replicar. Tente de novo."
                )
                db.commit()
        except Exception:
            db.rollback()
        logger.exception("Falha ao replicar captura %s", captura_id)
        return {"erro": "falha inesperada"}
    finally:
        db.close()


def _converter(db, user_id: int, url: str):
    """URL do concorrente → link de afiliada DELA. None quando não dá.

    Recebe a URL **crua** (como apareceu no texto) e normaliza só para resolver
    o marketplace — a forma crua é a que o texto final precisa trocar.

    Sem sub_id de grupo: a replicação vai para vários grupos de uma vez e o
    link é o mesmo para todos. A atribuição por grupo continua sendo do envio
    por oferta, que gera um short link por grupo.
    """
    from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository
    from app.services.integracao_service import provedor_da_url
    from app.services.monitoramento_service import com_esquema
    from app.services.shopee_integration_service import ShopeeIntegrationService

    url = com_esquema(url)
    provedor = provedor_da_url(url)
    if provedor != "shopee":
        # Só Shopee tem geração de link hoje. Outro marketplace: não inventamos
        # um link, e a captura fica com o motivo explícito na tela.
        return None
    try:
        return asyncio.run(
            ShopeeIntegrationService(
                ShopeeIntegrationRepository(db)
            ).generate_short_link(user_id, url, None)
        )
    except Exception as e:
        logger.warning("Conversão de link falhou: %s", str(e)[:150])
        return None
