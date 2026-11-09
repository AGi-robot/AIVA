#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname -- "$SCRIPT_DIR")"
ROS_SETUP="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"
VENV_DIR="${AIVA_VENV:-$SCRIPT_DIR/.venv}"
WORKSPACE_SETUP="$SCRIPT_DIR/install/setup.bash"
WORKSPACE_PACKAGES=(aiva_chat cryptic_signal_core scs telepathic_management)
WEBUI_HOST="${AGI_WEB_TELEOPS_CONSOLE_HOST:-127.0.0.1}"
WEBUI_PORT="${AGI_WEB_TELEOPS_CONSOLE_PORT:-8853}"
WEBUI_URL="${AIVA_WEBUI_URL:-http://${WEBUI_HOST}:${WEBUI_PORT}}"
START_CHAT="${AIVA_START_CHAT:-1}"
LLM_BASE_URL="${AIVA_LLM_BASE_URL:-http://127.0.0.1:11434/v1}"
LLM_MODEL="${AIVA_LLM_MODEL:-hf.co/unsloth/Ministral-3-14B-Instruct-2512-GGUF:UD-Q4_K_XL}"
LLM_KEEP_ALIVE="${AIVA_LLM_KEEP_ALIVE:-30m}"
PRELOAD_LLM="${AIVA_PRELOAD_LLM:-1}"
LOG_DIR="${AIVA_LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/agi/aiva}"
export AIVA_LLM_BASE_URL="$LLM_BASE_URL"
export AIVA_LLM_MODEL="$LLM_MODEL"

if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != "dumb" ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_CYAN=$'\033[36m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
  C_MAGENTA=$'\033[35m'
else
  C_RESET=''
  C_BOLD=''
  C_DIM=''
  C_CYAN=''
  C_GREEN=''
  C_YELLOW=''
  C_RED=''
  C_MAGENTA=''
fi

info() { printf '%s\n' "${C_CYAN}•${C_RESET} $*"; }
success() { printf '%s\n' "${C_GREEN}✓${C_RESET} $*"; }
warn() { printf '%s\n' "${C_YELLOW}!${C_RESET} $*" >&2; }
error() { printf '%s\n' "${C_RED}✗${C_RESET} $*" >&2; }
stage() { printf '\n%s%s%s  %s\n' "$C_MAGENTA" "$1" "$C_RESET" "$2"; }

run_spinner() {
  local label="$1" logfile="$2" status=0 pid index elapsed started=$SECONDS timer
  shift 2
  if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != "dumb" ]]; then
    local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    "$@" >"$logfile" 2>&1 &
    pid=$!
    index=0
    while kill -0 "$pid" 2>/dev/null; do
      elapsed=$((SECONDS - started))
      printf -v timer '%02d:%02d' "$((elapsed / 60))" "$((elapsed % 60))"
      printf '\r\033[2K%s%s%s  %s %s' "$C_CYAN" "${frames[$((index % ${#frames[@]}))]}" "$C_RESET" "$label" "${C_DIM}[$timer]${C_RESET}"
      index=$((index + 1))
      sleep 0.1
    done
    wait "$pid" || status=$?
    printf '\r\033[2K'
  else
    info "$label (details: $logfile)"
    "$@" >"$logfile" 2>&1 || status=$?
  fi

  if [[ "$status" == "0" ]]; then
    elapsed=$((SECONDS - started))
    success "$label (${elapsed}s)"
    return 0
  fi
  error "$label failed (exit $status). Details: $logfile"
  tail -n 20 "$logfile" >&2 || true
  return "$status"
}

printf '\n%s╭──────────────────────────────────────╮\n' "$C_MAGENTA"
printf '│  %s✦ AIVA%s  %sGrace companion startup%s       │\n' "$C_BOLD" "$C_RESET" "$C_DIM" "$C_RESET"
printf '│  %sROS 2%s  ·  %sOllama%s  ·  %sWebUI%s            │\n' "$C_CYAN" "$C_RESET" "$C_GREEN" "$C_RESET" "$C_MAGENTA" "$C_RESET"
printf '╰──────────────────────────────────────╯%s\n\n' "$C_RESET"

if [[ ! -f "$ROS_SETUP" ]]; then
  error "ROS setup not found: $ROS_SETUP"
  info "Set ROS_SETUP to your ROS 2 setup.bash path."
  exit 1
fi
set +u
source "$ROS_SETUP"
set -u

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  error "AIVA Python venv not found: $VENV_DIR"
  info "Create it and install dependencies with:"
  printf '  cd %q\n  /usr/bin/python3 -m venv --system-site-packages .venv\n  .venv/bin/python -m pip install -r requirements.txt\n' "$SCRIPT_DIR"
  exit 1
fi
export VIRTUAL_ENV="$VENV_DIR"
export PATH="$VENV_DIR/bin:$PATH"
AIVA_ROS_PYTHON="$VENV_DIR/bin/python"
if ! "$AIVA_ROS_PYTHON" -c 'import rclpy, httpx' >/dev/null 2>&1; then
  error "The AIVA venv cannot import ROS 2 Python modules or httpx."
  info "Recreate it with --system-site-packages, then install requirements.txt."
  exit 1
fi

if ! command -v colcon >/dev/null 2>&1; then
  error "colcon is unavailable; cannot build the AIVA workspace."
  exit 1
fi
mkdir -p "$LOG_DIR"
stage "01 / 04" "Build workspace"
build_workspace() {
  (cd "$SCRIPT_DIR" && colcon build --cmake-args "-DPython3_EXECUTABLE=$AIVA_ROS_PYTHON")
}
if ! run_spinner "Building ROS 2 packages" "$LOG_DIR/build.log" build_workspace; then
  exit 1
fi

if [[ ! -f "$WORKSPACE_SETUP" ]]; then
  error "Workspace build did not create $WORKSPACE_SETUP."
  exit 1
fi

for package in "${WORKSPACE_PACKAGES[@]}"; do
  if [[ ! -f "$SCRIPT_DIR/install/$package/share/ament_index/resource_index/packages/$package" ]]; then
    error "Workspace build did not install required package: $package"
    exit 1
  fi
done

set +u
source "$WORKSPACE_SETUP"
set -u
if ! ros2 pkg prefix rosbridge_server >/dev/null 2>&1; then
  error "ROS package rosbridge_server is missing."
  info "Install it with: sudo apt install ros-jazzy-rosbridge-server"
  exit 1
fi

# Catch an orphaned WebUI from an older launch before bringing up rosbridge/chat.
if ! "$AIVA_ROS_PYTHON" - "$WEBUI_HOST" "$WEBUI_PORT" <<'PY'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
try:
    with socket.socket() as probe:
        probe.bind((host, port))
except OSError as error:
    print(f"WebUI cannot bind to {host}:{port}: {error}", file=sys.stderr)
    raise SystemExit(1)
PY
then
  info "Find the listener with: sudo ss -ltnp 'sport = :$WEBUI_PORT'"
  info "Check its command with: ps -fp <PID> (replace <PID> with the ss process ID)."
  error "Stop only the stale AIVA WebUI process, then run ./launch.sh again."
  exit 1
fi

# AIVA's WebUI publishes into this local rosbridge. Starting a second ROS
# graph with the same node names can make messages appear sent but never get
# to the chat node, so fail before creating more orphaned services.
if ! "$AIVA_ROS_PYTHON" - <<'PY'
import socket
import sys

try:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 9090))
except OSError as error:
    print(f"AIVA rosbridge cannot bind to 127.0.0.1:9090: {error}", file=sys.stderr)
    raise SystemExit(1)
PY
then
  error "A ROS bridge is already using port 9090."
  info "Find it with: sudo ss -ltnp 'sport = :9090'"
  info "Stop the stale AIVA launch before starting another one."
  exit 1
fi

if [[ "$START_CHAT" == "1" && "$PRELOAD_LLM" != "0" ]]; then
  stage "02 / 04" "Warm up Grace's language model"
  info "Model: $LLM_MODEL  ${C_DIM}· keep alive: $LLM_KEEP_ALIVE${C_RESET}"
  preload_llm() {
    "$AIVA_ROS_PYTHON" - "$LLM_BASE_URL" "$LLM_MODEL" "$LLM_KEEP_ALIVE" <<'PY'
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

base_url, model, keep_alive = sys.argv[1:]
parsed = urlsplit(base_url)
if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.port != 11434:
    print("Skipping preload: the configured LLM endpoint is not local Ollama on port 11434.")
    raise SystemExit(0)

origin = f"{parsed.scheme}://{parsed.netloc}"
request = Request(
    f"{origin}/api/generate",
    data=json.dumps({"model": model, "keep_alive": keep_alive, "stream": False}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
deadline = time.monotonic() + 30
while True:
    try:
        with urlopen(request, timeout=300) as response:
            result = json.load(response)
        if result.get("error"):
            print(f"Ollama preload failed: {result['error']}", file=sys.stderr)
            raise SystemExit(1)
        print(f"LLM ready: {result.get('model', model)}")
        break
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        print(f"Ollama preload failed (HTTP {error.code}): {detail}", file=sys.stderr)
        raise SystemExit(1)
    except (URLError, TimeoutError, OSError) as error:
        if time.monotonic() >= deadline:
            print(f"Could not preload Ollama model from {origin}: {error}", file=sys.stderr)
            raise SystemExit(1)
        time.sleep(0.5)
PY
  }
  if ! run_spinner "Loading model into Ollama" "$LOG_DIR/llm-preload.log" preload_llm; then
    error "Set AIVA_PRELOAD_LLM=0 to skip model preloading."
    exit 1
  fi
elif [[ "$START_CHAT" == "1" ]]; then
  stage "02 / 04" "Warm up Grace's language model"
  warn "Preloading disabled (AIVA_PRELOAD_LLM=0)."
fi

pids=()
service_names=()
service_has_session=()
start_service() {
  local name="$1"
  shift
  info "Starting $name  ${C_DIM}· log: $LOG_DIR/$name.log${C_RESET}"
  if command -v setsid >/dev/null 2>&1; then
    setsid -- "$@" >"$LOG_DIR/$name.log" 2>&1 &
    service_has_session+=(1)
  else
    "$@" >"$LOG_DIR/$name.log" 2>&1 &
    service_has_session+=(0)
  fi
  pids+=("$!")
  service_names+=("$name")
}

cleanup() {
  trap - EXIT INT TERM
  local index pid attempt
  # Stop every service group first so ROS wrappers and their Python children
  # all receive the signal, then give them a short chance to release sockets.
  for index in "${!pids[@]}"; do
    pid="${pids[$index]}"
    if [[ "${service_has_session[$index]}" == "1" ]]; then
      kill -TERM -- "-$pid" 2>/dev/null || true
    else
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done

  for attempt in {1..20}; do
    local any_alive=0
    for index in "${!pids[@]}"; do
      pid="${pids[$index]}"
      if [[ "${service_has_session[$index]}" == "1" ]]; then
        kill -0 -- "-$pid" 2>/dev/null && any_alive=1
      else
        kill -0 "$pid" 2>/dev/null && any_alive=1
      fi
    done
    [[ "$any_alive" == "0" ]] && break
    sleep 0.1
  done

  # A service that ignores TERM must not leave the WebUI port occupied.
  for index in "${!pids[@]}"; do
    pid="${pids[$index]}"
    if [[ "${service_has_session[$index]}" == "1" ]]; then
      kill -KILL -- "-$pid" 2>/dev/null || true
    else
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  for pid in "${pids[@]}"; do
    wait "$pid" 2>/dev/null || true
  done

  # Confirm the listener has released the configured WebUI address.
  if ! "$AIVA_ROS_PYTHON" - "$WEBUI_HOST" "$WEBUI_PORT" <<'PY'
import socket
import sys

try:
    with socket.socket() as probe:
        probe.bind((sys.argv[1], int(sys.argv[2])))
except OSError:
    raise SystemExit(1)
PY
  then
    warn "$WEBUI_HOST:$WEBUI_PORT is still occupied after stopping AIVA services."
    info "Check the remaining listener with: sudo ss -ltnp 'sport = :$WEBUI_PORT'"
  else
    success "AIVA services stopped; port $WEBUI_PORT is free."
  fi
}
handle_signal() {
  local signal="$1"
  cleanup
  if [[ "$signal" == "INT" ]]; then
    exit 130
  fi
  exit 143
}
trap cleanup EXIT
trap 'handle_signal INT' INT
trap 'handle_signal TERM' TERM

stage "03 / 04" "Start AIVA services"
start_service rosbridge ros2 launch rosbridge_server rosbridge_websocket_launch.xml address:=127.0.0.1
if [[ "$START_CHAT" == "1" ]]; then
  start_service aiva-chat ros2 run aiva_chat aiva_chat
fi
start_service webui ros2 run telepathic_management telepathic_console

stage "04 / 04" "Connect the WebUI"
wait_for_webui() {
  "$AIVA_ROS_PYTHON" - "$WEBUI_URL" <<'PY'
import sys
import time
import urllib.request

deadline = time.monotonic() + 30
last_error = None
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(sys.argv[1], timeout=1) as response:
            if response.status < 500:
                raise SystemExit(0)
    except Exception as error:
        last_error = error
    time.sleep(0.5)
print(f"WebUI did not become ready: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
}
if ! run_spinner "Waiting for the WebUI at $WEBUI_URL" "$LOG_DIR/webui-ready.log" wait_for_webui; then
  error "Check the WebUI log at $LOG_DIR/webui.log."
  exit 1
fi

if command -v xdg-open >/dev/null 2>&1; then
  success "WebUI is ready; opening browser."
  xdg-open "$WEBUI_URL" >/dev/null 2>&1 &
else
  success "WebUI is ready at $WEBUI_URL (xdg-open is unavailable)."
fi
printf '\n%s%sAIVA is ready%s\n' "$C_BOLD" "$C_GREEN" "$C_RESET"
info "Open $WEBUI_URL"
info "Press Ctrl+C to stop services. Set AIVA_START_CHAT=0 to skip chat."

if wait -n "${pids[@]}"; then
  status=0
else
  status=$?
fi

failed_service=""
for index in "${!pids[@]}"; do
  if ! kill -0 "${pids[$index]}" 2>/dev/null; then
    failed_service="${service_names[$index]}"
    break
  fi
done
if [[ -n "$failed_service" ]]; then
  error "Service '$failed_service' exited (status $status)."
else
  error "A service exited (status $status)."
fi
info "Check service logs in $LOG_DIR."
exit "$status"
