#!/usr/bin/env bash
# One command: install everything, load the real review data, analyse it, build the dashboard,
# start both servers, open the browser and print a short tour.
#
#   ./scripts/setup.sh                # install + run
#   ./scripts/setup.sh --skip-install # already installed: just start everything
#   ./scripts/setup.sh --no-browser
#   API_PORT=8000 WEB_PORT=3000 ./scripts/setup.sh
#
# Stop everything with Ctrl+C.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"
SKIP_INSTALL=0
OPEN_BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --skip-install) SKIP_INSTALL=1 ;;
    --no-browser) OPEN_BROWSER=0 ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

VENV="$ROOT/.venv"
if [ -x "$VENV/Scripts/python.exe" ]; then BIN="$VENV/Scripts"; else BIN="$VENV/bin"; fi
LOGS="$ROOT/data/logs"
export REVIEWHUB_LOG_LEVEL=WARNING REVIEWHUB_LOG_FORMAT=text   # quiet, readable setup output
step() { printf '\n\033[36m[%s] %s\033[0m\n' "$1" "$2"; }
ok()   { printf '    \033[32mOK\033[0m  %s\n' "$1"; }
fail() { printf '\n    \033[31mERROR\033[0m  %s\n' "$1"; exit 1; }
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

echo "Azzurro Review Intelligence - setup and launch"
echo "Repository: $ROOT"

step "1/6" "Checking prerequisites"
PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || fail "Python 3.11+ is required: https://www.python.org/downloads/"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || fail "Python 3.11+ is required (found $("$PY" --version 2>&1))"
ok "$("$PY" --version 2>&1)"
command -v node >/dev/null || fail "Node.js 20+ is required: https://nodejs.org/"
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || fail "Node.js 20+ is required (found $(node --version))"
ok "Node.js $(node --version)"
for p in "$API_PORT" "$WEB_PORT"; do
  if port_busy "$p"; then fail "Port $p is already in use. Free it or set API_PORT / WEB_PORT."; fi
done
ok "Ports $API_PORT and $WEB_PORT are free"

if [ "$SKIP_INSTALL" -eq 0 ]; then
  step "2/6" "Installing the backend (Python virtual environment)"
  [ -d "$VENV" ] || "$PY" -m venv "$VENV"
  if [ -x "$VENV/Scripts/python.exe" ]; then BIN="$VENV/Scripts"; else BIN="$VENV/bin"; fi
  "$BIN/python" -m pip install --quiet --upgrade pip
  "$BIN/python" -m pip install --quiet -e "$ROOT/backend"
  ok "Backend installed"

  step "3/6" "Preparing the database and the 973 real reviews"
  "$BIN/reviewhub" db upgrade
  "$BIN/reviewhub" sample load
  "$BIN/reviewhub" ai analyze --provider builtin
  ok "Database ready: data/reviewhub.db"

  step "4/6" "Installing and building the dashboard (Next.js)"
  [ -f "$ROOT/frontend/.env.local" ] || echo "BACKEND_URL=http://127.0.0.1:$API_PORT" > "$ROOT/frontend/.env.local"
  (cd "$ROOT/frontend" && npm ci --no-audit --no-fund --loglevel=error && npm run build)
  ok "Dashboard built"
else
  step "2-4/6" "Skipping installation (--skip-install)"
  [ -x "$BIN/reviewhub" ] || fail "Not installed yet. Run without --skip-install first."
fi

step "5/6" "Starting the API and the dashboard"
mkdir -p "$LOGS"
"$BIN/reviewhub" serve --port "$API_PORT" >"$LOGS/api.log" 2>&1 &
API_PID=$!
(cd "$ROOT/frontend" && BACKEND_URL="http://127.0.0.1:$API_PORT" exec npm start -- -p "$WEB_PORT") >"$LOGS/web.log" 2>&1 &
WEB_PID=$!
cleanup() { kill "$WEB_PID" "$API_PID" 2>/dev/null || true; pkill -P "$WEB_PID" 2>/dev/null || true; echo "Stopped."; }
trap cleanup EXIT INT TERM

wait_for() {
  for _ in $(seq 1 60); do
    curl -fsS -o /dev/null "$1" 2>/dev/null && return 0
    sleep 1
  done
  fail "$2 did not start in 60 s. Logs: $LOGS"
}
wait_for "http://127.0.0.1:$API_PORT/api/v1/health" "The API"
ok "API       http://127.0.0.1:$API_PORT   (interactive docs: /docs)"
wait_for "http://127.0.0.1:$WEB_PORT/" "The dashboard"
ok "Dashboard http://localhost:$WEB_PORT"

step "6/6" "Quick tour (full version: docs/TOUR.md)"
cat <<'TOUR'
    1. Executive Overview    /            this week's rating vs last week, trends, top issues to act on
    2. Properties            /properties  the four hotels side by side; click one for its own page
    3. Review Intelligence   /insights    the 8 topics, '% of negative reviews mentioning X', guest quotes
    4. Review Explorer       /reviews     every review: search, filter by property/date/rating/topic
    5. Data & Collection     /data        where data comes from, BLOCKED attempts, import a CSV/JSON
TOUR
if [ "$OPEN_BROWSER" -eq 1 ]; then
  URL="http://localhost:$WEB_PORT"
  (command -v xdg-open >/dev/null && xdg-open "$URL") || (command -v open >/dev/null && open "$URL") || true
fi
printf '\n\033[33mRunning. Press Ctrl+C to stop both servers.\033[0m\n'
wait "$API_PID" "$WEB_PID"
