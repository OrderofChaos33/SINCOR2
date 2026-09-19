# Salt files and environment debris

## `sinc_lbp_salt.json` / `sinc_lbp_verified_salt.json`

These are **CREATE2 search artifacts** (outer salt, strategy address, block
range). They are not private keys. They are public because they let anyone
reproduce how a deterministic address was found.

Decision 2026-09-19: **keep public**, documented here. If a future salt file
ever includes a keystore, mnemonic, or funded key, it is a secret and must
not be committed.

## `.env`

`.env` is gitignored. `.env.example` and `.env.test` are fixtures and stay.

## `.agents/`

Not present on current `main`. `.gitignore` now excludes it so plugin
tooling cannot be committed by accident.
