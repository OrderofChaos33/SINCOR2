# Contributing to SINCOR

Thank you for your interest in contributing to SINCOR — the AI Business Automation Platform.

## Getting Started

1. **Fork** the repository and clone your fork
2. Create a **feature branch**: `git checkout -b feature/your-feature-name`
3. Make your changes following the guidelines below
4. **Test** your changes locally
5. Open a **Pull Request** against `main`

## Development Setup

```bash
git clone https://github.com/YOUR_FORK/SINCOR2.git
cd SINCOR2
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Configure your local .env
PYTHONPATH=src python run.py
```

## Code Style

- **Python**: Follow PEP 8. Use 4-space indentation.
- **Docstrings**: Include for all public functions and classes.
- **Type hints**: Encouraged for new code.
- **Naming**: Descriptive names; avoid single-letter variables except in comprehensions.

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR.
- Update `README.md` if you change any user-facing functionality.
- Ensure all existing tests pass before submitting.
- Add tests for any new functionality.
- Reference any relevant issues in the PR description.

## Reporting Issues

- **Bugs**: Open a GitHub issue with reproduction steps and expected vs actual behavior.
- **Security issues**: See [SECURITY.md](SECURITY.md) — do NOT open public issues for security vulnerabilities.
- **Feature requests**: Open a GitHub Discussion or issue with the `enhancement` label.

## Agent Development

To add a new AI agent:

1. Create `agents/E-<star-name>-<id>.yaml` following the existing agent YAML schema
2. Register the agent archetype in `src/sincor2/agency_kernel.py`
3. Add the agent's persona in `src/sincor2/persona_engine.py`
4. Document capabilities in the agent YAML

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
