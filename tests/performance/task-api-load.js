// Non-destructive load test for the Task Management API.
//
// Runs against a freshly started, ephemeral LOCAL instance of the API
// (see .github/workflows/performance.yml) — never against a deployed
// staging/production environment. Two bounded ("iterations", not
// open-ended-duration) scenarios:
//
//   - reads:     pure GET traffic against /health, /api/tasks, and
//                /api/tasks/overdue. Safe by construction.
//   - lifecycle: each iteration creates one task it owns, reads it back,
//                transitions its status, then deletes it. Every mutation
//                is scoped to data the iteration itself created, so the
//                run is idempotent and leaves no residue. Set
//                PERF_TEST_READS_ONLY=true to drop this scenario entirely
//                (used by the manual, opt-in staging smoke job, which must
//                never send a mutating request to a shared environment).
//
// This script intentionally has NO pass/fail thresholds. It only measures
// and exports a JSON summary; scripts/perf_gate.py is the single source of
// truth for comparing that summary against an explicit, human-reviewed
// baseline (tests/performance/baseline.json).
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';
import { uuidv4 } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.1.0/index.js';

const BASE_URL = __ENV.PERF_TEST_BASE_URL || 'http://localhost:5087';
const VUS = Number(__ENV.PERF_TEST_VUS || 5);
const ITERATIONS_PER_VU = Number(__ENV.PERF_TEST_ITERATIONS_PER_VU || 10);
// Read-only mode drops the `lifecycle` scenario entirely (no POST/PATCH/DELETE),
// for the manual, opt-in staging smoke job, which must never mutate a shared
// environment. Local/CI runs never set this and always exercise both scenarios.
const READS_ONLY = (__ENV.PERF_TEST_READS_ONLY || '').toLowerCase() === 'true';

// Custom per-scenario metrics so scripts/perf_gate.py can read a stable,
// explicit set of names out of the JSON summary instead of depending on
// k6's tag-based sub-metrics (which only materialize when referenced by a
// threshold — and this script deliberately has no thresholds).
const readsDuration = new Trend('reads_duration', true);
const readsErrors = new Rate('reads_errors');
const lifecycleDuration = new Trend('lifecycle_duration', true);
const lifecycleErrors = new Rate('lifecycle_errors');

const scenarios = {
  reads: {
    executor: 'per-vu-iterations',
    vus: VUS,
    iterations: ITERATIONS_PER_VU,
    maxDuration: '2m',
    exec: 'reads',
  },
};
if (!READS_ONLY) {
  scenarios.lifecycle = {
    executor: 'per-vu-iterations',
    vus: VUS,
    iterations: ITERATIONS_PER_VU,
    maxDuration: '2m',
    exec: 'lifecycle',
    startTime: '2s',
  };
}

export const options = {
  scenarios,
  // No `thresholds` block by design: perf_gate.py owns pass/fail decisions
  // against an explicit, reviewed baseline instead of numbers hardcoded here.
  // k6's default summaryTrendStats omits p(99) and count, but perf_gate.py
  // reads both from every *_duration trend, so they must be requested here.
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)', 'count'],
};

function recordRead(response, checkName, expectedStatus) {
  readsDuration.add(response.timings.duration);
  const ok = check(response, { [checkName]: (candidate) => candidate.status === expectedStatus });
  readsErrors.add(!ok);
}

export function reads() {
  recordRead(http.get(`${BASE_URL}/health`, { tags: { scenario: 'reads', endpoint: 'health' } }), 'health status is 200', 200);
  recordRead(http.get(`${BASE_URL}/api/tasks`, { tags: { scenario: 'reads', endpoint: 'list' } }), 'list status is 200', 200);
  recordRead(
    http.get(`${BASE_URL}/api/tasks/overdue`, { tags: { scenario: 'reads', endpoint: 'overdue' } }),
    'overdue status is 200',
    200,
  );

  sleep(0.1);
}

export function lifecycle() {
  const headers = { 'Content-Type': 'application/json' };
  const title = `perf-test-${uuidv4()}`;
  const dueDate = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();

  const createResponse = http.post(
    `${BASE_URL}/api/tasks`,
    JSON.stringify({ title, description: 'k6 load-test task, self-cleaning', dueDate }),
    { headers, tags: { scenario: 'lifecycle', endpoint: 'create' } },
  );
  lifecycleDuration.add(createResponse.timings.duration);
  const created = check(createResponse, { 'create status is 201': (response) => response.status === 201 });
  lifecycleErrors.add(!created);
  if (!created) {
    // Nothing was persisted (or we can't be sure); skip the rest of this
    // iteration rather than risk operating on an unknown task id.
    sleep(0.1);
    return;
  }

  const task = createResponse.json();

  const getResponse = http.get(`${BASE_URL}/api/tasks/${task.id}`, {
    tags: { scenario: 'lifecycle', endpoint: 'get-by-id' },
  });
  lifecycleDuration.add(getResponse.timings.duration);
  lifecycleErrors.add(!check(getResponse, { 'get-by-id status is 200': (response) => response.status === 200 }));

  const statusResponse = http.patch(
    `${BASE_URL}/api/tasks/${task.id}/status`,
    // TaskItemStatus has no JsonStringEnumConverter registered, so the API
    // binds/serializes it as its underlying int (Todo=0, InProgress=1,
    // Done=2), not by enum name.
    JSON.stringify({ status: 1 }),
    { headers, tags: { scenario: 'lifecycle', endpoint: 'update-status' } },
  );
  lifecycleDuration.add(statusResponse.timings.duration);
  lifecycleErrors.add(
    !check(statusResponse, { 'update-status status is 200': (response) => response.status === 200 }),
  );

  const deleteResponse = http.del(`${BASE_URL}/api/tasks/${task.id}`, null, {
    tags: { scenario: 'lifecycle', endpoint: 'delete' },
  });
  lifecycleDuration.add(deleteResponse.timings.duration);
  lifecycleErrors.add(!check(deleteResponse, { 'delete status is 204': (response) => response.status === 204 }));

  sleep(0.1);
}

export function handleSummary(data) {
  return {
    'perf-results/summary.json': JSON.stringify(data, null, 2),
    stdout: textSummary(data, { indent: ' ', enableColors: false }),
  };
}
