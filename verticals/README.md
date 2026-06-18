# Vertical Packs — Production Architecture

Each vertical implements domain-specific agents that integrate with SINCOR2’s marketplace, orchestration core, and on-chain settlement.

All verticals follow the same high-standard structure:
- Rich `VerticalAgent` base with circuit breaker protection
- Strongly typed Pydantic schemas
- Detailed capability definitions
- Clear A2A Agent Card output

Current status: Wired to the live runtime via `platform_bootstrap.py`. Vertical agents dispatch through A2A for registered skill ids. External API integrations remain the next implementation step per vertical.
