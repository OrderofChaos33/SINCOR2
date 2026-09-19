import http from "k6/http";
import { check } from "k6";
export const options = {
  vus: 500,
  duration: "30s",
  thresholds: { http_req_duration: ["p(95)<800"], http_req_failed: ["rate==0"] },
};
export default function () {
  const url = `${__ENV.STAGING_URL}/api/genesis/apply`;
  const email = `k6-${__VU}-${__ITER}@test.sincor`;
  const res = http.post(url, JSON.stringify({ email }), { headers: { "Content-Type": "application/json" } });
  check(res, { "status 200": (r) => r.status === 200 });
}
