# Shell — Grace runtime

## Quick start

Open the AGi workspace in VS Code. The ROS workspace lives in the separate
`AIVA/shell` repository.
To start the local WebUI, rosbridge, and chat node together, run:

```sh
cd ~/AGi/AIVA
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
cd shell
../launch.sh
```

The venv includes the system ROS 2 Python packages through
`--system-site-packages`; this lets the chat node use ROS Jazzy's `rclpy` while
keeping pip-installed AIVA dependencies inside `AIVA/.venv`. `requirements.txt`
and the venv live at the AIVA root. `launch.sh`
(lives at `AIVA/launch.sh`, one level up) builds
with that venv's Python and starts the AIVA services with it. If you keep the
venv elsewhere, set `AIVA_VENV=/path/to/venv` when launching.


The package links resolve inside this AGi checkout:

```text
AIVA/shell/src/cryptic_signal_core   -> ../../cypher
AIVA/shell/src/telepathic_management -> ../../telepathy
```

The standalone text conversation engine is checked out beside Shell at
`AIVA/interactive` and installed by `AIVA/requirements.txt`. Clone it before
installing the workspace requirements:

```sh
cd ~/AGi/AIVA
git clone https://github.com/AGi-robot/interactive.git interactive
```

Install ROS dependencies before the first build:

```sh
source /opt/ros/jazzy/setup.bash
rosdep install --from-paths src --ignore-src -r -y
```

After moving an existing workspace, move its generated `build/` and `install/`
directories aside before rebuilding; CMake and installed scripts contain absolute
paths. Recreate a moved `.venv` and reinstall requirements if its activation or
command scripts still point to the old directory. Memory is installed editable
from its checkout into this venv by `requirements.txt`.

## ROS packages and processes

The `shell` colcon workspace contains ROS packages, but not every package
executable is a ROS node:

| Component | What it runs | ROS node? |
|---|---|---:|
| `aiva_chat` | ROS adapter for Telepathy input/output, Interaction turns, Canvas orchestration, and the `GraceMemory` adapter | Yes |
| `agi_logging` | Collects `/rosout`, writes JSONL logs, and publishes `/agi/logs` | Yes |
| `rosbridge_server` | ROS WebSocket bridge started by `launch.sh` | Yes, from the ROS distribution |
| `telepathic_management` | Telepathy WebUI server, started as `telepathic_console`; it sends/receives ROS traffic through rosbridge | No; standalone web process in a ROS package |
| Interactive (`agi-interactive`) | Reusable text conversation engine imported by `aiva_chat` from the venv | No |
| Memory (`agi-memory`) | Reusable Python/SQLite library imported by `aiva_chat` from the venv | No |
| `aiva_cryptic_signal_core` and `cryptic_signal_core` (Cypher) | Encoding library and ROS message definitions | No |
| `scs` / `aiva-presence` | HTTP heartbeat process for the Web Teleops Console | No; standalone process packaged for `ros2 run` |
| Canvas, Imaging, llama.cpp, MossInfer | Python capabilities, model service, or painting sidecar used by the chat flow | No |

Memory currently runs in the `aiva_chat` process and stores data in SQLite.
Keep it as a library unless several ROS processes need to share one memory
service. Likewise, Canvas and Imaging do not need ROS wrappers unless other ROS
processes need to invoke them through ROS topics, services, or actions.

`launch.sh` starts rosbridge, the log collector, the Telepathy web process, and
the chat node (unless `AIVA_START_CHAT=0`). The llama.cpp router may be managed
by systemd or launched as a child process; it is not a ROS node.

The launcher opens `http://127.0.0.1:8853` and writes service logs under
`~/.agi/log/` (`$AGI_HOME/log`), overridable with `AIVA_LOG_DIR`. Press Ctrl+C
to stop the services. Set `AIVA_START_CHAT=0` to start rosbridge, the log collector,
and the WebUI without chat or the local model router. The script expects ROS 2
Jazzy and builds the workspace on each launch; set `ROS_SETUP` if ROS is installed
at a different path.

## Central neural system packages

### Automated checks

The parent AGi repository has GitHub Actions jobs for the shared Telepathy and
Cypher packages. Shell also includes local launcher lifecycle, chat adapter,
memory, drawing, and ROS graph checks. Install the local editable Memory
package and test dependencies with AIVA's root venv, then run non-ROS checks from
`~/AGi`:

```sh
cd ~/AGi/AIVA
.venv/bin/python -m pip install pytest -r requirements.txt
cd ~/AGi
PYTHONPATH=capability/canvas/src:capability/imaging/src:AIVA/shell/src/central_neural_system/aiva_chat:AIVA/shell/src/central_neural_system/aiva_cryptic_signal_core:AIVA/shell/src/cryptic_signal_core \
  python3 -m pytest -q AIVA/shell/src/central_neural_system/aiva_chat/test AIVA/shell/src/central_neural_system/aiva_cryptic_signal_core/test
```

Packages grouped under `src/central_neural_system/` belong to AIVA's central neural system. For
example, `src/central_neural_system/aiva_cryptic_signal_core` is AIVA's encoding scheme registry. Shared ROS messages and the base text codec come from [AGi-Robot/cypher](https://github.com/AGi-robot/cypher), linked at `src/cryptic_signal_core`. Clone Cypher beside Shell with `git clone https://github.com/AGi-robot/cypher.git ~/AGi/AIVA/cypher`. The `src/cryptic_signal_core` link targets `../../cypher`. Colcon discovers the packages beneath the source directories; build the chat and console with:

```sh
cd ~/AGi/AIVA/shell
source /opt/ros/jazzy/setup.bash
colcon build --packages-up-to aiva_chat telepathic_management agi_logging
```

The shared WebUI backend uses AIVA's `CrypticSignalCore` when the AIVA package is
installed. `AGI_TELEPATHIC_SCHEME` selects a registered scheme (default
`ternary_utf8_v1`); its non-secret name travels in the signal so the receiving
TIC can select the matching decoder. Other deployments can retain their own
encoding implementation.

## Benchmarking the recent-turn memory window

The scorecard provides an AIVA-specific BFCL V4 memory track that imports this
package's `GraceMemory` class, replays the BFCL prerequisite conversations
through an isolated SQLite database, and measures recall with the configured
recent-turn window. Results are separate from BFCL's official memory-tool
categories, which use simulated key-value, vector, or recursive-summary
backends. The runner never opens AIVA's live `~/.agi` memory database.

See the [scorecard BFCL V4 guide](https://github.com/AGi-robot/scorecard/blob/dev/benchmarks/bfcl/README.md)
for setup and run instructions.

## AIVA chat and simple memory

The `aiva_chat` ROS package receives and decodes
`/telepathic/stimulus`, then delegates text turns to
[Interactive](https://github.com/AGi-robot/interactive). Interactive sends
recent turns to an OpenAI-compatible chat API and emits `start` / `delta` /
`done` response events; Shell publishes them on `/tms/text_response`, which the
WebUI already displays. Recent turns persist
in SQLite at `~/.agi/profile/grace_memory.sqlite3` by default. The path follows
the `agi_home` parameter and can be overridden with `AIVA_MEMORY_PATH`. The
node sends the latest 8 exchanges to the LLM as active context; older turns
remain stored but are not semantically recalled yet.

The reusable SQLite conversation store is provided by
[Memory](https://github.com/AGi-robot/memory) (`agi-memory`), installed as an
editable Python package from `./mind/memory` through AIVA's root `requirements.txt`. AIVA's
`GraceMemory` adapter extends that store with
Grace-specific Canvas study records and trims legacy drawing payloads from
chat context. AIVA still configures the database path and decides what context
to send to the model; Memory does not run the chat loop or perform semantic
recall. Existing SQLite history opens in place. After updating AIVA, install
the dependency in its runtime environment with
`../.venv/bin/python -m pip install -r ../requirements.txt` from `AIVA/shell`.

Grace's language models run on a single llama.cpp router: one `llama-server`
serves chat (`ministral-3-8b-uncensored`), embeddings (`voyage-4-nano`), and
an on-demand vision/tool model (`swift15-qwen3.8-27b`, with its `mmproj`),
routing by the request's model name (`models/llama-router.ini`). The launcher starts that router through
the `agi-llm-router` systemd user unit when it exists, keeps the models loaded
according to `load-on-startup` and the router cache policy, and skips it when `AIVA_LLM_BASE_URL`
points elsewhere. For a different endpoint, set `AIVA_LLM_BASE_URL` and
`AIVA_LLM_MODEL`.
The default chat model has no vision projector. For image input in chat, set
`AIVA_LLM_MODEL=swift15-qwen3.8-27b` before launching. Canvas drawing uses the
vision-capable Swift model by default through `AIVA_DRAW_MODEL`; override that
variable to select a different drawing model.

Memory is checked out at `~/AGi/AIVA/mind/memory` and installed into the Shell
virtual environment with the other Python requirements. It is a Python library,
not a ROS package; colcon does not build it.

The shared Telepathy console is maintained in [AGi-robot/telepathy](https://github.com/AGi-robot/telepathy). Clone it beside this repository so the existing `src/telepathic_management` link resolves:

```sh
cd ~/AGi/AIVA
git clone https://github.com/AGi-robot/telepathy.git telepathy
```

Build the AIVA packages from this workspace:

```sh
cd ~/AGi/AIVA/shell
source /opt/ros/jazzy/setup.bash
colcon build --packages-up-to telepathic_management aiva_chat agi_logging
source install/setup.bash
```

To run services manually, start the llama.cpp router (or another compatible server), then open
separate terminals in `~/AGi/AIVA/shell`. In each terminal, prepare the same
Python environment that the launcher exposes (colcon entry points may use the
system interpreter):

```sh
source /opt/ros/jazzy/setup.bash
source install/setup.bash
source ../.venv/bin/activate
export PYTHONPATH="$(../.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')${PYTHONPATH:+:$PYTHONPATH}"
export AIVA_LLM_MODEL="${AIVA_LLM_MODEL:-ministral-3-8b-uncensored}"
```

Then start one service per terminal:

```sh
ros2 launch rosbridge_server rosbridge_websocket_launch.xml address:=127.0.0.1
ros2 run aiva_chat aiva_chat
ros2 run agi_logging agi_log_collector
ros2 run telepathic_management telepathic_console
```

The chat node reads `profile/user.md` from the AGi data directory. The ROS
parameter `agi_home` defaults to `~/.agi` and can be set with `AGI_HOME` or a
ROS override such as `ros2 run aiva_chat aiva_chat --ros-args -p agi_home:=/path/to/.agi`.
The profile is added to Grace's system context when the file exists.

Rosbridge must listen at `127.0.0.1:9090`; the console backend publishes the
input signal there, and the browser receives responses through its WebSocket
proxy. The default LLM endpoint is `http://127.0.0.1:8080/v1`, where the
llama.cpp router serves `ministral-3-8b-uncensored` (chat),
`swift15-qwen3.8-27b` (vision/tools on demand), and `voyage-4-nano` (embeddings) from `models/llama-router.ini`. For an
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
cd ~/AGi/AIVA/shell
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
cp ~/AGi/AIVA/shell/systemd/agi-aiva-presence.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agi-aiva-presence.service
```

The service starts with your AIVA user session and stops when the PC shuts down.
Replace the URL with the HTTPS host shown by `tailscale serve status` on AuRoRA.

Canvas requests use the same recent conversation memory as normal chat (the configured
`memory_turns` window). Your prompt, Grace's written reply, and her proposed drawing plan are
stored persistently in SQLite. The plan is recorded as unconfirmed playback; browser artwork
is still saved locally and is not a visual memory or proof that the drawing completed.

Canvas Studio is part of the host WebUI. It sends drawing requests to the host's WebSocket
API, and the host relays them through the existing AIVA ROS topics so requests and results
remain visible to ROS monitoring tools. Local WebUI use needs no API key. Set
`AGI_CANVAS_API_KEY` on the host if trusted external clients such as AuRoRA should call the
WebSocket API directly; expose it through HTTPS/WSS and pass the key as the initial auth frame.

Canvas generation uses the measured Swift recipe by default: `AIVA_DRAW_MODEL`
selects `swift15-qwen3.8-27b`, each request uses temperature 0, medium reasoning,
6,144 output tokens, and a JSON-schema response grammar that requires action kind
and explicit stroke/fill colors. The node streams JSON content, validates and
repairs complete actions independently, and enforces the cubic curve point
grammar (4, 7, 10, …, 31). It uses at most four passes, limited to 16, 10, 8,
then 6 actions. Complete actions from a truncated response are retained;
continuation receives their assigned IDs and a compact summary so it can finish
without replaying the same actions. The UI marks an unfinished result as a draft
and keeps received actions on errors. `AIVA_DRAW_MAX_TOKENS` and
`AIVA_DRAW_REASONING_EFFORT` can override the generation budget and reasoning
setting. Do not disable thinking while using the JSON-schema grammar.
`AIVA_DRAW_MAX_SECONDS` defaults to 240; this request budget is checked between
passes and streamed events. The existing `AIVA_DRAW_TIMEOUT_SECONDS` still bounds
idle reads, capped by the remaining request budget when each pass starts. A slow
or stalled read can therefore end after the request budget. Neither limit changes
the configured model context size. Interrupted plans are recorded as unconfirmed
summaries, and raw plans in older history are shortened for both chat and Canvas
without deleting the stored history.

## Raster painting with MOSS-Image (MossInfer)

Grace can paint real raster artwork through the community MossInfer engine (v2.2.1).
When she emits a canvas `image` action, the chat node spawns a `moss-server` sidecar,
creates an engine once, renders a PNG, and publishes it as a separate
`canvas_image` event (never through the model or `canvas_actions`). GPU weights are
released after each painting; the engine and its embedding cache remain available
for the next painting. Later vector actions wait for the bitmap, preserving layer order.

The engine is found on `PATH`, or defaults to the following paths under your home directory.
`launch.sh` sets the weight root to this checkout’s `models/mossimage/`:

* `~/mossinfer/mossinfer-2.2.1-linux-cu13-x86_64/bin/moss-server`
* `~/AGi/models/mossimage/` (`sft/`, `comfy/`, `distill-8step/`)

All paths, the GPU device, and the VRAM budget are overridable with environment
variables (the sidecar runs on `CUDA_VISIBLE_DEVICES=$MOSS_PAINT_DEVICE`):

| Variable | Default | Meaning |
|---|---|---|
| `MOSS_PAINT_SERVER_BIN` | `<engine>/bin/moss-server` | moss-server binary |
| `MOSS_PAINT_WEIGHTS_ROOT` | `<checkout>/models/mossimage` when launched, otherwise `~/AGi/models/mossimage` | root for all weight defaults |
| `MOSS_PAINT_MODEL_DIR` | `<weights>/sft` | SFT bundle (`model_index.json` + tokenizer) |
| `MOSS_PAINT_DIT_FILE` | `<weights>/comfy/diffusion_models/moss_image_sft_int8_convrot.safetensors` | DiT weights |
| `MOSS_PAINT_TE_FILE` | `<weights>/comfy/text_encoders/qwen3.5_9b_int8_convrot.safetensors` | text encoder |
| `MOSS_PAINT_VAE_FILE` | `<weights>/comfy/vae/flux2-vae.safetensors` | VAE |
| `MOSS_PAINT_DISTILL_DIR` | `<weights>/distill-8step` | 8-step DMD distill bundle (lora + time_embedding) |
| `MOSS_PAINT_DEVICE` | `1` | CUDA device (physical GPU index visible inside `CUDA_VISIBLE_DEVICES`) |
| `MOSS_PAINT_VRAM_MIB` | `4096` | VRAM budget passed to `load` |
| `MOSS_PAINT_TIMEOUT_SECONDS` | `300` | per-painting startup and generation timeout |
| `MOSS_PAINT_RELEASE` | `1` | `0` disables `release` after each painting |

Painting is enabled only when `MossClient.available()` is true — the binary, the SFT
bundle, the DiT, the text encoder, the VAE, and the distill bundle's `lora.safetensors`
and `time_embedding.safetensors` must all be present. On a missing engine or weights,
Grace falls back to vector drawing and logs a warning. A failed painting ends the request
with `canvas_error`, preserving layers already sent; it is never reported as a finished
painting. Timeouts and shutdown terminate and reap the sidecar so GPU jobs cannot linger.

Generation uses 256–704 pixels per side (multiples of 16, at most 0.5 MP), placed at the
requested canvas coordinates. There are at most two paintings per request. GPU generation
has its own timeout and does not consume the vector planning budget. The 4 GiB budget is
an upper bound for this engine, not a reservation: the selected GPU still needs enough
free memory alongside llama.cpp. The launcher does not unload the chat model automatically.

The browser waits for bitmap decoding before snapshots and PNG export. Requests send
image descriptions and positions plus a downscaled screenshot, rather than the original
PNG bytes. Editable artwork imports accept up to 32 MiB; browser storage may fill sooner,
so use **Save artwork** if local autosave reports a storage error.

Local tests exercise the sidecar protocol with a small fake process, without installing CUDA or
model weights, and runs the canvas bitmap lifecycle checks with Node.js. Actual image
quality and GPU memory use still require testing on the target GPU.

## Reference-guided drawing and practice

Canvas now defaults to **Draw with tools**: it cannot generate a raster layer in
this mode, even when MOSS is installed. Choose **Draw from MOSS reference** to
have MOSS make one simple study image in a separate reference panel. Grace sees
that target and the current canvas render, draws using curves/strokes/fills, then
gets a fresh render for review. The reference is never inserted into the artwork,
exported PNG, artwork JSON, or canvas snapshot. **Generate image with MOSS** keeps
the previous direct image-generation feature available explicitly.

Reference mode needs MOSS's engine/weights, a vision-capable chat endpoint with
its image projector configured, and the connected browser to supply screenshots.
It reports unavailable vision or missing screenshots instead of pretending to
observe the drawing. Rendering quality and faithful copying still need live-model
and GPU validation. MOSS reference generation has its own existing time budget.

Completed drawing plans are saved in `drawing_studies` in Grace's existing SQLite
memory. They are ungraded until you choose **Keep as example** or **Needs
improvement**, optionally writing specific feedback. Future drawing requests
retrieve up to three relevant user-reviewed studies, bounded summaries, and small
recipes from kept examples. You can clear the canvas and choose **Draw with tools**
to practice without generating a reference. The reference PNG itself is transient;
studies store prompts, modes, proposed actions and feedback, not image data or
proof of completed playback. These memories support adaptation and future dataset
curation; they do not automatically train model weights or guarantee a lasting
increase in drawing skill.
