#!/usr/bin/env bash
# Decide whether a queued Deploy Master run may still roll staging.
#
# Two pushes to master a few seconds apart start two Deploy Master runs. Even
# with a serialising concurrency group, GitHub does not guarantee the order in
# which queued runs acquire the group, so the older commit's run can execute
# after the newer one and leave staging on the older image. This guard makes the
# deploy newest-wins: a run whose commit is no longer current skips its build
# and deploy, and a run whose commit is still current proceeds.
#
# "Current" is not simply origin/<branch> HEAD: a docs-only commit on master
# does not trigger deploy-master, and must not strand staging on an older image.
# The commit is only superseded when a newer commit changed a path that would
# itself trigger a deploy.
#
# Writes ``superseded=true|false`` to $GITHUB_OUTPUT (the workflow step output).
# Human-readable notices go to stdout so they appear in the run log, not in the
# output file.
#
# Usage: staging_deploy_guard.sh [<sha> [<branch> [<remote>]]]
#   <sha>    commit being deployed (default: $GITHUB_SHA)
#   <branch> master branch to compare against (default: $GITHUB_REF_NAME)
#   <remote> git remote (default: origin)
set -euo pipefail

sha="${1:-${GITHUB_SHA:?GITHUB_SHA is not set}}"
branch="${2:-${GITHUB_REF_NAME:?GITHUB_REF_NAME is not set}}"
remote="${3:-origin}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is not set}"

# The paths that make deploy-master.yml run (see its `on.push.paths`). A newer
# commit touching only anything else does not supersede this deploy.
deploy_paths=(
  'packages'
  'docker'
  'pyproject.toml'
  '.github/workflows/deploy-*.yml'
)

git fetch --no-tags "$remote" "$branch"
head="$(git rev-parse "$remote/$branch")"

if [ "$sha" = "$head" ]; then
  echo "superseded=false" >>"$GITHUB_OUTPUT"
  echo "$sha is the current tip of $remote/$branch; deploying"
  exit 0
fi

if git diff --quiet "$sha" "$head" -- "${deploy_paths[@]}"; then
  echo "superseded=false" >>"$GITHUB_OUTPUT"
  echo "$remote/$branch advanced past $sha only in non-deploy paths; deploying"
else
  echo "superseded=true" >>"$GITHUB_OUTPUT"
  echo "::notice title=Staging deploy superseded::$sha is older than a newer deployable commit on $remote/$branch ($head); skipping build and deploy so the newest commit wins"
fi
