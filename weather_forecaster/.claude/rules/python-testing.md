# Testing Rules

Defines how tests should be written in this project.

## Framework

- Use `pytest` for test execution
- Run from the project root with `PYTHONPATH` set:

```bash
# Unit tests only (no API key required)
PYTHONPATH=. python3.11 -m pytest tests/test_extraction.py tests/test_bronze_loader.py tests/test_weather_mock.py -v

# Integration tests (requires OPENWEATHER_API_KEY in .env)
PYTHONPATH=. python3.11 -m pytest tests/test_weather_api.py -v -m integration

# All tests
PYTHONPATH=. python3.11 -m pytest tests/ -v
```

## Test Structure

```
weather_forecaster_sources/
  extraction.py
  bronze_loader.py
  weather_source.py
tests/
  test_extraction.py
  test_bronze_loader.py
  test_weather_mock.py
  test_weather_api.py
```

- One test file per source module
- Test class per logical unit: `class TestFeatureName(unittest.TestCase)`

## Test Categories

| Category | Files | Marker | Requires |
|---|---|---|---|
| Unit | `test_extraction.py`, `test_bronze_loader.py`, `test_weather_mock.py` | (none) | Nothing |
| Integration | `test_weather_api.py` | `@pytest.mark.integration` | Valid API key in `.env` |

## Naming

- Test files: `test_<module_name>.py`
- Test classes: `Test<ClassName>`
- Test methods: `test_<behavior_under_test>` — describe what it does, not what it calls

```python
# Good
def test_returns_empty_list_when_no_records_found(self):

# Bad
def test_get_records(self):
```

## What to Test

- Happy path for every public function
- Edge cases: empty input, None, boundary values
- Error cases: invalid input raises the correct exception
- Integration points: use real DuckDB connections (via `tmp_path`), never mock the database

## Test Quality Rules

- No mocking the database — use real DuckDB with temporary files (`tmp_path` fixture)
- Each test must be independent — no shared mutable state between tests
- Use `setUp` / `tearDown` or pytest fixtures for test setup
- Assert on specific values, not just that something was called

## Coverage Target

- Minimum 80% line coverage for new code
- 100% branch coverage for critical business logic
