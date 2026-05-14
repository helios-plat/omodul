# 3O Stack — Release Policy

**Applies to**: `oprim`, `oskill`, `omodul`  
**Last updated**: 2026-05-14

---

## 1. Branch Model

### 1.1 `main` is the only release branch

- `main` always reflects the latest stable, tagged release.
- No development happens directly on `main`.
- `main` is protected: no direct pushes, no force pushes, no bypassing CI.

### 1.2 Feature branches are temporary

Feature branches exist only until they are merged. Naming convention:

```
feat/v{semver}-from-{source}    # migration from an upstream project
feat/v{semver}-phase{N}         # in-platform development phase
```

**After a feature branch is merged into `main`, it must be deleted immediately** — both locally and on origin. A feature branch that outlives its merge becomes a "zombie mainline": the container bind-mount will continue to reference it, silently running different code than `main`.

### 1.3 Intermediate phase branches

When a version is built across multiple phases:
```
feat/v1.4.0-phase1 → feat/v1.5.0-phase2 → ... → feat/v1.7.0-phase4
```
Delete all of them after the final merge. They are cumulative and the final branch subsumes all earlier ones.

---

## 2. Merge Requirements

### 2.1 Dependency order

Always merge in this order. The container breaks if you merge out of order.

```
oprim → oskill → omodul
```

### 2.2 Fast-forward only

Use `--ff-only` for all merges into `main`. A merge commit on `main` means the history is not linear and indicates something went wrong with the branch strategy.

```bash
git checkout main
git merge --ff-only feat/v{semver}-phase{N}
```

If `--ff-only` is rejected, the feat branch diverged from `main`. Stop and investigate — do not create a merge commit without understanding why.

### 2.3 Pre-merge checklist

Before merging any branch into `main`:

- [ ] All tests pass on the feature branch (including coverage gates)
- [ ] The tag to be created is already decided and available
- [ ] Downstream repos (if any) have reviewed the interface changes
- [ ] No uncommitted modifications on the feature branch

---

## 3. Tagging

- Every merge to `main` must be immediately followed by an annotated tag.
- Tag format: `v{major}.{minor}.{patch}`
- Tag the commit at the tip of `main` after the merge.
- Push tags explicitly: `git push origin --tags`

```bash
git tag -a v{X}.{Y}.{Z} -m "{brief description of what this version adds}"
git push origin main --tags
```

**Do not delete and re-create tags that have already been pushed to origin** unless absolutely necessary — re-tagging requires coordinating with all consumers.

---

## 4. Container Bind-Mount Discipline

The helivex containers bind-mount the platform repos directly:

```
~/projects/platform/oprim  →  /opt/stack/oprim  (container sees working tree)
~/projects/platform/oskill →  /opt/stack/oskill
~/projects/platform/omodul →  /opt/stack/omodul
```

This means:
- **`git checkout` is a live operation** — switching any repo's working branch immediately changes what the running container imports.
- Running `git checkout main` on a repo while the container is live will break imports if `main` is behind the expected version.
- After any branch operation that changes the working tree, verify the container: `docker exec <container> python -c "import oprim, oskill, omodul; print(oprim.__version__, oskill.__version__, omodul.__version__)"`

### Expected stable state

All three repos should have their working tree pointing to `main` (post-merge) or the current active development feat branch. They must never be in a **mixed state** (e.g., oprim on main while oskill is on feat).

---

## 5. What NOT to Do

| Action | Problem |
|---|---|
| Leave feat branch after merge | Zombie mainline; container will silently use stale code if someone checks it out |
| Merge oskill before oprim | ImportError in container immediately |
| `git checkout main` mid-session on one repo only | Breaks import chain for the other repos still on feat |
| Tag without pushing | Tag exists locally; other consumers don't see it |
| Use merge commits on `main` | Breaks `--ff-only` assumption; obscures linear history |
| Direct push to `main` | Bypasses CI; use PR or at minimum confirm tests passed locally |

---

## 6. Incident Record

| Date | Incident | Root Cause | Fix |
|---|---|---|---|
| 2026-05-14 | `ImportError: cannot import name 'canonical_json' from 'oprim'` in helivex container | Diagnostic `git checkout main` loop switched oprim to v1.2.0 while oskill was on v2.0.0 | Restored oprim to `feat/v1.7.0-phase4`; then merged all to main |
| 2026-05-14 | omodul tests blocked: `ModuleNotFoundError: No module named 'oskill.regime'` | omodul feat merged to main before oskill feat; correct merge order not followed | Merged oprim → oskill → omodul in correct order |
