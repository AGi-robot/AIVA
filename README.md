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

GitHub Actions checks pushes and pull requests targeting `dev`. It validates
`launch.sh` syntax, compiles AIVA's chat and signal Python packages, and runs
unit tests for conversation memory and signal encoding. Run the unit tests from
the repository root with:

```sh
PYTHONPATH=AIVA/src/central_neural_system/aiva_chat:AIVA/src/central_neural_system/cryptic_signal_core \
  python3 -m unittest discover -s AIVA/src/central_neural_system/aiva_chat/test -v
PYTHONPATH=AIVA/src/central_neural_system/aiva_chat:AIVA/src/central_neural_system/cryptic_signal_core \
  python3 -m unittest discover -s AIVA/src/central_neural_system/cryptic_signal_core/test -v
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

For a local Ollama server, set `AIVA_LLM_BASE_URL` and `AIVA_LLM_MODEL` if your
model or address differs from the defaults. `./launch.sh` preloads the configured
local Ollama model before starting AIVA chat, then keeps it loaded for 30 minutes
after its last request. Set `AIVA_LLM_KEEP_ALIVE=-1` to keep it loaded
indefinitely, or `AIVA_PRELOAD_LLM=0` to disable preloading. Preloading is skipped
for non-local, non-Ollama-compatible endpoints.

By default, the launcher creates an Ollama wrapper model from the installed
Ministral 3 8B Q4_K_XL model with `num_ctx` set to 25600. This lowers KV-cache
memory compared with the previous 32768-token context and can allow Ollama to
offload more layers to GPU; it does not guarantee full GPU offload, which also
depends on free VRAM. Change the context with `AIVA_LLM_NUM_CTX`. Change the
underlying installed model with `AIVA_LLM_SOURCE_MODEL`. Setting `AIVA_LLM_MODEL`
selects an existing model directly and skips wrapper creation.

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
proxy. The default LLM endpoint is `http://127.0.0.1:11434/v1`; the launcher
creates `aiva-ministral-3-8b-ctx25600` from
`hf.co/unsloth/Ministral-3-8B-Instruct-2512-GGUF:UD-Q4_K_XL`. For an authenticated endpoint, set `AIVA_LLM_API_KEY` in
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
