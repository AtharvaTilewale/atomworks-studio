#!/usr/bin/env bash
# Update release/rosettacommons: merge a reviewed ref and commit the built browser bundle and guide.
#
# The branch is what RosettaCommons/atomworks-studio mirrors (same commits, no rewriting), so
# `pip install` from the public repo works without Node, Rust or Sphinx. Run `make build` first.
#
# Usage: scripts/make_release_branch.sh [ref]   (default: origin/main)
set -euo pipefail
cd "$(dirname "$0")/.."

ref="${1:-origin/main}"
branch=release/rosettacommons
generated=(packages/api/annotation_studio/web packages/api/annotation_studio/guide)

test -z "$(git status --porcelain)" || { echo "Working tree is not clean." >&2; exit 1; }
for dir in "${generated[@]}"; do
  test -f "$dir/index.html" || { echo "$dir is missing; run 'make build' first." >&2; exit 1; }
done

git fetch --quiet origin
if git show-ref --verify --quiet "refs/heads/$branch"; then
  git switch --quiet "$branch"
  git merge --no-edit "$ref"
else
  git switch --quiet -c "$branch" "$ref"
fi

# Built output is ignored on development branches but tracked on the release branch.
sed -i.bak -e '\#^packages/api/annotation_studio/web/$#d' -e '\#^packages/api/annotation_studio/guide/$#d' .gitignore
rm -f .gitignore.bak
git rm -r --cached --quiet --ignore-unmatch "${generated[@]}"
git add .gitignore
git add -f "${generated[@]}"

if git diff --cached --quiet; then
  echo "$branch is already up to date with $ref."
else
  git commit --quiet -m "release: built browser bundle and guide for $(git rev-parse --short "$ref")"
fi
echo "Review, then: git push origin $branch && git push <rosettacommons-remote> $branch:main"
