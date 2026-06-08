# Healthcare Vertical

The healthcare pack provides administrative automation for payer-facing and credentialing-heavy workflows while preserving the controls expected in regulated environments.

## Modules

- `rcm_agent.py` — prior authorization, eligibility verification, claim tracking, and ERA processing with mock EDI 270/271/278/835/837 structures.
- `credentialing_agent.py` — provider credentialing support for CAQH, payer enrollment, privileges, and license surveillance.
- `agent_card.json` — A2A-compliant Agent Card describing the vertical's externally discoverable skills.

## Primary use cases

- outpatient and specialty practice revenue cycle workflows;
- payer enrollment orchestration for new providers and facilities;
- denials prevention support via eligibility and prior auth validation;
- remittance normalization for finance and operations teams.
