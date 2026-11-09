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
LOG_DIR="${AIVA_LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/agi/aiva}"

if [[ ! -f "$ROS_SETUP" ]]; then
  echo "ROS setup not found: $ROS_SETUP" >&2
  echo "Set ROS_SETUP to your ROS 2 setup.bash path." >&2
  exit 1
fi
set +u
source "$ROS_SETUP"
set -u

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "AIVA Python venv not found: $VENV_DIR" >&2
  echo "Create it and install dependencies with:" >&2
  echo "  cd '$SCRIPT_DIR'" >&2
  echo "  /usr/bin/python3 -m venv --system-site-packages .venv" >&2
  echo "  .venv/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi
export VIRTUAL_ENV="$VENV_DIR"
export PATH="$VENV_DIR/bin:$PATH"
AIVA_ROS_PYTHON="$VENV_DIR/bin/python"
if ! "$AIVA_ROS_PYTHON" -c 'import rclpy, httpx' >/dev/null 2>&1; then
  echo "The AIVA venv cannot import ROS 2 Python modules or httpx." >&2
  echo "Recreate it with --system-site-packages, then install requirements.txt." >&2
  exit 1
fi

if ! command -v colcon >/dev/null 2>&1; then
  echo "colcon is unavailable; cannot build the AIVA workspace." >&2
  exit 1
fi
echo "Building AIVA workspace from $SCRIPT_DIR..."
(cd "$SCRIPT_DIR" && colcon build \
  --cmake-args "-DPython3_EXECUTABLE=$AIVA_ROS_PYTHON")

if [[ ! -f "$WORKSPACE_SETUP" ]]; then
  echo "Workspace build completed without creating $WORKSPACE_SETUP." >&2
  exit 1
fi

for package in "${WORKSPACE_PACKAGES[@]}"; do
  if [[ ! -f "$SCRIPT_DIR/install/$package/share/ament_index/resource_index/packages/$package" ]]; then
    echo "Workspace build did not install required package: $package" >&2
    exit 1
  fi
done

set +u
source "$WORKSPACE_SETUP"
set -u
if ! ros2 pkg prefix rosbridge_server >/dev/null 2>&1; then
  echo "ROS package rosbridge_server is missing; install it with:" >&2
  echo "  sudo apt install ros-jazzy-rosbridge-server" >&2
  exit 1
fi
mkdir -p "$LOG_DIR"

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
  echo "Find the process using that port with: fuser -n tcp $WEBUI_PORT" >&2
  echo "Stop only the stale AIVA WebUI process, then run ./launch.sh again." >&2
  exit 1
fi

pids=()
service_names=()
service_has_session=()
start_service() {
  local name="$1"
  shift
  echo "Starting $name (log: $LOG_DIR/$name.log)"
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
  for index in "${!pids[@]}"; do
    pid="${pids[$index]}"
    # ROS commands can leave Python grandchildren behind if only the ros2
    # wrapper is stopped. Each service gets a session when setsid exists.
    if [[ "${service_has_session[$index]}" == "1" ]]; then
      kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    else
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "${pids[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

start_service rosbridge ros2 launch rosbridge_server rosbridge_websocket_launch.xml address:=127.0.0.1
if [[ "$START_CHAT" == "1" ]]; then
  start_service aiva-chat ros2 run aiva_chat aiva_chat
fi
start_service webui ros2 run telepathic_management telepathic_console

echo "Opening AIVA WebUI at $WEBUI_URL"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$WEBUI_URL" >/dev/null 2>&1 &
fi
echo "Services are running. Press Ctrl+C to stop them. Set AIVA_START_CHAT=0 to skip chat."

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
  echo "Service '$failed_service' exited (status $status)." >&2
else
  echo "A service exited (status $status)." >&2
fi
echo "Check service logs in $LOG_DIR." >&2
exit "$status"
