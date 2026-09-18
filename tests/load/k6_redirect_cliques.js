// Carga no redirect de link — reproduz o incidente de 17–18/09/2026 (campanha
// com milhares de cliques no MESMO link) e prova que o redirect não escreve no
// Postgres por clique.
//
//   k6 run -e BASE=https://hml-api.marketdash.com.br -e SLUG=<slug-de-teste> tests/load/k6_redirect_cliques.js
//
// Critérios (ver docs/INCIDENTE-2026-09-18-LOGIN-SUPABASE.md): p95 < 300 ms,
// zero 5xx, e no Supabase de HML só 1 UPDATE em custom_links a cada ~15 s.
import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE || 'http://localhost:8081';
const SLUG = __ENV.SLUG || 'teste-carga';

export const options = {
  scenarios: {
    campanha: {
      executor: 'constant-arrival-rate',
      rate: 500, timeUnit: '1m', duration: '5m',
      preAllocatedVUs: 20, maxVUs: 100,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<300'],
    http_req_failed: ['rate<0.001'],
  },
};

export default function () {
  // IP/UA únicos por requisição para passar pelo dedup de 60 s.
  const i = __ITER + __VU * 100000;
  const res = http.get(`${BASE}/api/v1/links/r/${SLUG}`, {
    redirects: 0,
    headers: {
      'x-forwarded-for': `10.${(i >> 16) & 255}.${(i >> 8) & 255}.${i & 255}`,
      'user-agent': `Mozilla/5.0 (k6 ${i})`,
    },
  });
  check(res, {
    'redireciona (30x)': (r) => r.status >= 300 && r.status < 400,
    'sem 5xx': (r) => r.status < 500,
  });
}
