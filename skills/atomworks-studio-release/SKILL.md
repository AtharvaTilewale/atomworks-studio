---
name: atomworks-studio-release
description: >
  Promote reviewed AtomWorks Studio changes from baker-laboratory/atomworks-studio-dev main to the
  public RosettaCommons/atomworks-studio repository, and prepare or verify a PyPI release. Reach for
  this skill for "update the release branch", "mirror to RosettaCommons", "sync the public repo",
  "push the release", or "cut atomworks-studio vX.Y.Z".
---

# Promote Studio to RosettaCommons

`release/rosettacommons` is the branch that `RosettaCommons/atomworks-studio` mirrors, commit for
commit. It is `main` plus the committed browser bundle and guide, so `pip install` from the public
repository needs no Node, Rust or Sphinx. Read `docs/RELEASE.md` for the full policy.

## Promote

1. Work from the dev checkout (`baker-laboratory/atomworks-studio-dev`) with a clean tree. Check that
   `git remote -v` has `origin` (dev) and `rosettacommons` (`https://github.com/RosettaCommons/atomworks-studio.git`;
   add it if missing). Confirm the changes to promote are merged to `origin/main`; do not promote an open PR.
2. `git switch main && git pull --ff-only`.
3. `make build` (builds the web app, docs and wheels; expect Rust, Node, pnpm and uv).
4. `scripts/make_release_branch.sh origin/main`. It merges `origin/main` into `release/rosettacommons` and
   commits the fresh bundle and guide. It never rewrites history.
5. Verify in a scratch clone of the release branch, never in the working tree:
   `git clone --branch release/rosettacommons . "$TMP/rel" && cd "$TMP/rel" && uv build &&
   uv run scripts/check_distribution_assets.py dist && python3 scripts/verify_distribution.py`.
   For a stronger check, install the wheel into a clean Python 3.12 venv and run
   `scripts/check_installed_studio.py tests/fixtures/SAM_hbonds.cif` from outside the checkout.
6. Push: `git push origin release/rosettacommons`, then
   `git push rosettacommons release/rosettacommons:main`. The second push must be a fast-forward. If it is
   rejected, stop and report; never force-push without the user's explicit instruction, because the public
   history is meant to be append-only. Then switch back to `main`.
7. Confirm `gh api repos/RosettaCommons/atomworks-studio/branches/main --jq .commit.sha` equals
   `git rev-parse release/rosettacommons`, and check the `CI` and `VS Code extension` runs there.

Report the pushed SHA, what changed since the previous release commit, and the CI state. CI on the private
repository may fail to start for billing reasons; report that instead of retrying.

## Cut a PyPI release

Only when the user asks. The distribution is the single `atomworks-studio` package.

1. On a branch in dev, set the new version in `VERSION`, the root `pyproject.toml`, `package.json`,
   `packages/web/package.json`, `packages/vscode/package.json` and both Cargo manifests; run `uv lock`,
   `cargo metadata --locked` (or `cargo update -w --offline`) and `python3 scripts/verify_distribution.py`.
   Merge via PR with a merge commit, then promote as above.
2. Once the public repo's CI is green on that commit, tag it there: `git tag vX.Y.Z <sha>` and
   `git push rosettacommons vX.Y.Z`. Pushing the tag publishes to PyPI through trusted publishing
   (`pypi` environment, workflow `release.yaml`), so ask the user before pushing it.
3. Afterwards check `pip install atomworks-studio==X.Y.Z` in a fresh Python 3.12 venv and update the
   Hugging Face Space (`examples/space`) to that version.

## Do not

- Edit the release branch by hand or commit anything else onto it.
- Push tags, make the repository public, or touch PyPI or GitHub settings without being asked.
- Copy files between the repositories by hand; the branch mirror is the only path.
