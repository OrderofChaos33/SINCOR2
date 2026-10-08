# DeFi Proof-Ledger SKU Canon

One product = one canonical SKU. This is the rule that keeps gate evaluation honest.

## The canonical SKU

Canonical SKUs are minted by `products.mint_sku(protocol_id)`:

```
SINCOR-DEFI-P{swarm_id:02d}-{SLUG}
```

where `SLUG` comes from `SLUGS` in `src/sincor2/defi/products.py`. Examples:

| Product | protocol_id | Canonical SKU |
|---|---|---|
| P01 | `P01_YIELD_AGG` | `SINCOR-DEFI-P01-VAULT` |
| P05 | `P05_INSURANCE` | `SINCOR-DEFI-P05-MUTUAL` |
| P24 | `P24_SOCIALFI` | `SINCOR-DEFI-P24-SOCIALFI` |

(26 products total; see `SLUGS` for the full table.)

## Gate-matching rule

Every gate in `src/sincor2/defi/gates.py` queries the ledger by **canonical SKU only**
(`product["sku"]` comes from `mint_sku`). An entry recorded under any other SKU is
invisible to gate evaluation by construction.

## Supersede rule

When evidence lands under a stale (pre-canonical or mistyped) SKU:

1. **Never delete it.** The proof ledger is append-only (`ProofLedger.append` has no
   update/delete). The underlying test runs may be genuine; only the SKU key was wrong.
2. **Mark it.** Add to the entry's `details`:
   - `"status": "superseded"`
   - `"superseded_by": "<canonical SKU>"`
   - `"supersede_reason": "<why>"`
   - `"superseded_by_wave": "<branch/wave that marked it>"`
3. **Annotate.** Append one `note`-kind entry under the stale SKU documenting the
   supersede, `recorded_by` the hygiene wave. This keeps the audit trail in the ledger.
4. **Exclude explicitly.** `gates._is_superseded()` treats `details.status == "superseded"`
   as dead evidence: `_passing_entries()` and `_check_audit_report()` skip such entries
   even if queried directly. Belt and suspenders — today gates only query canonical SKUs,
   but a future direct query can never resurrect superseded evidence.

## Historical supersessions (2026-09-30, `xioix/buildout-26-ledger-hygiene`)

| Stale SKU | Entries | Superseded by | Origin |
|---|---|---|---|
| `SINCOR-DEFI-P01-YIELD` | 1 (`ev_11b1b596f771`, test_run) | `SINCOR-DEFI-P01-VAULT` | `xioix/sku-defi-p01-p07` |
| `SINCOR-DEFI-P05-INSURANCE` | 2 (`ev_e27e4f64ef46` test_run, `ev_0adf75900649` invariant_test) | `SINCOR-DEFI-P05-MUTUAL` | `xioix/sku-defi-p01-p07` |

Notes `ev_*` (appended same wave) document each supersede in-ledger. The canonical SKUs
already hold re-recorded evidence (waves 20/21); the superseded entries are retained for
audit history only and count toward nothing.
