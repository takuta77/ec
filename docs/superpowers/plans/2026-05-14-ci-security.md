# CI + Security Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build GitHub Actions pipelines for the EC API repo that gate every PR on lint/type/test plus security checks (deps / SAST / secrets / container / dockerfile / IaC / SBOM), and run a nightly re-scan on main.

**Architecture:** Two main workflows (`ci.yml`, `security.yml`) plus a nightly schedule that reuses the security jobs via a callable workflow. SARIF results flow into GitHub Code Scanning; SBOM uploads as artifact. Dependabot manages dependency upgrades. Local reproduction uses `uv run` / `fastapi` commands documented in README — no Makefile or task runner introduced.

**Tech Stack:** GitHub Actions, `astral-sh/setup-uv`, ruff, mypy, pytest, pip-audit, Semgrep, gitleaks, Trivy, hadolint, Checkov, Syft, Dependabot, actionlint (local validation).

---

## Working Branch Prerequisite

This plan touches `.github/workflows/*` and validates them against a real Python app. The branch must contain the EC API source code (`app/`, `docker/`, `pyproject.toml` with FastAPI/uv config, `tests/`).

`feature/ci-security` was originally branched from `main` which has **only docs**. Before starting Task 1, the implementer must either:

- **(Option A, preferred)** Wait until `feature/ec-api-impl` and `feature/cart-reopen-cancel-lifespan` are merged into `main`, then rebase `feature/ci-security` onto the new `main`.
- **(Option B)** Rebase `feature/ci-security` onto `feature/cart-reopen-cancel-lifespan` so the app code is locally present. Note: when the parent branches eventually merge, this branch will need a final rebase onto `main`.

Pick one before Task 1. Implementer should verify by running:

```bash
ls app/main.py docker/Dockerfile pyproject.toml docker-compose.yml
# All four must exist.
```

If they don't, stop and rebase as above before continuing.

---

## File Structure

```
.github/
├── workflows/
│   ├── ci.yml                    # Lint, type, test (required status checks)
│   ├── security.yml              # Security suite on PR + push to main
│   ├── nightly-security.yml      # cron + dispatch, reuses _security-reusable
│   └── _security-reusable.yml    # Callable workflow with all security jobs
├── dependabot.yml                # pip, github-actions, docker (weekly)
└── pull_request_template.md      # PR checklist
.gitleaks.toml                    # Allowlist for test fixture keys
.semgrepignore                    # Allowlist for migration auto-gen lines
.security/
├── pip-audit-ignore.yaml         # Expiring CVE allowlist
└── README.md                     # How to add/remove ignore entries
pyproject.toml                    # Modified: pip-audit + semgrep added to dev deps
README.md                         # Modified: §9 commands + branch protection setup
```

---

## Task 1: Add security dev dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml` (the `[dependency-groups] dev` array)

**Goal:** Add `pip-audit` and `semgrep` so they can be invoked via `uv run` locally and in CI.

- [ ] **Step 1: Inspect the existing `[dependency-groups]` block**

Run: `grep -n -A 20 "dependency-groups" pyproject.toml`
Expected: shows `dev = [ ... ]` with current entries (pytest, ruff, mypy, etc.).

- [ ] **Step 2: Add the new entries**

Edit `pyproject.toml`, find the `dev = [...]` array, and append:

```toml
    "pip-audit>=2.7",
    "semgrep>=1.95",
```

Keep entries alphabetically (or in existing convention).

- [ ] **Step 3: Sync the lockfile**

Run: `uv sync --frozen` (this should fail because we changed the manifest)
Expected: error about lockfile mismatch. Then run:

```bash
uv lock
uv sync
```

Expected: `uv.lock` updated; commands install successfully.

- [ ] **Step 4: Verify the tools are callable**

Run:

```bash
uv run pip-audit --version
uv run semgrep --version
```

Expected: both print versions without error.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add pip-audit and semgrep for security CI"
```

---

## Task 2: Create CI workflow — lint + type jobs

**Files:**
- Create: `.github/workflows/ci.yml`

**Goal:** Set up `ci.yml` with `lint` and `type` jobs only; test jobs come in Task 3.

- [ ] **Step 1: Install actionlint locally for validation**

Run: `brew install actionlint` (skip if already installed)
Expected: `actionlint --version` works.

- [ ] **Step 2: Create the workflow file**

Create `.github/workflows/ci.yml` with this exact content:

```yaml
name: ci

on:
  pull_request:
    types: [opened, synchronize, reopened]
  push:
    branches: [main]

permissions:
  contents: read

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    name: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
          enable-cache: true
      - run: uv sync --frozen
      - name: ruff check
        run: uv run ruff check .
      - name: ruff format --check
        run: uv run ruff format --check .

  type:
    name: type
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
          enable-cache: true
      - run: uv sync --frozen
      - name: mypy strict
        run: uv run mypy app
```

- [ ] **Step 3: Validate with actionlint**

Run: `actionlint .github/workflows/ci.yml`
Expected: no output (exit 0). If errors, fix YAML/syntax before continuing.

- [ ] **Step 4: Verify the commands match what the app needs**

Run locally:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```

Expected: all three succeed (or surface existing issues unrelated to CI plumbing).
If any failure looks unrelated to formatting (e.g., `mypy app` finds real type errors), record them and continue — CI failures on existing code are fixed in their own PRs, not this one.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add lint and type jobs"
```

---

## Task 3: Add test-unit and test-slow jobs to ci.yml

**Files:**
- Modify: `.github/workflows/ci.yml` (append two jobs)

**Goal:** Run pytest, separating fast unit tests from Testcontainers-backed slow tests.

- [ ] **Step 1: Append the two jobs**

Open `.github/workflows/ci.yml` and append (keep `lint` and `type` as-is):

```yaml
  test-unit:
    name: test-unit
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
          enable-cache: true
      - run: uv sync --frozen
      - name: pytest (unit)
        run: uv run pytest -m "not slow" --cov=app --cov-report=xml --cov-report=term
      - uses: actions/upload-artifact@v4
        with:
          name: coverage-xml
          path: coverage.xml
          retention-days: 14

  test-slow:
    name: test-slow
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
          enable-cache: true
      - run: uv sync --frozen
      - name: pytest (slow / Testcontainers)
        run: uv run pytest -m slow --maxfail=3
```

- [ ] **Step 2: Validate workflow**

Run: `actionlint .github/workflows/ci.yml`
Expected: no errors.

- [ ] **Step 3: Verify the commands run locally**

Run:

```bash
uv run pytest -m "not slow" --cov=app --cov-report=xml
ls coverage.xml
```

Expected: tests pass and `coverage.xml` is created.

For slow tests, ensure Docker is running, then:

```bash
uv run pytest -m slow --maxfail=3
```

Expected: tests pass (may take several minutes for Testcontainers to start).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add unit and slow (Testcontainers) test jobs"
```

---

## Task 4: Create reusable security workflow — deps + sast + secrets

**Files:**
- Create: `.github/workflows/_security-reusable.yml`

**Goal:** Define a callable workflow that runs the three required security jobs. Container/dockerfile/IaC/SBOM are added in Tasks 5–6.

- [ ] **Step 1: Create the file**

Create `.github/workflows/_security-reusable.yml`:

```yaml
name: _security-reusable

on:
  workflow_call:
    inputs:
      secrets-full-history:
        description: "If true, gitleaks scans full git history (used by nightly). If false, scan diff only."
        type: boolean
        default: false

permissions:
  contents: read
  security-events: write   # required for SARIF upload

jobs:
  deps:
    name: deps (pip-audit)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
          enable-cache: true
      - run: uv sync --frozen
      - name: Export requirements from uv.lock
        run: uv export --no-hashes --no-dev > /tmp/requirements.txt
      - name: Build pip-audit args (apply allowlist + reject expired)
        id: audit-args
        run: |
          set -euo pipefail
          TODAY=$(date -u +%Y-%m-%d)
          EXPIRED=$(yq -r --arg today "$TODAY" \
            '.ignores[] | select(.expires_at <= $today) | "\(.vuln_id) (expired \(.expires_at))"' \
            .security/pip-audit-ignore.yaml || true)
          if [ -n "$EXPIRED" ]; then
            echo "::error::Expired entries in .security/pip-audit-ignore.yaml:"
            echo "$EXPIRED"
            exit 1
          fi
          IGNORE_ARGS=""
          while IFS= read -r vid; do
            [ -n "$vid" ] && IGNORE_ARGS="$IGNORE_ARGS --ignore-vuln $vid"
          done < <(yq -r '.ignores[].vuln_id // empty' .security/pip-audit-ignore.yaml)
          echo "args=$IGNORE_ARGS" >> "$GITHUB_OUTPUT"
      - name: Run pip-audit
        run: |
          uv run pip-audit \
            --requirement /tmp/requirements.txt \
            --strict \
            --format sarif \
            --output pip-audit.sarif \
            ${{ steps.audit-args.outputs.args }}
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: pip-audit.sarif
          category: pip-audit

  sast:
    name: sast (semgrep)
    runs-on: ubuntu-latest
    container:
      image: returntocorp/semgrep
    steps:
      - uses: actions/checkout@v4
      - name: Semgrep CI
        run: |
          semgrep ci \
            --config p/python \
            --config p/security-audit \
            --config p/owasp-top-ten \
            --config p/jwt \
            --sarif --output semgrep.sarif \
            --error
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: semgrep.sarif
          category: semgrep

  secrets:
    name: secrets (gitleaks)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: ${{ inputs.secrets-full-history && 0 || 1 }}
      - uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITLEAKS_CONFIG: .gitleaks.toml
          GITLEAKS_ENABLE_UPLOAD_ARTIFACT: "true"
          GITLEAKS_ENABLE_SUMMARY: "true"
```

- [ ] **Step 2: Note the dependency**

The `deps` step uses `yq` for parsing the ignore file. Ubuntu runners include `yq` out of the box (Mike Farah's Go version). If not present in your runner image, add a setup step:

```yaml
      - name: Install yq if missing
        run: which yq || sudo snap install yq
```

For now, skip this step — re-evaluate after first CI run.

- [ ] **Step 3: Validate**

Run: `actionlint .github/workflows/_security-reusable.yml`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/_security-reusable.yml
git commit -m "ci: add reusable security workflow (deps/sast/secrets)"
```

---

## Task 5: Add image + dockerfile + iac jobs to _security-reusable.yml

**Files:**
- Modify: `.github/workflows/_security-reusable.yml` (append three jobs)

**Goal:** Add container image scanning (Trivy), Dockerfile lint (hadolint), and docker-compose IaC scan (Checkov). All run as warn-only initially — they upload SARIF but use `continue-on-error: true`.

- [ ] **Step 1: Append the three jobs**

Append to `.github/workflows/_security-reusable.yml`:

```yaml
  image:
    name: image (trivy)
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4
      - name: Build image
        run: docker build -f docker/Dockerfile -t ec-api:ci .
      - uses: aquasecurity/trivy-action@0.28.0
        with:
          image-ref: ec-api:ci
          format: sarif
          output: trivy-image.sarif
          severity: HIGH,CRITICAL
          exit-code: "0"   # warn-only for now
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: trivy-image.sarif
          category: trivy

  dockerfile:
    name: dockerfile (hadolint)
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4
      - uses: hadolint/hadolint-action@v3.1.0
        with:
          dockerfile: docker/Dockerfile
          format: sarif
          output-file: hadolint.sarif
          no-fail: true   # warn-only
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: hadolint.sarif
          category: hadolint

  iac:
    name: iac (checkov)
    runs-on: ubuntu-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4
      - uses: bridgecrewio/checkov-action@v12
        with:
          file: docker-compose.yml
          output_format: sarif
          output_file_path: ./checkov.sarif
          soft_fail: true   # warn-only
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: checkov.sarif/results_sarif.sarif
          category: checkov
```

- [ ] **Step 2: Validate**

Run: `actionlint .github/workflows/_security-reusable.yml`
Expected: no errors.

- [ ] **Step 3: Optional local smoke test (skip if Docker unavailable)**

```bash
docker build -f docker/Dockerfile -t ec-api:ci .
trivy image --severity HIGH,CRITICAL ec-api:ci   # if trivy installed locally
```

Expected: image builds; Trivy reports findings (warn-only).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/_security-reusable.yml
git commit -m "ci: add container image/dockerfile/IaC scanning (warn-only)"
```

---

## Task 6: Add SBOM job to _security-reusable.yml

**Files:**
- Modify: `.github/workflows/_security-reusable.yml` (append one job)

**Goal:** Generate a CycloneDX SBOM with Syft and upload as artifact for audit/compliance trail.

- [ ] **Step 1: Append the job**

Append to `.github/workflows/_security-reusable.yml`:

```yaml
  sbom:
    name: sbom (syft)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: anchore/sbom-action@v0
        with:
          path: .
          format: cyclonedx-json
          artifact-name: sbom-cyclonedx.json
          output-file: sbom-cyclonedx.json
      - uses: actions/upload-artifact@v4
        with:
          name: sbom-cyclonedx
          path: sbom-cyclonedx.json
          retention-days: 90
```

- [ ] **Step 2: Validate**

Run: `actionlint .github/workflows/_security-reusable.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/_security-reusable.yml
git commit -m "ci: add SBOM generation via syft"
```

---

## Task 7: Create security.yml — calls reusable workflow on PR/push

**Files:**
- Create: `.github/workflows/security.yml`

**Goal:** Top-level workflow that fires on PR + push to main and delegates to the reusable workflow.

- [ ] **Step 1: Create the file**

Create `.github/workflows/security.yml`:

```yaml
name: security

on:
  pull_request:
    types: [opened, synchronize, reopened]
  push:
    branches: [main]

permissions:
  contents: read
  security-events: write

concurrency:
  group: security-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  scan:
    uses: ./.github/workflows/_security-reusable.yml
    secrets: inherit
    with:
      secrets-full-history: false
```

- [ ] **Step 2: Validate**

Run: `actionlint .github/workflows/security.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/security.yml
git commit -m "ci: add security workflow (PR + push to main)"
```

---

## Task 8: Create nightly-security.yml — cron + auto-issue on failure

**Files:**
- Create: `.github/workflows/nightly-security.yml`

**Goal:** Run the full security suite every night against main with full git history, and open an issue if anything fails.

- [ ] **Step 1: Create the file**

Create `.github/workflows/nightly-security.yml`:

```yaml
name: nightly-security

on:
  schedule:
    - cron: "0 18 * * *"   # 18:00 UTC = 03:00 JST
  workflow_dispatch:

permissions:
  contents: read
  security-events: write
  issues: write

concurrency:
  group: nightly-security
  cancel-in-progress: false

jobs:
  scan:
    uses: ./.github/workflows/_security-reusable.yml
    secrets: inherit
    with:
      secrets-full-history: true

  notify-on-failure:
    needs: scan
    if: failure()
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Open issue
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          DATE=$(date -u +%Y-%m-%d)
          RUN_URL="${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
          gh issue create \
            --title "[security] nightly scan failure $DATE" \
            --label "security,nightly" \
            --body "Nightly security scan failed. See run: $RUN_URL"
```

- [ ] **Step 2: Validate**

Run: `actionlint .github/workflows/nightly-security.yml`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/nightly-security.yml
git commit -m "ci: add nightly security scan with auto-issue on failure"
```

---

## Task 9: Create dependabot.yml

**Files:**
- Create: `.github/dependabot.yml`

**Goal:** Automatic weekly PRs for pip, github-actions, and docker base images.

- [ ] **Step 1: Create the file**

Create `.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "03:00"
      timezone: "Asia/Tokyo"
    open-pull-requests-limit: 5
    groups:
      python-minor-patch:
        update-types: ["minor", "patch"]
    labels:
      - "dependencies"
      - "python"

  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    labels:
      - "dependencies"
      - "github-actions"

  - package-ecosystem: "docker"
    directory: "/docker"
    schedule:
      interval: "weekly"
    labels:
      - "dependencies"
      - "docker"
```

- [ ] **Step 2: Validate YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/dependabot.yml'))"`
Expected: no output (valid YAML).

- [ ] **Step 3: Commit**

```bash
git add .github/dependabot.yml
git commit -m "ci: add dependabot config for pip/github-actions/docker"
```

---

## Task 10: Create allowlist files

**Files:**
- Create: `.gitleaks.toml`
- Create: `.semgrepignore`
- Create: `.security/pip-audit-ignore.yaml`
- Create: `.security/README.md`

**Goal:** Provide empty-by-default allowlist scaffolds that document the expiry/justification rules.

- [ ] **Step 1: Create .gitleaks.toml**

```toml
# .gitleaks.toml
# Allowlist for known-safe matches. Keep this minimal —
# real secrets must be removed, rotated, and not allowlisted.

title = "EC API gitleaks config"

[extend]
useDefault = true

[[rules.allowlist]]
description = "Test JWT fixtures committed for unit tests"
paths = [
  '''tests/fixtures/jwt_test_keys/.*\.pem$''',
  '''tests/fixtures/jwt_test_keys/.*\.json$''',
]
```

- [ ] **Step 2: Create .semgrepignore**

```text
# .semgrepignore
# Skip auto-generated Alembic migration files; review manually instead.
# Application code MUST NOT be added here — use inline `# nosemgrep: rule-id`
# with a reason comment if a finding is a true false positive.
migrations/versions/
```

- [ ] **Step 3: Create .security/pip-audit-ignore.yaml**

```yaml
# .security/pip-audit-ignore.yaml
#
# Each entry must include:
#   - vuln_id: GHSA or PYSEC id
#   - package: affected package
#   - reason: why we cannot/should not patch now (be specific)
#   - expires_at: YYYY-MM-DD, at most 60 days from when added
#
# The `deps` CI job rejects expired entries.
ignores: []
```

- [ ] **Step 4: Create .security/README.md**

```markdown
# .security/

Configuration for security tooling allowlists.

## pip-audit-ignore.yaml

Used by the `security / deps` CI job to suppress specific known
vulnerabilities. Every entry is **time-bounded** — `expires_at` must be set
to a date no more than 60 days in the future. Expired entries cause the
job to fail.

To add an entry:

1. Confirm the affected code path is not exercised by our app (or document
   the planned fix in `reason`).
2. Add an entry to `pip-audit-ignore.yaml`:
   ```yaml
   ignores:
     - vuln_id: GHSA-xxxx-yyyy-zzzz
       package: cryptography
       reason: "<why we are ignoring this for now>"
       expires_at: 2026-07-01
   ```
3. Open an Issue tracking the eventual remediation, referencing the
   `expires_at` date.

To remove an entry: delete it and ensure CI is green.
```

- [ ] **Step 5: Validate YAML**

Run:

```bash
python -c "import yaml; yaml.safe_load(open('.security/pip-audit-ignore.yaml'))"
```

Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add .gitleaks.toml .semgrepignore .security/
git commit -m "ci: add security allowlist scaffolds (gitleaks/semgrep/pip-audit)"
```

---

## Task 11: Create pull request template

**Files:**
- Create: `.github/pull_request_template.md`

**Goal:** Surface a security-relevant checklist on every PR so reviewers think about scope.

- [ ] **Step 1: Create the file**

Create `.github/pull_request_template.md`:

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add .github/pull_request_template.md
git commit -m "ci: add PR template with test + security checklist"
```

---

## Task 12: Update README with local commands + branch protection setup

**Files:**
- Modify: `README.md`

**Goal:** Document the §9 commands and how to configure branch protection in the GitHub UI.

- [ ] **Step 1: Inspect the current README structure**

Run: `cat README.md`
Expected: shows current sections. Identify a place to insert "Local checks" and "Branch protection" sections (typically after a "Getting started" / "Development" section).

- [ ] **Step 2: Append the new sections**

Append to `README.md`:

````markdown
## Local checks (same as CI)

CI runs each tool directly via `uv run`; you can reproduce locally with the
same commands. No Makefile or task runner is used.

### Development server

```bash
# Auto-reload dev server
uv run fastapi dev app/main.py

# Production-like (multi-worker)
uv run fastapi run app/main.py --workers 4
```

### Lint / type / tests

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -m "not slow"
uv run pytest -m slow         # requires Docker for Testcontainers
```

### Security

```bash
# Python dependency vulnerabilities
uv export --no-hashes --no-dev > /tmp/req.txt
uv run pip-audit -r /tmp/req.txt

# SAST
uv run semgrep ci \
  --config p/python \
  --config p/security-audit \
  --config p/owasp-top-ten \
  --config p/jwt

# Secrets scan
gitleaks detect --redact --no-banner

# Dockerfile lint
docker run --rm -i hadolint/hadolint < docker/Dockerfile

# Container image scan
docker build -f docker/Dockerfile -t ec-api:dev .
trivy image --severity HIGH,CRITICAL ec-api:dev
```

External binaries (`gitleaks`, `hadolint`, `trivy`) are installed via
`brew install gitleaks hadolint aquasecurity/trivy/trivy`. CI uses the
respective GitHub Actions, so these are optional for contributors.

## Branch protection (one-time setup)

Apply the following to `main` (and any long-lived `feature/*` branch):

1. Go to **Settings → Branches → Branch protection rules → Add rule**
2. Branch name pattern: `main`
3. Tick:
   - **Require a pull request before merging** (1 approval, dismiss stale reviews)
   - **Require status checks to pass before merging**
     - Required checks: `ci / lint`, `ci / type`, `ci / test-unit`, `ci / test-slow`, `security / scan / deps (pip-audit)`, `security / scan / sast (semgrep)`, `security / scan / secrets (gitleaks)`
     - **Require branches to be up to date**: ON
   - **Require linear history**: ON
   - **Do not allow force pushes**: ON
   - **Do not allow deletions**: ON

The `image`, `dockerfile`, `iac` jobs are warn-only and intentionally left
off the required list until their noise level is evaluated.
````

- [ ] **Step 3: Verify Markdown renders**

Open `README.md` in your editor / GitHub preview; check headings/anchors.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add local checks and branch protection setup to README"
```

---

## Task 13: End-to-end validation

**Files:**
- (No source changes; this task validates the pipeline via deliberate failures.)

**Goal:** Confirm each required gate actually blocks a bad PR. After verification, **revert** everything.

- [ ] **Step 1: Push the branch and open the main PR**

```bash
git push -u origin feature/ci-security
gh pr create --title "ci: add CI + security pipelines" --body "$(cat <<'EOF'
## Summary

- Adds GitHub Actions for lint/type/test (ci.yml) and security suite (security.yml + nightly-security.yml).
- Adds Dependabot config and allowlist scaffolds.
- Documents local reproduction in README; no Makefile introduced.

## Test plan

See Task 13 in the implementation plan: deliberate-failure PRs validate
each gate, then revert.
EOF
)"
```

Expected: PR opened; `ci` and `security` runs start automatically.

- [ ] **Step 2: Validate happy-path is green**

Wait for the PR to finish. All `ci.yml` jobs (`lint`, `type`, `test-unit`, `test-slow`) and the three required security jobs (`deps`, `sast`, `secrets`) must succeed. Warn-only jobs (`image`, `dockerfile`, `iac`) may have findings — record them but they should not block.

- [ ] **Step 3: Deliberate-fail test for `deps`**

In a new throwaway branch off this one:

```bash
git checkout -b test/ci-deps-fail
# Pin a package with a known CVE, e.g. an old jinja2
```

Edit `pyproject.toml`, add `"jinja2==2.10",` to the main `dependencies` array (or another known-vulnerable version), then:

```bash
uv lock
git add pyproject.toml uv.lock
git commit -m "test: introduce CVE for ci validation (DO NOT MERGE)"
git push -u origin test/ci-deps-fail
gh pr create --title "test: ci-deps-fail" --body "Validating deps gate." --draft
```

Expected: `security / scan / deps (pip-audit)` job fails. Verify in PR Checks UI.
Cleanup:

```bash
gh pr close test/ci-deps-fail --delete-branch
```

- [ ] **Step 4: Deliberate-fail test for `secrets`**

```bash
git checkout feature/ci-security
git checkout -b test/ci-secrets-fail
mkdir -p tests/__deliberate_fail__
cat > tests/__deliberate_fail__/leak.txt <<'EOF'
AWS_SECRET_ACCESS_KEY=AKIAEXAMPLEKEY1234567890ABCDEFGHIJK
EOF
git add tests/__deliberate_fail__/leak.txt
git commit -m "test: leak fake AWS key for ci validation (DO NOT MERGE)"
git push -u origin test/ci-secrets-fail
gh pr create --title "test: ci-secrets-fail" --body "Validating secrets gate." --draft
```

Expected: `security / scan / secrets (gitleaks)` job fails.
Cleanup:

```bash
gh pr close test/ci-secrets-fail --delete-branch
```

- [ ] **Step 5: Deliberate-fail test for `sast`**

```bash
git checkout feature/ci-security
git checkout -b test/ci-sast-fail
cat > app/_deliberate_fail.py <<'EOF'
import os

def run(cmd: str) -> None:
    os.system(cmd)  # known semgrep python.lang.security.audit.dangerous-system-call.dangerous-system-call
EOF
git add app/_deliberate_fail.py
git commit -m "test: introduce SAST violation for ci validation (DO NOT MERGE)"
git push -u origin test/ci-sast-fail
gh pr create --title "test: ci-sast-fail" --body "Validating sast gate." --draft
```

Expected: `security / scan / sast (semgrep)` job fails.
Cleanup:

```bash
gh pr close test/ci-sast-fail --delete-branch
```

- [ ] **Step 6: Deliberate-fail test for `test-unit`**

```bash
git checkout feature/ci-security
git checkout -b test/ci-test-fail
cat > tests/test__deliberate_fail.py <<'EOF'
def test_deliberate_failure() -> None:
    assert False, "deliberate failure to validate ci pipeline"
EOF
git add tests/test__deliberate_fail.py
git commit -m "test: deliberate test failure for ci validation (DO NOT MERGE)"
git push -u origin test/ci-test-fail
gh pr create --title "test: ci-test-fail" --body "Validating test-unit gate." --draft
```

Expected: `ci / test-unit` job fails.
Cleanup:

```bash
gh pr close test/ci-test-fail --delete-branch
```

- [ ] **Step 7: Configure branch protection per README**

Follow the README section "Branch protection (one-time setup)" in the GitHub web UI for `main`. After this, the four deliberate-fail scenarios above could not have been merged.

- [ ] **Step 8: Approve and merge the main PR**

Once happy-path is green and Steps 3-6 confirmed each gate blocks correctly:

```bash
gh pr merge --rebase --delete-branch
```

Expected: branch merged to main, `feature/ci-security` deleted on remote.

- [ ] **Step 9: Verify nightly schedule appears**

Open Actions → "nightly-security" workflow. The schedule will not have fired yet, but `workflow_dispatch` should be available:

```bash
gh workflow run nightly-security.yml --ref main
```

Expected: run starts. Verify it succeeds against current main.

---

## Self-Review Notes

Spec coverage check:

- §5 (CI workflow) — Tasks 2, 3
- §6 (Security workflow) — Tasks 4, 5, 6, 7
- §6 Nightly + auto-Issue — Task 8
- §6 Allowlist files — Task 10
- §7 (Dependabot) — Task 9
- §8 (Branch protection) — Task 12 (README section) + Task 13 Step 7 (manual apply)
- §9 (Local commands in README) — Task 12
- §10 (Failure flow) — covered by Task 13 deliberate failures + README
- §11 (Observability) — no implementation needed; uses GitHub native UI
- §12 (Validation strategy) — Task 13
- §13 (Rollout) — Task 13 Steps 1-9 enact the rollout
- §14 (Open items) — intentionally deferred

No placeholders detected. No type/name drift across tasks.
