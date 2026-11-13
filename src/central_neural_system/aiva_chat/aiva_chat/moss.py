"""Grace's raster painting: a client for the MossInfer ``moss-server`` sidecar.

``moss-server`` (from the community MossInfer engine for MOSS-Image) speaks one
JSON message per line over stdin/stdout — protocol version 2 — the same role
``llama-server`` plays for llama.cpp. This module owns one child process:

* ``hello`` negotiates the protocol,
* ``load`` loads the MOSS-Image model once (INT8 components, ``min_memory``
  streaming so a small GPU is enough),
* ``generate`` renders one PNG (the 8-step DMD distill bundle resolves the
  step count and CFG),
* ``release`` frees GPU memory after every painting so the graphics card
  stays available to the chat models.

The server trusts its launcher completely, so every path is produced here and
never taken from model output or the network.
"""
from __future__ import annotations

import json
import os
import selectors
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from pathlib import Path

ENGINE_ROOT = Path("/home/oppa-ai/mossinfer/mossinfer-2.2.1-linux-cu13-x86_64")
WEIGHTS_ROOT = Path("/home/oppa-ai/AGi/models/mossimage")


class MossPaintError(RuntimeError):
    """A painting could not be generated."""


def _path_env(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value) if value else default


def paint_size(width, height) -> tuple[int, int]:
    """Choose the generation size for a canvas placement.

    The result is aspect-true to the placement, made of multiples of 16, kept
    inside 256..704 per side (small sizes look bad, large ones are slow) and
    capped at roughly 0.6 MP so a painted layer stays affordable.
    """
    try:
        width = min(1000.0, max(0.0, float(width)))
        height = min(650.0, max(0.0, float(height)))
    except (TypeError, ValueError):
        return 512, 512
    if not width or not height:
        return 512, 512
    scale = max(1.0, 256.0 / min(width, height))
    width, height = width * scale, height * scale
    if width > 704:
        height *= 704.0 / width
        width = 704.0
    if height > 704:
        width *= 704.0 / height
        height = 704.0
    if width * height > 614400:  # 0.6 MP cap, applied proportionally
        shrink = (614400.0 / (width * height)) ** 0.5
        width, height = width * shrink, height * shrink
    def round16(value: float) -> int:
        return max(256, min(704, int(round(value / 16)) * 16))
    return round16(width), round16(height)


class MossClient:
    """Own one ``moss-server`` child; generate PNG paintings on demand."""

    def __init__(self) -> None:
        self.binary = _path_env("MOSS_PAINT_SERVER_BIN", ENGINE_ROOT / "bin" / "moss-server")
        self.model_dir = _path_env("MOSS_PAINT_MODEL_DIR", WEIGHTS_ROOT / "sft")
        self.dit_file = _path_env(
            "MOSS_PAINT_DIT_FILE", WEIGHTS_ROOT / "comfy" / "diffusion_models" / "moss_image_sft_int8_convrot.safetensors")
        self.text_encoder_file = _path_env(
            "MOSS_PAINT_TE_FILE", WEIGHTS_ROOT / "comfy" / "text_encoders" / "qwen3.5_9b_int8_convrot.safetensors")
        self.vae_file = _path_env(
            "MOSS_PAINT_VAE_FILE", WEIGHTS_ROOT / "comfy" / "vae" / "flux2-vae.safetensors")
        self.distill_dir = _path_env("MOSS_PAINT_DISTILL_DIR", WEIGHTS_ROOT / "distill-8step")
        self.device = os.getenv("MOSS_PAINT_DEVICE", "1")
        self.vram_budget_bytes = int(os.getenv("MOSS_PAINT_VRAM_MIB", "4096")) * 1024 * 1024
        self.timeout = float(os.getenv("MOSS_PAINT_TIMEOUT_SECONDS", "300"))
        self.release_after = os.getenv("MOSS_PAINT_RELEASE", "1") != "0"
        self._process: subprocess.Popen | None = None
        self._buffer = b""
        self._stderr: deque[str] = deque(maxlen=6)
        self._lock = threading.Lock()
        self._next_id = 1
        self._loaded = False

    # -- availability ---------------------------------------------------------

    def available(self) -> bool:
        """True when the engine and the weights are all in place."""
        return (self.binary.is_file()
                and (self.model_dir / "model_index.json").is_file()
                and self.dit_file.is_file()
                and self.text_encoder_file.is_file()
                and self.vae_file.is_file()
                and (self.distill_dir / "lora.safetensors").is_file()
                and (self.distill_dir / "time_embedding.safetensors").is_file())

    # -- process handling -----------------------------------------------------

    def _stderr_tail(self) -> str:
        return " ".join(self._stderr)[-600:]

    def _spawn(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._buffer = b""
        self._loaded = False
        env = dict(os.environ, CUDA_VISIBLE_DEVICES=self.device)
        try:
            self._process = subprocess.Popen(
                [str(self.binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env)
        except OSError as error:
            self._process = None
            raise MossPaintError(f"Cannot start moss-server: {error}") from error
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for raw in iter(process.stderr.readline, b""):
            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                self._stderr.append(text)

    def _send(self, method: str, params: dict | None = None) -> int:
        request_id = self._next_id
        self._next_id += 1
        request = {"id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        try:
            assert self._process is not None and self._process.stdin is not None
            self._process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            self._process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError, AssertionError):
            raise MossPaintError(f"moss-server is not accepting requests. {self._stderr_tail()}") from None
        return request_id

    def _read_line(self, timeout: float) -> str | None:
        """One protocol line with a timeout; ``None`` when it does not arrive."""
        assert self._process is not None and self._process.stdout is not None
        buffer = self._buffer
        deadline = time.monotonic() + max(0.1, timeout)
        with selectors.DefaultSelector() as selector:
            selector.register(self._process.stdout, selectors.EVENT_READ)
            while b"\n" not in buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._buffer = buffer
                    return None
                if not selector.select(remaining):
                    continue
                chunk = self._process.stdout.read1(65536)
                if not chunk:
                    raise MossPaintError(f"moss-server stopped. {self._stderr_tail()}")
                buffer += chunk
        line, _, self._buffer = buffer.partition(b"\n")
        return line.decode("utf-8", errors="replace").strip()

    def _call(self, method: str, params: dict | None, timeout: float,
              wait_done: bool = False) -> dict:
        """Send one request; return its result, or its ``done`` event."""
        assert self._process is not None
        request_id = self._send(method, params)
        deadline = time.monotonic() + max(1.0, timeout)
        response: dict | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MossPaintError(f"moss-server did not answer {method} within {timeout:g}s")
            line = self._read_line(remaining)
            if line is None:
                raise MossPaintError(f"moss-server did not answer {method} within {timeout:g}s")
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if message.get("event") == "done":
                if message.get("status") == "error":
                    error = message.get("error") or {}
                    raise MossPaintError(f'{error.get("code", "error")}: {error.get("message", "")}')
                if response is not None or method in ("generate", "preload"):
                    return message
            if "error" in message:
                error = message["error"]
                raise MossPaintError(f'{error.get("code", "error")}: {error.get("message", "")}')
            if "result" in message:
                if not wait_done or method not in ("generate", "preload"):
                    return message
                response = message

    # -- protocol steps -------------------------------------------------------

    def _hello(self) -> None:
        result = self._call("hello", {"client": "aiva_chat", "protocol_version": 2}, 30)
        if result.get("protocol_version") != 2:
            raise MossPaintError("moss-server speaks an unexpected protocol version")

    def _load(self) -> None:
        if self._loaded:
            return
        params = {
            "model": str(self.model_dir),
            "tier": "high",
            "mode": "min_memory",
            "device": 0,
            "vram_budget_bytes": self.vram_budget_bytes,
            "dit_file": str(self.dit_file),
            "text_encoder_file": str(self.text_encoder_file),
            "vae_file": str(self.vae_file),
            "distill": str(self.distill_dir),
            "te_host_pin": "auto",
        }
        self._call("load", params, 120)
        self._loaded = True

    def _release(self) -> None:
        """Free GPU memory after a painting; the next one reloads from disk."""
        if not self.release_after or self._process is None:
            return
        try:
            self._call("release", {"keep_host_copy": False}, 120)
            # The next painting must load the weights again after release.
            self._loaded = False
        except MossPaintError:
            # The engine keeps its memory; generation still works next time.
            pass

    # -- public API -----------------------------------------------------------

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        """Render one painting and return its PNG bytes."""
        prompt = (prompt or "").strip()
        if not prompt:
            raise MossPaintError("The painting prompt is empty")
        output = Path(tempfile.gettempdir()) / f"grace-paint-{uuid.uuid4().hex}.png"
        with self._lock:
            self._spawn()
            try:
                self._hello()
                self._load()
                self._call("generate", {
                    "prompt": prompt[:2000],
                    "width": int(width),
                    "height": int(height),
                    "output": str(output),
                    "save_metadata": False,
                    "include_prompts": False,
                    "distill": str(self.distill_dir),
                }, self.timeout, wait_done=True)
                try:
                    return output.read_bytes()
                except OSError as error:
                    raise MossPaintError(f"The painted PNG could not be read: {error}") from error
            finally:
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    pass
                self._release()

    def close(self) -> None:
        """Ask the sidecar to shut down; fall back to killing it."""
        process, self._process = self._process, None
        self._loaded = False
        if process is None or process.poll() is not None:
            return
        try:
            process.stdin.write(b'{"id": 0, "method": "shutdown"}\n')
            process.stdin.flush()
            process.wait(timeout=10)
            return
        except Exception:
            pass
        process.kill()
