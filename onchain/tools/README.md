# onchain/tools — vendored solc for the offline P24 CI step

`solc-0.8.26` is the Linux x86-64 solc 0.8.26 binary, byte-identical to the
one Foundry's version manager (svm) downloads from
https://binaries.soliditylang.org/:

- version: `0.8.26+commit.8a97fa7a.Linux.g++`
- sha256: `d5f23436f443edb85d8e76906d12f0a86ce0490e7663a9e608efeb7a93f149ef`
- size: 15,434,456 bytes

## Why it is vendored

`.github/workflows/pr-ci.yml` runs the P24 local-simulation safety
regression as `FOUNDRY_PROFILE=p24 forge test --offline
--match-path test/p24/P24Deploy.t.sol` with `working-directory: onchain`.
`[profile.p24]` in `onchain/foundry.toml` pins `solc` to this binary's path.

A fresh GitHub runner has an empty `~/.svm` (foundry-toolchain installs
forge only, never a solc), and `--offline` blocks the download, so the step
failed in <0.1s with `Error: can't install missing solc 0.8.26 in offline
mode` — before any test ran (CI runs 43296819660, 43296915440 on main
2ac84a53). Pointing the profile at this committed binary makes the exact
0.8.26 compiler available with no download. The workflow file itself is
untouched (GitHub App cannot push workflow changes).

Do NOT replace this binary with a different solc version: the P24 test and
deploy script carry an exact `pragma solidity 0.8.26;`.
