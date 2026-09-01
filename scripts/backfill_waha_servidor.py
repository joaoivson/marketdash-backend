"""Backfill da migration 071 — cria a linha do servidor WAHA atual e aponta as
sessões existentes para ela.

Não dá para fazer em SQL puro: `api_key_cifrada` exige a chave Fernet da
aplicação (`SHOPEE_ENCRYPTION_KEY`), que não existe dentro do Postgres.

Sem este backfill nada quebra — o resolvedor cai em `settings.WAHA_URL` para
sessão sem servidor. Mas o cap global passa a contar errado (as sessões
existentes não aparecem na ocupação de nenhum servidor) e a alocação enxerga o
pool mais vazio do que ele está. Rodar no MESMO dia da migration.

    PYTHONPATH=$PWD python scripts/backfill_waha_servidor.py            # dry-run
    PYTHONPATH=$PWD python scripts/backfill_waha_servidor.py --apply    # grava

Idempotente: não duplica a linha (o `rotulo` é UNIQUE) nem repinta instância
que já tem `servidor_id`.
"""
import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill_waha")

ROTULO_PADRAO = "waha-01"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="grava (sem isso é dry-run)")
    parser.add_argument("--rotulo", default=ROTULO_PADRAO,
                        help=f"rótulo do servidor atual (default: {ROTULO_PADRAO})")
    parser.add_argument("--max-sessoes", type=int, default=None,
                        help="teto do servidor (default: WHATSAPP_MAX_INSTANCIAS_GLOBAL)")
    args = parser.parse_args()

    from app.core.config import settings
    from app.core.encryption import encrypt_value
    from app.db.session import SessionLocal
    from app.models.waha_servidores import WahaServidor
    from app.models.whatsapp_grupos import INSTANCIA_REMOVIDA, WhatsappInstancia

    if not (settings.WAHA_URL and settings.WAHA_API_KEY):
        logger.error("WAHA_URL/WAHA_API_KEY ausentes — nada a migrar. "
                     "Rode no ambiente que já fala com o WAHA.")
        return 1

    teto = args.max_sessoes or settings.WHATSAPP_MAX_INSTANCIAS_GLOBAL
    db = SessionLocal()
    try:
        servidor = db.query(WahaServidor).filter(WahaServidor.rotulo == args.rotulo).first()
        if servidor is None:
            logger.info("Criar servidor %s → %s (max_sessoes=%s)",
                        args.rotulo, settings.WAHA_URL, teto)
            servidor = WahaServidor(
                rotulo=args.rotulo,
                base_url=settings.WAHA_URL,
                api_key_cifrada=encrypt_value(settings.WAHA_API_KEY),
                max_sessoes=teto,
            )
            if args.apply:
                db.add(servidor)
                db.flush()
        else:
            logger.info("Servidor %s já existe (id=%s) — não recriado",
                        servidor.rotulo, servidor.id)

        # Só instância VIVA: apontar removida para o servidor a faria ocupar
        # vaga no pool (a contagem filtra por status, mas o dado ficaria sujo).
        orfas = (
            db.query(WhatsappInstancia)
            .filter(WhatsappInstancia.servidor_id.is_(None),
                    WhatsappInstancia.status != INSTANCIA_REMOVIDA)
            .all()
        )
        logger.info("Instâncias vivas sem servidor: %s", len(orfas))
        for i in orfas:
            logger.info("  %s (user %s, status %s)", i.nome_instancia, i.user_id, i.status)
            if args.apply:
                i.servidor_id = servidor.id

        if args.apply:
            db.commit()
            logger.info("OK — %s instâncias apontadas para %s", len(orfas), args.rotulo)
        else:
            db.rollback()
            logger.info("DRY-RUN — nada gravado. Repita com --apply.")
        return 0
    except Exception:
        db.rollback()
        logger.exception("Backfill falhou — nada foi gravado")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
