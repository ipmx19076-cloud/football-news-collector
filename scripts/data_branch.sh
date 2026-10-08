#!/usr/bin/env bash
# Persist the data/ directory on a dedicated `data` branch.
#
#   scripts/data_branch.sh restore   # copy the data branch contents into ./data (no-op if absent)
#   scripts/data_branch.sh publish   # replace the data branch with a single commit of ./data
#
# The branch holds ONE commit that is replaced on every publish (force-push). The SQLite file
# changes daily and git keeps every version of a binary in full, so normal history would grow
# the repository by the database size each day. Digests are files, so all of them remain
# browsable in the latest snapshot. Only run this from CI or when you mean to overwrite it.
set -euo pipefail

BRANCH="${DATA_BRANCH:-data}"
DATA_DIR="${DATA_DIR:-data}"
REMOTE="${DATA_REMOTE:-origin}"

restore() {
  mkdir -p "$DATA_DIR"
  if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
    git fetch --quiet --depth=1 "$REMOTE" "$BRANCH"
    git archive FETCH_HEAD | tar -x -C "$DATA_DIR"
    echo "Restored $(git ls-tree -r --name-only FETCH_HEAD | wc -l | tr -d ' ') files from '$BRANCH' into $DATA_DIR/"
  else
    echo "No '$BRANCH' branch on $REMOTE yet; starting with an empty database."
  fi
}

publish() {
  if [ ! -f "$DATA_DIR/football_news.db" ]; then
    echo "No $DATA_DIR/football_news.db to publish" >&2
    exit 1
  fi
  local index
  index="$(mktemp)"
  rm -f "$index"
  trap "rm -f '$index'" EXIT  # expand now: $index is local and gone when the trap fires
  # Build a tree from DATA_DIR in a private index, so the main checkout is untouched.
  GIT_INDEX_FILE="$index" git --work-tree="$DATA_DIR" add --force -- football_news.db \
    $( [ -d "$DATA_DIR/digests" ] && echo digests )
  local tree commit message
  tree="$(GIT_INDEX_FILE="$index" git write-tree)"
  if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
    git fetch --quiet --depth=1 "$REMOTE" "$BRANCH"
    if [ "$(git rev-parse 'FETCH_HEAD^{tree}')" = "$tree" ]; then
      echo "Data unchanged; nothing to publish."
      return 0
    fi
  fi
  message="Data snapshot $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  commit="$(
    GIT_AUTHOR_NAME="${GIT_AUTHOR_NAME:-github-actions[bot]}" \
    GIT_AUTHOR_EMAIL="${GIT_AUTHOR_EMAIL:-41898099+github-actions[bot]@users.noreply.github.com}" \
    GIT_COMMITTER_NAME="${GIT_COMMITTER_NAME:-github-actions[bot]}" \
    GIT_COMMITTER_EMAIL="${GIT_COMMITTER_EMAIL:-41898099+github-actions[bot]@users.noreply.github.com}" \
    git commit-tree "$tree" -m "$message"
  )"
  git push --quiet --force "$REMOTE" "$commit:refs/heads/$BRANCH"
  echo "Published $commit to '$BRANCH' ($message)"
}

case "${1:-}" in
  restore) restore ;;
  publish) publish ;;
  *) echo "usage: $0 restore|publish" >&2; exit 2 ;;
esac
