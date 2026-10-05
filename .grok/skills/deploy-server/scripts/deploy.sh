#!/usr/bin/env bash
# Deploy the worktree to the live site. Does not replace server-local files.
#
# Protected files (never uploaded; merge into the server copy instead):
#   app/config.py       SECRET_KEY and ADMIN_PASSWORD have defaults. Do not
#                       restore the required-env check.
#   app/auth.py         An empty password still reaches compare_digest.
#   app/grok_client.py  Also looks for the Grok binary at
#                       /home/exedev/.grok/downloads/grok-linux-x86_64.
#   docker-compose.yml  Keeps HOME=/home/exedev, the two ~/.grok volume mounts,
#                       and the password defaults. Add a new env key by editing
#                       the server file; do not copy this repo's compose over it.
#   README.md           Has the exe.dev share notes for this VM.
#
# Never uploaded: data/, .env
set -euo pipefail

HOST="${DEPLOY_HOST:-charles.exe.xyz}"
APP="${DEPLOY_APP:-/home/exedev/astro-photo}"
ROOT="$(cd "$(dirname "$0")" && git rev-parse --show-toplevel)"
dry=""
if [[ "${1:-}" == "--dry-run" ]]; then
  dry=1
elif [[ -n "${1:-}" ]]; then
  echo "usage: deploy.sh [--dry-run]" >&2
  exit 2
fi

SSH=(ssh -o BatchMode=yes)

cd "$ROOT"

if git rev-parse --verify origin/main >/dev/null 2>&1; then
  changed="$(git diff --name-only origin/main...HEAD -- \
    app/config.py app/auth.py app/grok_client.py docker-compose.yml README.md || true)"
  if [[ -n "$changed" ]]; then
    echo "Protected files changed since origin/main (not uploaded):"
    printf '  %s\n' $changed
    echo "Merge any new settings into the server copies. Do not replace those files."
  fi
fi

show_changes() {
  local log
  log="$(mktemp)"
  if ! rsync "$@" >"$log"; then
    cat "$log" >&2
    rm -f "$log"
    exit 1
  fi
  grep -E '^[<>*]' "$log" || true
  rm -f "$log"
}

show_changes -azc --delete --itemize-changes ${dry:+-n} \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'config.py' \
  --exclude 'auth.py' \
  --exclude 'grok_client.py' \
  -e "${SSH[*]}" \
  "$ROOT/app/" "$HOST:$APP/app/"

show_changes -azc --itemize-changes ${dry:+-n} \
  -e "${SSH[*]}" \
  "$ROOT/Dockerfile" "$ROOT/requirements.txt" \
  "$HOST:$APP/"

if [[ -n "$dry" ]]; then
  echo "Dry run only. Nothing was rebuilt."
  exit 0
fi

"${SSH[@]}" "$HOST" "cd '$APP' && docker compose up -d --build"

healthy=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if "${SSH[@]}" "$HOST" "curl -sf http://127.0.0.1:8000/health"; then
    echo
    healthy=1
    break
  fi
  sleep 1
done

if [[ "$healthy" -ne 1 ]]; then
  echo "Server health check failed." >&2
  "${SSH[@]}" "$HOST" "docker logs astro-photo --tail 40" >&2 || true
  exit 1
fi

curl -sf "https://$HOST/health"
echo
