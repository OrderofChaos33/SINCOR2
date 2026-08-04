# SINCOR Phase 2 — Asset Inventory

**Document**: `docs/launch/ASSET_INVENTORY_2026-08.md`  
**Audit Date**: 2026-08-04  
**Auditor**: Builder archetype (automated + manual scan)  
**Scope**: All assets relevant to the Harvest Moon activation

---

## 1. Templates (`templates/`)

| File | Purpose | Harvest Relevance |
|------|---------|-------------------|
| `home.html` | Main landing page | Aesthetic reference for harvest page |
| `harvest.html` | **NEW** Harvest Moon claim page | Primary activation surface |
| `buy.html`, `buy_tokens.html`, `buy_stripe.html` | Purchase flows | Post-claim conversion target |
| `pricing.html` | Plan pricing | Post-claim upgrade destination |
| `operator_dashboard.html` | A2A operator UI | External agent discovery surface |
| `sinc_gateway.html`, `sinc_acceptance.html` | SINC payment flows | Claim-to-purchase bridge |
| `sin-airdrop.html` | Existing airdrop page | **Review**: ensure no conflicting campaign language |
| `signup.html`, `login.html` | Auth flows | Post-claim account creation |
| `onboarding.html` | Customer onboarding | Post-claim engagement |
| `terms.html`, `privacy.html`, `security.html` | Legal pages | Must link from harvest footer |
| `launch_partners.html` | Partner page | Partner outreach post-gate |

**Action items**:
- [ ] Review `sin-airdrop.html` for conflicting language before soft launch
- [ ] Ensure `terms.html` covers utility token access credits

---

## 2. Static Assets (`static/`)

| Asset | Type | Status |
|-------|------|--------|
| `sincor_logo.svg` | Brand logo | ✅ Available |
| `sincor_favicon.png` | Favicon | ✅ Available |
| `sincor_og.jpg` | OG image | ✅ Available |
| `styles.css` | Main stylesheet | ✅ Available |
| `harvest.css` | **NEW** Harvest CSS | ✅ Created |
| `js/sinc_wallet.js` | Wallet connect | ✅ Available (reuse in harvest) |
| `js/harvest.js` | **NEW** Harvest JS | ✅ Created |
| `tokenlists/sincor.tokenlist.json` | Token list | ✅ Maintained |
| `docs/SINCOR_whitepaper.md` | Whitepaper | ✅ Linked from harvest footer |

---

## 3. Source Blueprints (`src/sincor2/`)

| Module | Purpose | Harvest Integration |
|--------|---------|---------------------|
| `app.py` | App factory | ✅ Harvest blueprint registered |
| `mvp_app.py` | Main Flask app | ✅ `/harvest` + `/early` routes added |
| `blueprints/harvest.py` | **NEW** Harvest API | ✅ Created |
| `blueprints/marketplace.py` | Agent marketplace | Feed harvest skill into marketplace |
| `blueprints/payments.py` | Payment processing | Post-claim purchase flow |
| `blueprints/auth.py` | Authentication | Post-claim account creation |
| `blueprints/waitlist.py` | Waitlist signup | Harvest notify-me form |
| `a2a_integration.py` | A2A agent protocol | Harvest skill exposed via A2A |
| `rate_limiter.py` | Rate limiting | Applied to harvest endpoints |
| `production_logger.py` | Structured logging | All harvest events use this |
| `x402_payments.py` | X402 payment flow | AXM/SINC settlement routing |

---

## 4. Agent YAMLs (`agents/`)

| File | Archetype | Harvest Role |
|------|-----------|--------------|
| `E-harvest-claim-agent.yaml` | **NEW** Builder | Claim lifecycle, monitoring, conversion |
| `E-antares-13.yaml` | Negotiator | Demo pipeline, gate reporting |
| `E-arcturus-10.yaml` | Caretaker | Customer confirmation, onboarding |
| `E-betelgeuse-11.yaml` | Builder/WebBuilder | Vertical conversion, checkout |
| `E-strategist-45.yaml` | Strategist | Campaign strategy |
| `E-critic-46.yaml` | Critic | Copy review, utility language audit |
| `E-toa-44.yaml` | TOA | Revenue ranking, gate check-in |
| `archetypes/Negotiator.yaml` | Negotiator | Sales outreach |
| `archetypes/Caretaker.yaml` | Caretaker | Customer success |
| `archetypes/Builder.yaml` | Builder | Technical execution |
| `archetypes/Director.yaml` | Director | Strategic oversight |
| `archetypes/Auditor.yaml` | Auditor | Code + contract review |
| `toa/config.py` | TOA | ✅ harvest_conversion weight added |

**Action items**:
- [ ] Wire E-harvest-claim-agent into 5-minute scheduler
- [ ] Confirm E-antares-13 and E-arcturus-10 emit `harvest_gate_checkin` events

---

## 5. Launch Content Engine (`launch_content_engine/`)

| File | Purpose | Harvest Use |
|------|---------|-------------|
| `run_cycle.py` | Content cycle runner | Activate harvest content track |
| `agent_spotlight.py` | Agent spotlight posts | Feature harvest agents |
| `agent_personas.json` | Content personas | Use for harvest copy |
| `config/posting_schedule.yaml` | Post schedule | Add harvest campaign slots |
| `config/topic_rotation.yaml` | Topic rotation | Add harvest topics |
| `config/disclosure_strings.yaml` | Legal disclosures | **Must** include utility-only language |
| `review_queue.py` | Content approval | All harvest copy passes through here |
| `adapters/farcaster_api.py` | Farcaster | Activation announcement channel |
| `content_topics.json` | Topic bank | Add harvest topics |

**Action items**:
- [ ] Add harvest disclosure string to `disclosure_strings.yaml`
- [ ] Add harvest campaign topics to `content_topics.yaml`
- [ ] Schedule activation announcement (held until gate cleared)

---

## 6. Media Assets (`media/sadas/`)

| File | Type |
|------|------|
| `Executive_One_Pager_SADAS.md` | Exec summary |
| `Press_Release_SADAS.md` | Press release template |
| `Social_Assets_SADAS.md` | Social copy |

**Action items**:
- [ ] Create harvest-specific versions of these assets (agent-generated, post-gate)
- [ ] All copy reviewed against utility-only rule before use

---

## 7. On-Chain Contracts (`onchain/src/` + root)

### Existing contracts (Base mainnet deployed)

| Contract | Address | Role |
|----------|---------|------|
| SINC Token (v3) | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` | Claim currency |
| Treasury | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | Claim pool source |
| Axiom (AXM) | `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822` | A2A payment token |
| SincBondingCurve | `onchain/src/SincBondingCurve.sol` | Official buy path |
| SincGenesisNFT | `onchain/src/SincGenesisNFT.sol` | Early holder NFT |
| SharedLiquidityVault | `onchain/src/SharedLiquidityVault.sol` | DeFi integration |
| SINCLending | `onchain/src/SINCLending.sol` | Lending protocol |

### New contracts (Harvest Moon)

| Contract | File | Status |
|----------|------|--------|
| HarvestClaim | `onchain/src/HarvestClaim.sol` | ✅ Created — pending deploy |

### Root-level contracts (legacy — review ownership)

| File | Notes |
|------|-------|
| `SINC_v3.sol` | Legacy token source — do not redeploy |
| `SINCBondingCurve.sol` | Legacy — superseded by `onchain/src/SincBondingCurve.sol` |
| `SINCPlatformAccess.sol` | Platform access contract — review for overlap with HarvestClaim |

### Existing access / vesting contracts

Audit of `SINCPlatformAccess.sol` required to confirm there is no overlap with HarvestClaim eligibility gate. No vesting contracts found in current codebase — HarvestClaim uses 30-day claim window (not vesting) by design.

---

## 8. Scripts (`scripts/`)

| Script | Purpose | Harvest Relevance |
|--------|---------|-------------------|
| `generate_harvest_merkle.py` | **NEW** Merkle tree generator | Core Phase 1 tool |
| `whitelist_token.py` | Token whitelisting | Run before activation |
| `register_agent.py` | Agent registration | Register harvest agent |
| `certify_token.py` | Token certification | Basescan verification |
| `register_blockscout_token.py` | Blockscout listing | Token discovery |
| `defi_swarm_checkin_scheduler.py` | DeFi check-in | Runs harvest reporting loop |

---

## 9. Configuration (`config/`)

| File | Purpose | Action |
|------|---------|--------|
| `x402_pricing.yaml` | X402 skill pricing | Ensure harvest skill is priced at 0 |
| `launch_partners.yaml` | Partner list | Activate for harvest outreach |
| `agent_quota.py` | Agent compute quotas | Set harvest agent quota |
| `.env.example` | Env var reference | Document new HARVEST_* vars |

**New environment variables required** (Railway secrets only):

```
HARVEST_PAGE_ENABLED=false          # Set to true only after gate cleared
HARVEST_PROOFS_PATH=                # Path to proofs.json after Merkle generation
HARVEST_CONTRACT_ADDRESS=           # Set after deployment
HARVEST_DB_PATH=data/harvest.db     # Defaults to this if unset
```

---

## 10. Summary — Open Action Items

| Priority | Item | Owner | Due |
|----------|------|-------|-----|
| 🔴 HIGH | Run test suite for harvest blueprint | Builder | Aug 10 |
| 🔴 HIGH | Confirm HARVEST_PAGE_ENABLED=false in Railway before any public push | Builder | Aug 10 |
| 🔴 HIGH | Review `sin-airdrop.html` for conflicting language | Auditor | Aug 10 |
| 🟡 MED | Add harvest disclosure to `launch_content_engine/config/disclosure_strings.yaml` | Copywriter | Aug 12 |
| 🟡 MED | Wire harvest agent into 5-min scheduler | Builder | Aug 12 |
| 🟡 MED | Generate test Merkle tree with dummy wallets for dry-run | Builder | Aug 15 |
| 🟡 MED | Deploy HarvestClaim.sol to Base Sepolia + run Foundry tests | Builder + Auditor | Aug 20 |
| 🟢 LOW | Create harvest media assets (agents generate, post-gate) | Content swarm | Sep 1 |
| 🟢 LOW | Final Basescan verification of HarvestClaim | Auditor | Sep 25 |
