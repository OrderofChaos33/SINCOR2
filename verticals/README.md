# Vertical Packs

Vertical packs encapsulate domain-specific agent logic, schemas, and workflows that integrate with the core SINCOR2 marketplace and orchestration layers.

Each vertical follows a consistent, extensible structure designed for production use:
- Clear capability definitions
- Pydantic schemas for task input/output
- Base agent class with proper interfaces
- Logging and error handling patterns
- Clear integration points with A2A discovery and settlement

Current verticals are in active scaffolding phase with production-grade foundations. Real implementations should extend the provided base classes and register capabilities via Agent Cards.
