"""
Root-level pytest configuration.
Excludes test files that use module-level script execution (not pytest-compatible).
"""
import os

# These script-style test files use module-level sys.exit; exclude from collection
collect_ignore = [
    "tests/test_units.py",
    "tests/test_value.py",
]
