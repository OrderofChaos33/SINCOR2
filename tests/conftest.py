# Exclude standalone runner scripts from pytest collection.
# These scripts are meant to be executed directly (e.g. `python test_value.py`),
# not collected as pytest test modules.
collect_ignore = [
    "test_engines_simple.py",
    "test_value.py",
    "run_all_tests.py",
]
