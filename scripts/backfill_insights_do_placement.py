"""Backfill de `campaign_daily_insights` a partir de `campaign_platform_daily_insights`.

Contexto: `GET /{campaign_id}/insights` parou de trazer dados para algumas contas
de anúncio, enquanto `GET /act_X/insights?breakdowns=publisher_platform` (uma
chamada por conta) segue funcionando. A soma dos placements de uma campanha num
dia É o insight agregado daquele dia — validado contra o Gerenciador da Meta
(conta da Alice, 27/08) e contra 8 dias de dados de todas as usuárias.

Só toca (campaign_id, date) que EXISTEM no placement e estão ausentes ou
divergentes no agregado. Não apaga nada. Idempotente.

    python backfill_insights.py              # dry-run (default)
    python backfill_insights.py --apply      # grava
"""
import argparse
import pathlib
from datetime import date

import psycopg2
import psycopg2.extras

DESDE = date(2026, 8, 26)
TOLERANCIA = 0.01  # centavo: abaixo disso o agregado já está correto


def conn_string() -> str:
    env = pathlib.Path(__file__).resolve().parent.parent / ".env"
    with open(env) as fh:
        for line in fh:
            if line.strip().startswith("CONNECTION_STRING_PROD="):
                return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("CONNECTION_STRING_PROD não encontrada")


# Placement somado por (campanha, dia) x o que está hoje no agregado.
#
# Só entram as contas REALMENTE quebradas: as que têm pelo menos um dia/campanha
# presente no placement e AUSENTE no agregado — assinatura de que a chamada por
# campanha não trouxe nada. Contas saudáveis divergem por centavos no dia em
# curso (as duas chamadas acontecem em instantes diferentes do mesmo dia, e o
# delta às vezes é negativo); mexer nelas trocaria dado bom por dado mais velho.
DIVERGENTES = """
    WITH placement AS (
        SELECT p.user_id, p.campaign_id, p.date,
               max(p.fb_campaign_id) AS fb_campaign_id,
               sum(p.spend) AS spend, sum(p.clicks) AS clicks, sum(p.impressions) AS impressions
        FROM campaign_platform_daily_insights p
        WHERE p.date >= %(desde)s
        GROUP BY p.user_id, p.campaign_id, p.date
    ),
    comparado AS (
        SELECT pl.*, i.spend AS spend_atual, i.clicks AS clicks_atual,
               (i.campaign_id IS NULL) AS linha_nova
        FROM placement pl
        LEFT JOIN campaign_daily_insights i
               ON i.campaign_id = pl.campaign_id AND i.date = pl.date
    ),
    quebradas AS (
        SELECT DISTINCT user_id FROM comparado WHERE linha_nova
    )
    SELECT c.user_id, c.campaign_id, c.date, c.fb_campaign_id,
           c.spend, c.clicks, c.impressions, c.spend_atual, c.clicks_atual, c.linha_nova
    FROM comparado c JOIN quebradas q ON q.user_id = c.user_id
    WHERE c.linha_nova
       OR abs(coalesce(c.spend_atual, 0) - c.spend) > %(tol)s
       OR coalesce(c.clicks_atual, 0) <> c.clicks
    ORDER BY c.user_id, c.date, c.campaign_id
"""

UPSERT = """
    INSERT INTO campaign_daily_insights
        (user_id, campaign_id, fb_campaign_id, date, spend, clicks, impressions, cpc, ctr, reach, leads,
         created_at, updated_at)
    VALUES (%(user_id)s, %(campaign_id)s, %(fb_campaign_id)s, %(date)s, %(spend)s, %(clicks)s,
            %(impressions)s, %(cpc)s, %(ctr)s, NULL, NULL, now(), now())
    ON CONFLICT ON CONSTRAINT uq_insight_campaign_date DO UPDATE SET
        spend = EXCLUDED.spend,
        clicks = EXCLUDED.clicks,
        impressions = EXCLUDED.impressions,
        cpc = EXCLUDED.cpc,
        ctr = EXCLUDED.ctr,
        -- reach não é somável entre placements e leads não vem nessa chamada:
        -- preserva o que já existe em vez de sobrescrever com valor pior.
        reach = campaign_daily_insights.reach,
        leads = campaign_daily_insights.leads,
        fb_campaign_id = COALESCE(EXCLUDED.fb_campaign_id, campaign_daily_insights.fb_campaign_id),
        updated_at = now()
"""

# Mesma lógica de CampaignRepository.rebuild_ad_spend_from_meta (app/repositories/campaign_repository.py):
# AdSpend source='meta' é projeção pura dos insights; o manual só sobrevive em dias
# que o Meta não cobre.
REBUILD_DELETE_META = "DELETE FROM ad_spends WHERE user_id = %(uid)s AND source = 'meta'"
REBUILD_DELETE_MANUAL_COBERTO = """
    DELETE FROM ad_spends
    WHERE user_id = %(uid)s AND source <> 'meta'
      AND date IN (
        SELECT i.date FROM campaign_daily_insights i
        WHERE i.user_id = %(uid)s
        GROUP BY i.date HAVING sum(i.spend) > 0 OR sum(i.clicks) > 0
      )
"""
REBUILD_INSERT = """
    INSERT INTO ad_spends (user_id, date, sub_id, amount, clicks, source)
    SELECT i.user_id, i.date, c.sub_id,
           sum(i.spend), sum(i.clicks), 'meta'
    FROM campaign_daily_insights i
    JOIN campaigns c ON c.id = i.campaign_id
    WHERE i.user_id = %(uid)s
    GROUP BY i.user_id, i.date, c.sub_id
    HAVING sum(i.spend) > 0 OR sum(i.clicks) > 0
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="grava (sem isso é dry-run)")
    args = ap.parse_args()

    cn = psycopg2.connect(conn_string())
    cn.autocommit = False
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(DIVERGENTES, {"desde": DESDE, "tol": TOLERANCIA})
    linhas = cur.fetchall()
    if not linhas:
        print("Nada divergente — o agregado já bate com o placement.")
        cn.close()
        return

    por_user: dict[int, dict] = {}
    for r in linhas:
        d = por_user.setdefault(r["user_id"], {"linhas": 0, "novas": 0, "delta": 0.0, "dias": set()})
        d["linhas"] += 1
        d["novas"] += 1 if r["linha_nova"] else 0
        d["delta"] += float(r["spend"]) - float(r["spend_atual"] or 0)
        d["dias"].add(r["date"])

    cur.execute("SELECT id, email FROM users WHERE id = ANY(%s)", (list(por_user),))
    emails = {u["id"]: u["email"] for u in cur.fetchall()}

    print(f"{'user':>5}  {'email':38} {'linhas':>7} {'novas':>6} {'gasto a somar':>14}  dias")
    for uid, d in sorted(por_user.items(), key=lambda kv: -kv[1]["delta"]):
        dias = ", ".join(x.strftime("%d/%m") for x in sorted(d["dias"]))
        print(f"{uid:>5}  {emails.get(uid, '?'):38} {d['linhas']:>7} {d['novas']:>6} "
              f"{d['delta']:>13.2f}  {dias}")
    print(f"\nTOTAL: {len(linhas)} linhas de insight, "
          f"R$ {sum(d['delta'] for d in por_user.values()):.2f} de gasto que hoje não aparece.")

    if not args.apply:
        print("\n[DRY-RUN] nada foi gravado. Rode com --apply para aplicar.")
        cn.close()
        return

    gravadas = 0
    for r in linhas:
        spend, clicks, impressions = float(r["spend"]), int(r["clicks"]), int(r["impressions"])
        cur.execute(UPSERT, {
            "user_id": r["user_id"], "campaign_id": r["campaign_id"],
            "fb_campaign_id": r["fb_campaign_id"], "date": r["date"],
            "spend": spend, "clicks": clicks, "impressions": impressions,
            "cpc": round(spend / clicks, 6) if clicks else None,
            "ctr": round(100.0 * clicks / impressions, 6) if impressions else None,
        })
        gravadas += 1

    for uid in por_user:
        cur.execute(REBUILD_DELETE_META, {"uid": uid})
        cur.execute(REBUILD_DELETE_MANUAL_COBERTO, {"uid": uid})
        cur.execute(REBUILD_INSERT, {"uid": uid})

    cn.commit()
    print(f"\nOK: {gravadas} insights gravados e ad_spends reconstruído para "
          f"{len(por_user)} usuária(s).")

    cur.execute("""
        SELECT a.user_id, a.date, round(sum(a.amount)::numeric, 2) AS gasto, sum(a.clicks) AS cliques
        FROM ad_spends a WHERE a.user_id = ANY(%s) AND a.date >= %s
        GROUP BY a.user_id, a.date ORDER BY a.user_id, a.date
    """, (list(por_user), DESDE))
    print("\nad_spends depois (o que a tela passa a mostrar):")
    for r in cur.fetchall():
        print(f"  user {r['user_id']}  {r['date'].strftime('%d/%m')}  R$ {r['gasto']}  {r['cliques']} cliques")
    cn.close()


if __name__ == "__main__":
    main()
