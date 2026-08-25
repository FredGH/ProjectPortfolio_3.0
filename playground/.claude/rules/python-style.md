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

Use Google-style docstrings for **all** functions and classes — public and private:

**Functions and methods** — include `Args`, `Returns`, and `Raises` whenever relevant:

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

Private methods follow the same format:

```python
def _validate_record(self, record: dict) -> bool:
    """Check that a record contains all required fields.

    Args:
        record: A single raw data dict to validate.

    Returns:
        True if the record is valid, False otherwise.
    """
```

**Classes** — include a class-level docstring and document all public attributes under `Attributes`:

```python
class DataProcessor:
    """Normalise and validate raw records from the source API.

    Attributes:
        batch_size: Number of records processed per batch.
        strict_mode: If True, raises on the first invalid record instead of skipping it.
    """

    def __init__(self, batch_size: int = 100, strict_mode: bool = False) -> None:
        """Initialise the processor.

        Args:
            batch_size: Number of records to process per batch.
            strict_mode: Whether to raise on invalid records instead of skipping.
        """
        self.batch_size = batch_size
        self.strict_mode = strict_mode
```

**Parameters summary** — every parameter in every function/method signature must be described in `Args`. Omit `Args` only when the function takes no parameters.

## Type Hints

- Required on all public function signatures
- Use `from __future__ import annotations` for forward references
- Use `list[T]` / `dict[K, V]` (not `List` / `Dict`) for Python 3.9+

## General

- Max line length: 88 characters
- No unused imports
- No bare `except:` clauses — always specify the exception type
- Prefer f-strings over `.format()` or `%`
