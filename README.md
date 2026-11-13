# AIVA presence

## Quick start

Open `AGi/AIVA.code-workspace` in VS Code to keep the Explorer focused on AIVA;
AuRoRA remains in the shared Git repository but is outside this workspace view.
To start the local WebUI, rosbridge, and chat node together, run:

```sh
cd ~/AGi/AIVA
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
./launch.sh
```

The venv includes the system ROS 2 Python packages through
`--system-site-packages`; this lets the chat node use ROS Jazzy's `rclpy` while
keeping pip-installed AIVA dependencies inside `AIVA/.venv`. `launch.sh` builds
with that venv's Python and starts the AIVA services with it. If you keep the
venv elsewhere, set `AIVA_VENV=/path/to/venv` when launching.

The launcher opens `http://127.0.0.1:8853` and writes service logs under
`~/.local/state/agi/aiva/`. Press Ctrl+C to stop the services. Set
`AIVA_START_CHAT=0` to start only rosbridge and the WebUI. The script expects
ROS 2 Jazzy and a built AIVA workspace; set `ROS_SETUP` if ROS is installed at
a different path.

## Central neural system packages

### Automated checks

GitHub Actions checks pushes and pull requests targeting `dev`. Its fast job
validates `launch.sh`, tests whether it starts chat only when enabled, exercises
the ROS chat adapter with message doubles, and tests memory and signal encoding.
A second job builds the AIVA ROS packages on Jazzy and sends messages through a
real ROS graph. Run the non-ROS tests from the repository root with:

```sh
python3 -m pip install pytest httpx
PYTHONPATH=AIVA/src/central_neural_system/aiva_chat:AIVA/src/central_neural_system/cryptic_signal_core \
  python3 -m pytest -q AIVA/src/central_neural_system/aiva_chat/test AIVA/src/central_neural_system/cryptic_signal_core/test
```

Packages grouped under `src/central_neural_system/` belong to AIVA's central neural system. For
example, `src/central_neural_system/cryptic_signal_core` is AIVA's cryptic ROS package, containing
its message definitions and encoder class.
Colcon discovers the package beneath the grouping directory; build it with:

```sh
cd ~/AGi/AIVA
source /opt/ros/jazzy/setup.bash
colcon build --packages-select cryptic_signal_core
```

The shared WebUI backend uses AIVA's `CrypticSignalCore` when the AIVA package is
installed. `AGI_TELEPATHIC_SCHEME` selects a registered scheme (default
`ternary_utf8_v1`); its non-secret name travels in the signal so the receiving
TIC can select the matching decoder. Other deployments can retain their own
encoding implementation.

## AIVA chat and simple memory

Chat runs in the separate `aiva_chat` ROS package, not in SCS. It decodes
`/telepathic/stimulus`, sends recent turns to an OpenAI-compatible chat API,
and publishes `start` / `delta` / `done` response events on
`/tms/text_response`, which the WebUI already displays. Recent turns persist
in SQLite at `~/.agi/profile/grace_memory.sqlite3` by default. The path follows
the `agi_home` parameter and can be overridden with `AIVA_MEMORY_PATH`. The
node sends the latest 8 exchanges to the LLM as active context; older turns
remain stored but are not semantically recalled yet.

Grace's language models run on a single llama.cpp router: one `llama-server`
serves chat and vision (Ministral 3 14B Instruct 2512, with its `mmproj` vision
projector) plus embeddings (voyage-4-nano) on one port, routing by the request's
model name (`models/llama-router.ini`). The launcher starts that router through
the `agi-llm-router` systemd user unit when it exists, keeps the models loaded
indefinitely (`AIVA_LLM_KEEP_ALIVE` changes this), and skips it when `AIVA_LLM_BASE_URL`
points elsewhere. For a different endpoint, set `AIVA_LLM_BASE_URL` and
`AIVA_LLM_MODEL`. Preloading is skipped for non-local, non-Ollama-compatible
endpoints; set `AIVA_PRELOAD_LLM=0` to disable preloading entirely.

Build the AIVA packages from this workspace:

```sh
cd ~/AGi/AIVA
source /opt/ros/jazzy/setup.bash
colcon build --packages-select cryptic_signal_core aiva_chat telepathic_management
source install/setup.bash
```

To run services manually, start Ollama (or another compatible server), then open
separate terminals, source `AIVA/install/setup.bash` in each, and run:

```sh
ros2 launch rosbridge_server rosbridge_websocket_launch.xml address:=127.0.0.1
ros2 run aiva_chat aiva_chat
ros2 run telepathic_management telepathic_console
```

The chat node reads `profile/user.md` from the AGi data directory. The ROS
parameter `agi_home` defaults to `~/.agi` and can be set with `AGI_HOME` or a
ROS override such as `ros2 run aiva_chat aiva_chat --ros-args -p agi_home:=/path/to/.agi`.
The profile is added to Grace's system context when the file exists.

Rosbridge must listen at `127.0.0.1:9090`; the console backend publishes the
input signal there, and the browser receives responses through its WebSocket
proxy. The default LLM endpoint is `http://127.0.0.1:8080/v1`, where the
llama.cpp router serves `ministral-3-14b-instruct-2512` (chat and vision) and
`voyage-4-nano` (embeddings) from `models/llama-router.ini`. For an
authenticated endpoint, set `AIVA_LLM_API_KEY` in
the environment; never put it in the ROS message or source files.

The AIVA ROS workspace can run a small heartbeat process alongside its future
services. It lets the Web Teleops Console on AuRoRA distinguish the PC companion from the
physical robot. When both AIVA's heartbeat and AuRoRA's rosbridge connection
are online, the Web Teleops Console shows Grace's VRM in the center with AuRoRA's original
pink robot beside her. When only AuRoRA is online, the robot returns to center
at regular size; if only AIVA is online, Grace appears by herself.

Install/build the AIVA workspace, configure the AuRoRA Web Teleops Console's Tailscale Serve
URL, and run this as a user service on the AIVA PC:

```sh
cd ~/AGi/AIVA
source /opt/ros/jazzy/setup.bash
colcon build --packages-select scs
source install/setup.bash
export AGI_WEB_TELEOPS_CONSOLE_PRESENCE_URL="https://your-aurora-machine.your-tailnet.ts.net/api/presence/aiva"
ros2 run scs aiva-presence
```

Keep AIVA and AuRoRA on the same tailnet and use its ACL/grant rules to restrict
access to your devices. No second Web Teleops Console account is required. The heartbeat expires
12 seconds after the last successful request by default; the sender posts every
4 seconds. `AGI_PRESENCE_INTERVAL` changes the sender interval, and
`AGI_AIVA_PRESENCE_TIMEOUT` changes the Web Teleops Console expiration period.

To keep presence in sync with the PC power state, install the user service after
building the package:

```sh
mkdir -p ~/.config/agi ~/.config/systemd/user
printf '%s\n' 'AGI_WEB_TELEOPS_CONSOLE_PRESENCE_URL=https://your-aurora-machine.your-tailnet.ts.net/api/presence/aiva' > ~/.config/agi/aiva-presence.env
cp ~/AGi/AIVA/systemd/agi-aiva-presence.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agi-aiva-presence.service
```

The service starts with your AIVA user session and stops when the PC shuts down.
Replace the URL with the HTTPS host shown by `tailscale serve status` on AuRoRA.

Canvas requests use the same recent conversation memory as normal chat (the configured
`memory_turns` window). Your prompt, Grace's written reply, and her proposed drawing plan are
stored persistently in SQLite. The plan is recorded as unconfirmed playback; browser artwork
is still saved locally and is not a visual memory or proof that the drawing completed.

Canvas generation now uses at most three streamed passes, limited to 16, 8, then 4 actions,
31 points per new contour, and 3,072 output tokens each. Complete actions from a
truncated tool response are retained; continuation receives their assigned IDs
and a compact summary so it can finish without replaying the same actions. The
UI marks an unfinished result as a draft and keeps received actions on errors.
`AIVA_DRAW_MAX_SECONDS` defaults to 240; this request budget is checked between
passes and streamed events. The existing `AIVA_DRAW_TIMEOUT_SECONDS` still bounds
idle reads, capped by the remaining request budget when each pass starts. A slow
or stalled read can therefore end after the request budget. Neither limit changes
the configured model context size. Interrupted plans are recorded as unconfirmed
summaries, and raw plans in older history are shortened for both chat and Canvas
without deleting the stored history.
