## Summary

<!-- 1-3 bullets describing what changed and why. -->

## Test plan

- [ ] `uv run ruff check .` passes
- [ ] `uv run mypy app` passes
- [ ] `uv run pytest -m "not slow"` passes
- [ ] `uv run pytest -m slow` passes (or N/A: explain)
- [ ] Manual verification: <describe>

## Security checklist

- [ ] No new secrets, keys, or credentials committed (test fixtures only in `tests/fixtures/jwt_test_keys/`)
- [ ] No new dependencies with known HIGH/CRITICAL CVEs
- [ ] Changes to authn/authz, crypto, MQ, SQL, or `subprocess`-style code are flagged in the description
- [ ] Dockerfile / docker-compose changes considered for IaC scan impact
