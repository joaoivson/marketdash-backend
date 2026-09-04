"""
Aplica a migration 078 (função `trigger_shopee_sync_user`) e agenda os dois
jobs da conta do Luiz Fernando (user_id 9) em HOMOLOGAÇÃO.

Os 24 jobs `shopee-sync-*h-brt` de todo mundo continuam DESLIGADOS aqui de
propósito — em homologação eles só sincronizariam dado de teste parado, e foi
essa cadência horária que derrubou o banco compartilhado em 20/07/2026.

Rodar de dentro do container (o repo está montado em /app):
    docker exec marketdash_app python scripts/aplicar_078_hml.py
"""
from sqlalchemy import text

from app.db.session import SessionLocal

USER_ID_LUIZ = 9
MIGRATION = "/app/migrations/078_shopee_sync_por_usuario.sql"

# 04:00 UTC fica fora do incremental porque é o slot do full: os dois na mesma
# hora disputam o mesmo lock e um vira `skipped_lock`.
JOBS = [
    ("shopee-sync-luiz-incremental", "0 0-3,5-23 * * *", "incremental"),
    ("shopee-sync-luiz-full", "0 4 * * *", "full"),
]


def main() -> None:
    db = SessionLocal()
    try:
        ref = db.execute(text("SELECT current_database()")).scalar()
        print(f"banco: {ref}")

        db.execute(text(open(MIGRATION, encoding="utf-8").read()))
        db.commit()
        print("função trigger_shopee_sync_user criada")

        for nome, agenda, tipo in JOBS:
            # cron.schedule com nome existente REAGENDA (não duplica).
            db.execute(
                text("SELECT cron.schedule(:nome, :agenda, :cmd)"),
                {
                    "nome": nome,
                    "agenda": agenda,
                    "cmd": f"SELECT public.trigger_shopee_sync_user({USER_ID_LUIZ}, '{tipo}');",
                },
            )
            print(f"agendado: {nome} [{agenda}] → user {USER_ID_LUIZ} / {tipo}")
        db.commit()

        print("\nestado final dos jobs de shopee:")
        for r in db.execute(
            text(
                "SELECT jobname, schedule, active FROM cron.job "
                "WHERE jobname LIKE '%shopee%' ORDER BY active DESC, jobname"
            )
        ):
            print(" ", tuple(r))
    finally:
        db.close()


if __name__ == "__main__":
    main()
