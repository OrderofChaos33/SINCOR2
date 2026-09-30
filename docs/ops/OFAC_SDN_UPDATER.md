# OFAC SDN Snapshot Updater — runbook

The P20 compliance oracle screens against a vendored lean snapshot of the
OFAC SDN list (`src/sincor2/defi/data/p20_ofac_sdn.json`). This snapshot
must be refreshed regularly: the oracle's freshness guards **alert at 24h**
and **fail closed beyond 48h** (`LIST_FRESHNESS_*_SECONDS` in
`compliance_automation.py`), so the updater runs **daily**.

## Schedule

```
# Daily OFAC SDN refresh — keeps the P20 oracle's freshness guards meaningful
0 6 * * *  cd /opt/sincor2 && ~/.venvs/sincor2/bin/python -m sincor2.defi.ofac_sdn_updater >> /var/log/sincor2/ofac-updater.log 2>&1
```

Adjust paths to the deployment. The cadence is the requirement (at least
daily); the exact hour is flexible.

## What the updater does

`src/sincor2/defi/ofac_sdn_updater.py`:

1. Downloads `https://www.treasury.gov/ofac/downloads/sdn.xml`.
2. Verifies: HTTP 200, body ≥ 1MB, parses as an SDN export, record count
   ≥ 10,000. **Any failure → the snapshot is untouched** (exit 1, JSON
   error on stdout).
3. Compares `publication_date` with the vendored snapshot. Unchanged →
   no-op (idempotent).
4. On a new publication: writes the new snapshot atomically
   (temp file + fsync + `os.replace`) — a crash can never leave a
   half-written list.

Output is a one-line JSON status: `{"status": "updated"|"unchanged"|"failed", ...}`.

## Monitoring

- Alert on `status: failed` or on no successful run in 24h.
- The oracle itself degrades loudly: `health()` freshness flags at 24h,
  `STALE` verdicts (fail-closed) beyond 48h. A missed updater run becomes
  a compliance block, not silent staleness.

## EU / UN lists (follow-up, not this run)

- **EU consolidated list**: published as XML/CSV by the EU Financial
  Sanctions Database (data.europa.eu / finance.ec.europa.eu). Same
  parse→lean-snapshot→daily-updater pattern applies; needs its own
  snapshot file and screening-set merge.
- **UN Consolidated List**: published by the UN Security Council
  (scsanctions.un.org / main.un.org). Same pattern; XML format differs,
  needs a dedicated parser.

Both are assessed as straightforward follow-ups reusing the updater's
verify→parse→atomic-replace skeleton. OFAC SDN ships first deliberately:
it is the list with digital-currency addresses, which is what makes
onchain wallet screening possible at all.
