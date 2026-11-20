# OFAC SDN Snapshot Updater — runbook

The P20 compliance oracle screens against a vendored lean snapshot of the
OFAC SDN list, stored as UTF-8 text under `src/sincor2/defi/data/`: a JSON
manifest (`p20_ofac_sdn.manifest.json`) plus chunk files
(`p20_ofac_sdn.b64.<digest>.<nnn>.txt`), each holding a slice of the
base64-encoded gzipped snapshot JSON. The loader reassembles the chunks,
verifies the SHA-256 digests recorded in the manifest, gunzips, and parses.
Text form is deliberate: the GitHub connector's push route validates UTF-8
text and rejects binary content outright, so a `.gz` snapshot could never
be pushed through it — these chunk files can. This snapshot
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
   entries are kept as-is and only `fetched_at` is re-stamped to the
   successful verification time (status `unchanged`, `restamped: true`),
   so the 48h freshness guards track the last verification, not the
   last list change (W-35). A failed fetch or parse never re-stamps.
4. On a new publication: writes the new snapshot atomically
   (temp file + fsync + `os.replace`) — a crash can never leave a
   half-written list. In text-chunk form the chunk files land first
   under digest-derived names and the manifest is replaced last, so
   the manifest stays the single commit point.

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
