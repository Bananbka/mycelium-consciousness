// Lab 5 load scenarios (k6). Selected with -e SCENARIO=A|B|C|PREP.
//   A  read-intensive   GET /memories/backups (cache layer + Postgres read)
//   B  write-intensive  POST /memories/write   (Redis XADD hot path)
//   C  complex workflow read profile -> 3 writes -> compute status -> read backups
// -e RAMP=1 replaces the constant level with one staged ramp-up run.
// Each run = warm-up phase (untimed) + constant-VU main phase (measured).
import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE_URL || 'http://nginx';
const SCENARIO = __ENV.SCENARIO || 'B';
const VUS = parseInt(__ENV.VUS || '10');
const DURATION = __ENV.DURATION || '30s';
const WARMUP = __ENV.WARMUP || '10s';
const NOCACHE = __ENV.NOCACHE === '1';
const RAMP = __ENV.RAMP === '1'; // one staged run: 10 -> 25 -> 50 -> 100 -> 200 VUs
const STAGE = __ENV.STAGE || '30s';
const USERS = 50;
const JSON_H = { 'Content-Type': 'application/json' };

http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }));

const measured = { 'http_req_duration{phase:main}': ['p(99)<600000'],
  'http_req_failed{phase:main}': ['rate<=1'], 'http_reqs{phase:main}': ['count>=0'] };

export const options = SCENARIO === 'PREP'
  ? { vus: 1, iterations: 1 }
  : {
      scenarios: {
        warmup: { executor: 'constant-vus', vus: Math.max(2, Math.ceil(VUS / 10)),
          duration: WARMUP, exec: 'run', tags: { phase: 'warmup' } },
        main: RAMP
          ? { executor: 'ramping-vus', startVUs: 0, exec: 'run', startTime: WARMUP,
              stages: [10, 25, 50, 100, 200].map((target) => ({ duration: STAGE, target })),
              tags: { phase: 'main' } }
          : { executor: 'constant-vus', vus: VUS, duration: DURATION,
              startTime: WARMUP, exec: 'run', tags: { phase: 'main' } },
      },
      thresholds: measured,
      summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
    };

// Deterministic accounts so runs are repeatable; register is idempotent (409 = exists).
export function setup() {
  const tokens = [];
  for (let i = 0; i < USERS; i++) {
    const creds = { email: `lt-user-${i}@example.com`, password: `lt-pass-${i}-Zx9` };
    http.post(`${BASE}/auth/register`, JSON.stringify({ ...creds, designation: `lt-clone-${i}` }),
      { headers: JSON_H, responseCallback: http.expectedStatuses(201, 409) });
    const r = http.post(`${BASE}/auth/login`, JSON.stringify(creds), { headers: JSON_H });
    tokens.push(r.json('access_token'));
  }
  return { tokens };
}

function headers(data) {
  const h = { Authorization: `Bearer ${data.tokens[__VU % USERS]}` };
  if (NOCACHE) h['Cache-Control'] = 'no-cache';
  return { headers: h };
}

function readBackups(data) {
  const r = http.get(`${BASE}/memories/backups?limit=50`, headers(data));
  check(r, { 'backups 200': (x) => x.status === 200 });
}
function writeMemory(data) {
  const r = http.post(`${BASE}/memories/write`,
    JSON.stringify({ content: 'x'.repeat(64 + (__ITER % 64)) }),
    { headers: { ...headers(data).headers, ...JSON_H } });
  check(r, { 'write 202': (x) => x.status === 202 });
}

export function run(data) {
  if (SCENARIO === 'A') {
    readBackups(data);
  } else if (SCENARIO === 'B') {
    writeMemory(data);
  } else {
    http.get(`${BASE}/profiles/me`, headers(data));   // read context
    for (let i = 0; i < 3; i++) writeMemory(data);    // write
    http.get(`${BASE}/memories/stream/status`, headers(data)); // computed state
    readBackups(data);                                // read result
  }
}

export default function () {}
