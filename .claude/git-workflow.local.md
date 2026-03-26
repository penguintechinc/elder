# Git Workflow (Elder-Specific Addendums)

## Versioning Policy

**Only bump patch/minor/major when the current semver already has a published release tag.**

| Situation | Command | Example |
|-----------|---------|---------|
| Interim commit, `v3.1.4` not yet released | `./scripts/version/update-version.sh` | `v3.1.4.old → v3.1.4.new` |
| `v3.1.4` is already tagged/released | `./scripts/version/update-version.sh patch` | `v3.1.4 → v3.1.5.new` |

**Why:** Bumping semver without tagging a release creates version gaps (`v3.1.1` released, then code ships as `v3.1.4` with `v3.1.2` and `v3.1.3` never publicly released). Keep the patch number stable until a release tag exists for it, then bump for the next cycle.

**Checking before bumping:**
```bash
git tag --list 'v3.1.*' | sort -V   # see what's already released
```
If the current `.version` semver (e.g. `v3.1.4`) is already in that list → safe to bump patch.
If not → build-only bump only.
