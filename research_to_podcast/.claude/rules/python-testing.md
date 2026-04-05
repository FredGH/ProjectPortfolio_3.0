# Testing Rules

Defines how tests should be written in this project.

## Framework

- Use `unittest` with `coverage` for test execution
- Run tests from the relevant sub-package directory:
  ```bash
  coverage run -m unittest discover
  coverage report -m
  ```

## Test Structure

```
src/
  module/
    feature.py
tests/
  module/
    test_feature.py
```

- Mirror the source tree under `tests/`
- One test file per source module
- Test class per logical unit: `class TestFeatureName(unittest.TestCase)`

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
- Integration points: database queries, external API calls (use real connections in integration tests, not mocks)

## Test Quality Rules

- No mocking the database — integration tests must use a real connection
- Each test must be independent — no shared mutable state between tests
- Use `setUp` / `tearDown` for fixtures, not module-level globals
- Assert on specific values, not just that something was called

## Coverage Target

- Minimum 80% line coverage for new code
- 100% branch coverage for critical business logic
