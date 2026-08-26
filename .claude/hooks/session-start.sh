#!/bin/bash
# Make a re-provisioned container usable: put the working tree back on the branch this work
# actually lives on, then make sure the deps the tests and the build need are present.
#
# WHY THE FIRST HALF EXISTS. A Claude Code on the web session runs in an ephemeral container, and
# when that container is reclaimed and re-provisioned the repository is cloned FRESH at the
# revision the session was created from — not at whatever has been pushed since. A long session
# therefore comes back, without warning, at a tree that can be a hundred commits behind, with the
# pushed work intact on the remote and invisible locally. Nothing is lost, but everything after
# the rollback is built on the wrong base until someone notices.
#
# The remote branch is the source of truth, so this only ever moves the tree TOWARDS it, and only
# when doing so cannot lose anything:
#
#   * a dirty tree is left alone entirely — uncommitted work outranks any convenience here;
#   * a HEAD that is NOT an ancestor of the remote tip is left alone and reported, because that
#     means there are local commits the remote has never seen, and choosing between two histories
#     is a person's decision, not a hook's;
#   * only a genuine fast-forward is applied.
#
# So the worst case is that the hook declines and says why, which is the same position as having
# no hook at all.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}" || exit 0

say() { printf '[session-start] %s\n' "$1"; }

# --- 1. put the tree back on the pushed branch ------------------------------------------------
BRANCH_FILE=".claude/dev-branch"
if [ -f "$BRANCH_FILE" ]; then
  BRANCH="$(tr -d '[:space:]' < "$BRANCH_FILE")"
else
  # No pin committed: fall back to whatever the current branch tracks. An unborn or detached HEAD
  # yields nothing and the sync is skipped, which is correct — there is no branch to return to.
  BRANCH="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null \
            | sed 's|^origin/||')"
fi

sync_branch() {
  [ -n "${BRANCH:-}" ] || { say "no branch pinned or tracked; leaving the tree as it is"; return; }

  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    say "working tree is dirty — NOT touching it (uncommitted work outranks the sync)"
    return
  fi

  # Retried, because a cold container's first outbound call is the one that tends to fail, and a
  # failed fetch here would silently leave the stale tree in place.
  local delay=2 i
  for i in 1 2 3 4; do
    git fetch origin "$BRANCH" --quiet 2>/dev/null && break
    say "fetch of $BRANCH failed (attempt $i); retrying in ${delay}s"
    sleep "$delay"; delay=$((delay * 2))
  done

  local remote head
  remote="$(git rev-parse --verify --quiet "refs/remotes/origin/$BRANCH")" || {
    say "origin/$BRANCH does not exist; leaving the tree as it is"; return; }
  head="$(git rev-parse --verify --quiet HEAD)" || { say "no HEAD yet"; return; }

  if [ "$head" = "$remote" ]; then
    say "already at origin/$BRANCH (${remote:0:7})"
    return
  fi

  if git merge-base --is-ancestor "$head" "$remote"; then
    # A pure fast-forward: every commit reachable from HEAD is already on the remote, so there is
    # nothing here to lose. This is the rollback case.
    git checkout -q -B "$BRANCH" "$remote" \
      && say "fast-forwarded ${head:0:7} -> ${remote:0:7} on $BRANCH (container had a stale clone)" \
      || say "checkout of origin/$BRANCH failed; leaving the tree as it is"
    return
  fi

  say "HEAD (${head:0:7}) carries commits origin/$BRANCH (${remote:0:7}) does not have."
  say "NOT syncing — that is a history choice for a person to make, not this hook."
}
sync_branch

# --- 2. make the tests and the build runnable --------------------------------------------------
# Idempotent and cheap when the image already carries them, which it normally does — this is the
# safety net for an image that does not, so a session does not open on a repo whose suite cannot
# run. Never fatal: a session with no deps is still worth starting, and `set -e` is deliberately
# not in force above so one failure cannot abort the rest.
if ! python -c 'import fastapi, fitz, reportlab' >/dev/null 2>&1; then
  say "installing backend dependencies"
  (cd backend && python -m pip install --quiet -e '.[dev,pdf]' 2>/dev/null \
     || python -m pip install --quiet -e . 2>/dev/null) \
    || say "backend dependency install failed — the suite may not run"
fi

if [ ! -d frontend/node_modules ]; then
  say "installing frontend dependencies"
  (cd frontend && pnpm install --silent) || say "pnpm install failed — the build may not run"
fi

# Playwright's browser is baked into the image at this path; telling the suite where it is here
# means a fresh container does not try to download one (the network policy may refuse it).
if [ -d /opt/pw-browsers ] && [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo 'export PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers' >> "$CLAUDE_ENV_FILE"
  echo 'export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1' >> "$CLAUDE_ENV_FILE"
fi

say "ready"
exit 0
