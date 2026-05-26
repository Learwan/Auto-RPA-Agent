#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HOST="${AUTO_AGENT_HOST:-0.0.0.0}"
PORT="${AUTO_AGENT_PORT:-8000}"
RELOAD="${AUTO_AGENT_RELOAD:-1}"
PORT_CONFLICT_ACTION="${AUTO_AGENT_PORT_CONFLICT:-ask}"
FALLBACK_PORT="${AUTO_AGENT_FALLBACK_PORT:-}"
UV_CACHE_DIR_OVERRIDE="${AUTO_AGENT_UV_CACHE_DIR:-${TMPDIR:-$ROOT_DIR/.tmp}/auto-agent-uv-cache}"
EXTRA_ARGS=("$@")

usage() {
  cat <<'EOF'
Auto Agent Workflow quick start script

Usage:
  ./scripts/start.sh [extra uvicorn args]

Optional environment variables:
  AUTO_AGENT_HOST=0.0.0.0   Bind host (default: 0.0.0.0)
  AUTO_AGENT_PORT=8000      Bind port (default: 8000)
  AUTO_AGENT_RELOAD=1       Enable reload: 1/0 (default: 1)
  AUTO_AGENT_PORT_CONFLICT=ask
                            Port conflict strategy: ask|kill|change|abort
  AUTO_AGENT_FALLBACK_PORT=8001
                            Port to use when AUTO_AGENT_PORT_CONFLICT=change
  AUTO_AGENT_UV_CACHE_DIR=...
                            Writable uv cache dir override for sandboxed envs

Examples:
  AUTO_AGENT_PORT=9000 ./scripts/start.sh
  AUTO_AGENT_RELOAD=0 ./scripts/start.sh
  AUTO_AGENT_PORT_CONFLICT=kill ./scripts/start.sh
  AUTO_AGENT_PORT_CONFLICT=change AUTO_AGENT_FALLBACK_PORT=8001 ./scripts/start.sh
  AUTO_AGENT_UV_CACHE_DIR="$TMPDIR/auto-agent-uv-cache" ./scripts/start.sh
  ./scripts/start.sh --log-level debug
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

print_endpoints() {
  cat <<EOF
Starting Auto Agent Workflow

Host:  $HOST
Port:  $PORT
Reload: $([[ "$RELOAD" == "0" ]] && printf 'off' || printf 'on')

Endpoints:
  Web Console:   http://localhost:${PORT}/
  Flow Editor:   http://localhost:${PORT}/flow-editor.html
  Health Check:  http://localhost:${PORT}/api/health
  OpenAPI Docs:  http://localhost:${PORT}/docs
EOF
}

normalize_action() {
  case "${1:-}" in
    k|K|kill|KILL)
      printf 'kill'
      ;;
    c|C|change|CHANGE)
      printf 'change'
      ;;
    a|A|abort|ABORT|'')
      printf 'abort'
      ;;
    ask|ASK)
      printf 'ask'
      ;;
    *)
      printf '%s' "${1:-}"
      ;;
  esac
}

is_valid_port() {
  local candidate="${1:-}"
  [[ "$candidate" =~ ^[0-9]+$ ]] || return 1
  (( candidate >= 1 && candidate <= 65535 ))
}

find_next_available_port() {
  local candidate
  if is_valid_port "$FALLBACK_PORT"; then
    candidate="$FALLBACK_PORT"
    if [[ -z "$(lsof -nP -iTCP:"$candidate" -sTCP:LISTEN 2>/dev/null | sed -n '2p')" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  fi

  candidate=$((PORT + 1))
  while (( candidate <= PORT + 50 )); do
    if [[ -z "$(lsof -nP -iTCP:"$candidate" -sTCP:LISTEN 2>/dev/null | sed -n '2p')" ]]; then
      printf '%s' "$candidate"
      return 0
    fi
    candidate=$((candidate + 1))
  done

  return 1
}

build_app_args() {
  app_args=(src.api.app:app --host "$HOST" --port "$PORT")
  if [[ "$RELOAD" != "0" ]]; then
    app_args+=(--reload)
  fi
  if [[ "${#EXTRA_ARGS[@]}" -gt 0 ]]; then
    app_args+=("${EXTRA_ARGS[@]}")
  fi
}

port_listener_info() {
  if ! command -v lsof >/dev/null 2>&1; then
    return 1
  fi
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | sed -n '2p'
}

prompt_for_new_port() {
  local suggestion response
  suggestion="$(find_next_available_port || true)"
  suggestion="${suggestion:-$((PORT + 1))}"

  if [[ "$(normalize_action "$PORT_CONFLICT_ACTION")" == "change" || ! -t 0 ]]; then
    if ! is_valid_port "$suggestion"; then
      echo "Cannot choose a fallback port automatically. Set AUTO_AGENT_FALLBACK_PORT to a valid port." >&2
      return 1
    fi
    PORT="$suggestion"
    return 0
  fi

  while true; do
    printf 'Enter a new port [%s]: ' "$suggestion" >&2
    read -r response || return 1
    response="${response:-$suggestion}"
    if ! is_valid_port "$response"; then
      echo "Invalid port: $response" >&2
      continue
    fi
    PORT="$response"
    return 0
  done
}

wait_for_port_release() {
  local attempts=0
  while (( attempts < 20 )); do
    if [[ -z "$(port_listener_info || true)" ]]; then
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 0.25
  done
  return 1
}

kill_listener_process() {
  local pid="${1:-}"
  if [[ -z "$pid" ]]; then
    echo "Cannot determine which process to stop for port ${PORT}." >&2
    return 1
  fi

  echo "Stopping PID ${pid}..." >&2
  if ! kill "$pid" 2>/dev/null; then
    echo "Failed to stop PID ${pid}." >&2
    return 1
  fi
  if wait_for_port_release; then
    return 0
  fi

  if [[ -t 0 ]]; then
    local force_answer=""
    printf 'Process still holds port %s. Force kill with SIGKILL? [y/N]: ' "$PORT" >&2
    read -r force_answer || true
    if [[ "$force_answer" == "y" || "$force_answer" == "Y" ]]; then
      if ! kill -9 "$pid" 2>/dev/null; then
        echo "Failed to SIGKILL PID ${pid}." >&2
        return 1
      fi
      if wait_for_port_release; then
        return 0
      fi
    fi
  fi

  echo "Port ${PORT} is still in use after attempting to stop PID ${pid}." >&2
  return 1
}

ensure_port_available() {
  local listener pid command_line action choice
  while true; do
    listener="$(port_listener_info || true)"
    if [[ -z "$listener" ]]; then
      return 0
    fi

    pid="$(awk '{print $2}' <<<"$listener")"
    command_line="$(ps -ww -p "$pid" -o command= 2>/dev/null | tr '\n' ' ' | tr -s '[:space:]' ' ' | sed 's/^ //; s/ $//' || true)"

    echo "Port ${PORT} is already in use." >&2
    if [[ -n "$pid" ]]; then
      echo "PID: ${pid}" >&2
    fi
    if [[ -n "$command_line" ]]; then
      echo "Process: ${command_line}" >&2
    else
      echo "Listener: ${listener}" >&2
    fi
    echo >&2

    action="$(normalize_action "$PORT_CONFLICT_ACTION")"
    if [[ "$action" == "ask" ]]; then
      if [[ ! -t 0 ]]; then
        action="abort"
      else
        echo "Choose one of the following:" >&2
        echo "  [k] Kill the process and continue" >&2
        echo "  [c] Change to another port and continue" >&2
        echo "  [a] Abort startup" >&2
        printf 'Select [k/c/a] (default: a): ' >&2
        read -r choice || choice="a"
        action="$(normalize_action "$choice")"
      fi
    fi

    case "$action" in
      kill)
        if kill_listener_process "$pid"; then
          PORT_CONFLICT_ACTION="ask"
          continue
        fi
        if [[ ! -t 0 ]]; then
          return 1
        fi
        PORT_CONFLICT_ACTION="ask"
        ;;
      change)
        if ! prompt_for_new_port; then
          return 1
        fi
        build_app_args
        PORT_CONFLICT_ACTION="ask"
        continue
        ;;
      abort)
        echo "Startup aborted." >&2
        return 1
        ;;
      *)
        echo "Unknown AUTO_AGENT_PORT_CONFLICT value: ${PORT_CONFLICT_ACTION}" >&2
        return 1
        ;;
    esac
  done
}

launcher_name=""
launcher=()
app_args=()
build_app_args

if command -v uv >/dev/null 2>&1; then
  launcher_name="uv"
  launcher=(uv run uvicorn)
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  launcher_name=".venv"
  launcher=("$ROOT_DIR/.venv/bin/python" -m uvicorn)
elif command -v python3 >/dev/null 2>&1; then
  launcher_name="python3"
  launcher=(python3 -m uvicorn)
else
  echo "No compatible launcher found. Install uv or create a .venv with project dependencies first." >&2
  echo "See README.md -> Installation for setup instructions." >&2
  exit 1
fi

ensure_port_available
build_app_args

print_endpoints
echo
echo "Launcher: $launcher_name"
echo "Working directory: $ROOT_DIR"
if [[ "$launcher_name" == "uv" ]]; then
  mkdir -p "$UV_CACHE_DIR_OVERRIDE"
  echo "UV cache: $UV_CACHE_DIR_OVERRIDE"
fi
echo

if [[ "$launcher_name" == "uv" ]]; then
  exec env UV_CACHE_DIR="$UV_CACHE_DIR_OVERRIDE" "${launcher[@]}" "${app_args[@]}"
fi

exec "${launcher[@]}" "${app_args[@]}"
