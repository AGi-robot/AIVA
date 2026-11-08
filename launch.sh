#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname -- "$SCRIPT_DIR")"
ROS_SETUP="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"
WORKSPACE_SETUP="$SCRIPT_DIR/install/setup.bash"
WORKSPACE_PACKAGES=(aiva_chat cryptic_signal_core scs)
WEBUI_URL="${AIVA_WEBUI_URL:-http://127.0.0.1:${AGI_WEB_TELEOPS_CONSOLE_PORT:-8853}}"
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

workspace_ready=1
for package in "${WORKSPACE_PACKAGES[@]}"; do
  if [[ ! -f "$SCRIPT_DIR/install/$package/share/ament_index/resource_index/packages/$package" ]]; then
    workspace_ready=0
    break
  fi
done

if [[ ! -f "$WORKSPACE_SETUP" || "$workspace_ready" -eq 0 ]]; then
  if ! command -v colcon >/dev/null 2>&1; then
    echo "AIVA workspace is not built and colcon is unavailable." >&2
    echo "Install colcon or build the workspace from $SCRIPT_DIR." >&2
    exit 1
  fi
  echo "AIVA workspace is incomplete; building from $SCRIPT_DIR..."
  AIVA_ROS_PYTHON="$(command -v python3)"
  (cd "$SCRIPT_DIR" && colcon build \
    --cmake-args "-DPython3_EXECUTABLE=$AIVA_ROS_PYTHON")
fi

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
mkdir -p "$LOG_DIR"

pids=()
start_service() {
  local name="$1"
  shift
  echo "Starting $name (log: $LOG_DIR/$name.log)"
  "$@" >"$LOG_DIR/$name.log" 2>&1 &
  pids+=("$!")
}

cleanup() {
  trap - EXIT INT TERM
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
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

wait -n "${pids[@]}"
status=$?
echo "A service exited (status $status). See logs in $LOG_DIR." >&2
exit "$status"
