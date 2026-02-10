<!--
  BEFORE SUBMITTING: Read every section. All required sections must be filled out.
  PRs that skip required sections will be closed without review.
-->

## PR Title Format

<!--
  Use one of these prefixes:
  fix: <description>     — Bug fix
  feat: <description>    — New feature or capability
  perf: <description>    — Performance improvement
  docs: <description>    — Documentation only
  refactor: <description> — Code restructuring with no behavior change
  test: <description>    — Adding or updating tests
  chore: <description>   — Maintenance (deps, CI, configs)
-->

## Scope Check (REQUIRED)

<!--
  This project enforces narrow, atomic PRs. Answer ALL of these:
-->

- [ ] This PR addresses **exactly one** concern (one bug, one feature, one refactor)
- [ ] This PR does NOT bundle unrelated changes (no "while I was here" fixes)
- [ ] Every changed file is directly related to the stated purpose
- [ ] If this PR touches more than 3 files, I have justified why below

**Scope justification (if >3 files changed):**

<!--
  If you are changing more than 3 files, explain why they all belong in this PR.
  Example: "Renaming a function required updating all call sites"
-->

## Description (REQUIRED)

<!-- What does this PR do? Be specific. -->

## Why

<!-- Why is this change needed? Link to issue if applicable. -->

---

## Testing — General (REQUIRED)

<!--
  Testing is MANDATORY. PRs without evidence of testing will be rejected.
  Check every test you performed. If a test is not applicable, explain why.
-->

### Database Testing

- [ ] Tested with **SQLite** database
- [ ] Tested with **PostgreSQL** database
- [ ] If only one DB tested, explain why: <!-- e.g., "change is DB-agnostic, only affects CLI output" -->

### Functional Testing

- [ ] Tested **preview mode** (dry run)
- [ ] Verified preview counts are accurate
- [ ] Tested **full execution** (actual deletions performed)
- [ ] Verified deleted records are actually removed from DB
- [ ] Tested with **no orphaned data** (script handles clean state gracefully)
- [ ] Tested with **large dataset** if performance-related change

## Testing — Vector Database (IF APPLICABLE)

<!--
  Only required if your change touches vector DB cleanup logic.
  Test against whichever vector DB your change affects.
-->

- [ ] N/A — This PR does not touch vector DB logic
- [ ] Tested orphaned collection detection
- [ ] Tested orphaned collection deletion
- [ ] Tested internal metadata / record cleanup (if applicable)
- [ ] Verified active collections are NOT deleted

---

## Checklist

- [ ] I have read and followed the scope requirements above
- [ ] I have tested my changes as documented above
