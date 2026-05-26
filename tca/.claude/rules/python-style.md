# Code Style Rules

Enforces formatting, naming, and style conventions for this project.

## Formatting

- Use `black` for Python formatting (line length: 88)
- Use `isort` for import ordering (profile: black)
- Use `ruff` for linting

## Naming Conventions

| Element | Convention | Example |
|---|---|---|
| Variables | `snake_case` | `user_count` |
| Functions | `snake_case` | `get_user_by_id()` |
| Classes | `PascalCase` | `UserRepository` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_RETRIES` |
| Private methods | `_snake_case` | `_validate_input()` |
| Modules/files | `snake_case` | `data_processor.py` |

## Import Order

1. Standard library
2. Third-party packages
3. Local/project imports

Separate each group with a blank line.

## Docstrings

Use Google-style docstrings for public functions and classes:

```python
def process_data(records: list[dict]) -> list[dict]:
    """Process raw records into normalized form.

    Args:
        records: List of raw data dicts from the source API.

    Returns:
        List of normalized dicts ready for database insertion.

    Raises:
        ValueError: If any record is missing a required field.
    """
```

## Type Hints

- Required on all public function signatures
- Use `from __future__ import annotations` for forward references
- Use `list[T]` / `dict[K, V]` (not `List` / `Dict`) for Python 3.9+

## General

- Max line length: 88 characters
- No unused imports
- No bare `except:` clauses — always specify the exception type
- Prefer f-strings over `.format()` or `%`
