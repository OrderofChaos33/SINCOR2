# Untracked-Files Resolution (gap-audit item 6)

**Wave:** 41 · **Branch:** `xioix/buildout-41-untracked-files` · **Base:** `fd96801`
**Status:** verification + correction of wave 23's untracked-file resolutions.

Wave 23 (`xioix/buildout-23-p1-cleanup`, `4391476`) resolved most of this item.
Wave 41 verified each resolution against the base tree and found one real
defect: the root-level `foundry.lock` / `.gitmodules` w23 added are wrong
duplicates. This doc records the final disposition of every item.

## Disposition table

| Item | Disposition | Evidence |
|---|---|---|
| `README.DRAFT.md` | **Explicitly deferred** — full draft README awaiting founder review. Not tracked, not deleted. Do not merge or remove without the founder's call. (Deferral already recorded in w23's `docs/README.md`.) | No copy exists anywhere in git history (`git log --all -- README.DRAFT.md` is empty); it was a local-only file at audit time. |
| `MONEY_FLOW.md` | **Resolved: kept as a doc** — tracked as `docs/architecture/MONEY_FLOW.md` (w23). It is the canonical money-flow spec ("this doc wins" on disagreements). | Added in `4391476`, 81 lines. |
| `toa_money_move_rehearsal.py` | **Resolved: tracked** at repo root (w23). Referenced in `docs/README.md`. Still the standing TOA rehearsal script per MEMORY. | `git ls-tree` on w23 shows both scripts tracked. |
| `toa_bankruptcy_policy_rehearsal.py` | **Resolved: tracked** at repo root (w23). | Same as above. |
| `.gitmodules` (repo root) | **REJECTED — do not merge w23's version.** See §2. | — |
| `foundry.lock` (repo root) | **REJECTED — do not merge w23's version.** The canonical lock is `onchain/foundry.lock`, already tracked on base. See §2. | — |

## §2 — The `.gitmodules` / `foundry.lock` defect (wave-41 finding)

Wave 23 added two files at the **repo root** that must not survive the merge:

1. **Root `foundry.lock` is a stale subset duplicate.** Base already tracks the
   complete canonical lock at `onchain/foundry.lock` with all six pinned
   dependencies: `forge-std` v1.9.7, `openzeppelin-contracts` v5.5.0,
   `permit2`, `uniswap-hooks`, `v4-core`, `v4-periphery`. Wave 23's root copy
   contains only the first two. Merging it would introduce a second,
   incomplete lockfile that drifts from the real one.

2. **Root `.gitmodules` points at the wrong layout.** It declares submodule
   paths `lib/forge-std` and `lib/openzeppelin-contracts` at the repo root,
   but the Foundry project lives in `onchain/` (`onchain/foundry.toml`,
   `libs = ["lib"]`, `remappings.txt` resolving `@openzeppelin/...`,
   `@uniswap/...`, `forge-std/`, `v4-core/` etc. to `onchain/lib/...`).
   Correct submodule paths would be `onchain/lib/...`, and wave 23 committed
   **no gitlinks at all** — the `.gitmodules` is dangling either way.

### Merge instruction

When the integration pass merges `xioix/buildout-23-p1-cleanup`:
- **Keep** `onchain/foundry.lock` (base version, 6 deps).
- **Drop** w23's root `foundry.lock` — do not add it.
- **Drop** w23's root `.gitmodules` — do not add it. If submodules are ever
  actually registered, paths must be `onchain/lib/<name>` with committed
  gitlinks, matching `onchain/remappings.txt`.
- **Keep** everything else from w23's file resolutions
  (`docs/architecture/MONEY_FLOW.md`, both `toa_*.py` scripts, the
  `README.DRAFT.md` deferral note in `docs/README.md`).

### Correction to MERGE_PLAN.md

`MERGE_PLAN.md:50` lists w23's `.gitmodules` / `foundry.lock` as files to
take, and `:272` says "w23 tracked `foundry.lock`" for the w12 forge-std
need. Both should be read with this doc's correction: the file that
matters is the already-tracked `onchain/foundry.lock`; w23's root-level
copies are rejected, not taken.

## Acceptance

- [x] Every audit-item-6 file has a recorded disposition above.
- [x] Nothing deleted: the only rejected files are w23's unmerged
      root-level additions, which never existed on base (quarantine rule
      from wave 23 respected — nothing on base was touched).
- [x] `git status` on this branch is clean apart from this doc.
- [x] The one remaining open thread (`README.DRAFT.md` founder review) is
      explicitly deferred with its owner named.
