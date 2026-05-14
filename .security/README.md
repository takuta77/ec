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
