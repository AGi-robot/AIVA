"""ROS adapter between TelepathicSignal, a local LLM, and the WebUI stream."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import httpx
import rclpy
from aiva_cryptic_signal_core import CrypticSignalCore
from cryptic_signal_core.msg import TelepathicSignal
from rclpy.node import Node
from std_msgs.msg import String
from ament_index_python.packages import get_package_share_directory

from .memory import GraceMemory
from .drawing import DRAW_TOOL, DRAW_INSTRUCTIONS, validate_actions


def _load_soul() -> str:
    """Load AIVA's packaged soul, with a source-workspace fallback."""
    candidates = []
    try:
        candidates.append(Path(get_package_share_directory("aiva_chat")) / "persona" / "SOUL.md")
    except Exception:
        pass
    candidates.append(Path(__file__).resolve().parents[4] / "persona" / "SOUL.md")
    for path in candidates:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return "You are Grace, AIVA's thoughtful and helpful companion."


class AivaChat(Node):
    """Decode WebUI messages, call an OpenAI-compatible LLM, and stream replies."""

    def __init__(self) -> None:
        super().__init__("aiva_chat")
        self.declare_parameter("input_topic", "/telepathic/stimulus")
        self.declare_parameter("webui_input_topic", "/aiva/chat_input")
        self.declare_parameter("response_topic", "/tms/text_response")
        self.declare_parameter("llm_base_url", os.getenv("AIVA_LLM_BASE_URL", "http://127.0.0.1:11434/v1"))
        self.declare_parameter(
            "llm_model",
            os.getenv("AIVA_LLM_MODEL", "aiva-ministral-3-8b-ctx25600"),
        )
        self.declare_parameter("system_prompt", _load_soul())
        self.declare_parameter("agi_home", os.getenv("AGI_HOME", "~/.agi"))
        default_memory_path = (
            Path(str(self.get_parameter("agi_home").value)).expanduser()
            / "profile" / "grace_memory.sqlite3"
        )
        self.declare_parameter("memory_path", os.getenv("AIVA_MEMORY_PATH", str(default_memory_path)))
        self.declare_parameter("memory_turns", 8)
        self._base_url = str(self.get_parameter("llm_base_url").value).rstrip("/")
        self._model = str(self.get_parameter("llm_model").value)
        self._system_prompt = str(self.get_parameter("system_prompt").value)
        profile_path = Path(str(self.get_parameter("agi_home").value)).expanduser() / "profile" / "user.md"
        try:
            user_profile = profile_path.read_text(encoding="utf-8").strip()
        except OSError as error:
            user_profile = ""
            self.get_logger().warning(f"User profile unavailable at {profile_path}: {error}")
        if user_profile:
            self._system_prompt += f"\n\nUser profile (trusted context; do not invent beyond it):\n{user_profile}"
        memory_path = str(self.get_parameter("memory_path").value)
        self._memory = GraceMemory(memory_path, int(self.get_parameter("memory_turns").value))
        self._cryptic = CrypticSignalCore()
        self._api_key = os.getenv("AIVA_LLM_API_KEY", "")
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aiva-llm")
        self._response_pub = self.create_publisher(
            String, str(self.get_parameter("response_topic").value), 10
        )
        self._input_sub = self.create_subscription(
            TelepathicSignal,
            str(self.get_parameter("input_topic").value),
            self._on_signal,
            10,
        )
        self._webui_input_sub = self.create_subscription(
            String,
            str(self.get_parameter("webui_input_topic").value),
            self._on_webui_input,
            10,
        )
        self.get_logger().info(
            f"AIVA chat listening on {self.get_parameter('input_topic').value} and "
            f"WebUI input {self.get_parameter('webui_input_topic').value}; "
            f"LLM={self._base_url} model={self._model}"
        )

    def _submit_prompt(self, text: str, identifier: str = "") -> None:
        text = text.strip()
        if not text:
            self.get_logger().warning("Received an empty chat message")
            return
        self.get_logger().info(
            f"Received chat message ({len(text)} characters, id={identifier}); sending to LLM"
        )
        self._pool.submit(self._answer, text, identifier)

    def _on_webui_input(self, message: String) -> None:
        """Accept plain WebUI chat over the browser's persistent rosbridge connection."""
        try:
            payload = json.loads(message.data)
        except (TypeError, json.JSONDecodeError):
            payload = {"text": message.data}
        if not isinstance(payload, dict):
            self.get_logger().warning("Ignoring malformed WebUI chat payload")
            return
        text = str(payload.get("text", ""))
        if payload.get("web_search"):
            text = f"/web {text}"
        if isinstance(payload.get("canvas"), dict):
            if text.strip():
                self._pool.submit(self._draw, text.strip(), str(payload.get("identifier", "")), payload["canvas"])
            return
        self._submit_prompt(text, str(payload.get("identifier", "")))

    def _on_signal(self, signal: TelepathicSignal) -> None:
        self.get_logger().info(
            f"Received TelepathicSignal on chat input (channel={signal.channel!r}, "
            f"scheme={signal.scheme!r}, id={signal.identifier})"
        )
        # Older WebUI deployments use the legacy `grace` channel. It is still
        # a direct user-to-Grace chat signal, so accept it alongside AIVA's
        # newer explicit channel names.
        if signal.channel not in {"aiva", "agi", "grace", ""}:
            self.get_logger().info(f"Ignoring non-chat channel {signal.channel!r}")
            return
        try:
            text = self._cryptic.decode(bytes(signal.signal), int(signal.pulse), signal.scheme).strip()
        except (TypeError, ValueError) as error:
            self.get_logger().warning(f"Ignoring invalid TelepathicSignal: {error}")
            return
        self._submit_prompt(text, signal.identifier)

    def _publish(self, event: dict) -> None:
        message = String()
        message.data = json.dumps(event, ensure_ascii=False)
        self._response_pub.publish(message)

    def _answer(self, text: str, identifier: str) -> None:
        try:
            timezone_name = os.getenv("AIVA_TIMEZONE", "America/Vancouver")
            now = datetime.now(ZoneInfo(timezone_name))
            normalized_text = text.casefold()
            asks_for_clock = bool(re.search(
                r"\b(?:what(?:'s| is)?\s+(?:the\s+)?time(?:\s+is\s+it)?|"
                r"what\s+time\s+is\s+it|do\s+you\s+know\s+(?:(?:what|the)\s+)?time(?:\s+it\s+is)?|"
                r"tell\s+me\s+(?:the\s+)?time|current\s+time|time\s+(?:right\s+now|now|again))\b",
                normalized_text,
            ))
            asks_for_date = bool(re.search(
                r"\b(?:what(?:'s| is)?\s+(?:today's\s+)?date|what(?:'s| is)?\s+the\s+date\s+today|"
                r"what(?:'s| is)?\s+(?:the\s+)?day\s+(?:today|is\s+it)|current\s+date|today's\s+date|date\s+today)\b",
                normalized_text,
            ))
            if asks_for_clock or asks_for_date:
                local_date = f"{now.strftime('%A, %B')} {now.day}, {now.year}"
                local_time = now.strftime("%I:%M %p").lstrip("0")
                if asks_for_clock and asks_for_date:
                    answer = f"It’s {local_date} at {local_time} {now.tzname()} ({timezone_name})."
                elif asks_for_clock:
                    answer = f"It’s {local_time} {now.tzname()} ({timezone_name}) on {local_date}."
                else:
                    answer = f"Today is {local_date} ({timezone_name})."
                self._publish({"type": "start", "content": ""})
                self._publish({"type": "delta", "content": answer})
                self._memory.add_exchange(text, answer)
                self._publish({"type": "done", "content": "", "radio_id": identifier})
                return
            current_time = (
                f"Current local date and time: {now.strftime('%A, %B %d, %Y at %I:%M:%S %p %Z')} "
                f"in the {timezone_name} timezone (UTC offset {now.strftime('%z')}). "
                "Use this as the authoritative current time for the user."
            )
            messages = [{"role": "system", "content": f"{self._system_prompt}\n\n{current_time}"}]
            messages.extend(self._memory.recent())
            messages.append({"role": "user", "content": text})
            headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
            self._publish({"type": "start", "content": ""})
            answer_parts: list[str] = []
            with httpx.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json={"model": self._model, "messages": messages, "stream": True},
                headers=headers,
                timeout=120,
            ) as response:
                self.get_logger().info(f"LLM streaming request returned HTTP {response.status_code}")
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                        chunk = event.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    except (IndexError, TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(chunk, str) and chunk:
                        answer_parts.append(chunk)
                        self._publish({"type": "delta", "content": chunk})

            answer = "".join(answer_parts).strip()
            if not answer:
                raise ValueError("LLM returned an empty response")
            self._memory.add_exchange(text, answer)
            self._publish({"type": "done", "content": "", "radio_id": identifier})
            self.get_logger().info(f"Published Grace reply (id={identifier})")
        except Exception as error:  # report provider/network failures to the chat UI
            self.get_logger().error(f"AIVA LLM request failed: {error}")
            self._publish({"type": "error", "content": f"AIVA could not reach its LLM: {error}"})

    def _draw(self, text: str, identifier: str, canvas: dict) -> None:
        """Ask the model for bounded drawing actions and send them to the Canvas page."""
        try:
            objects = canvas.get("objects", [])
            if not isinstance(objects, list) or len(objects) > 100:
                raise ValueError("Canvas is too large for one drawing request")
            objects = validate_actions(objects) if objects else []
            selected = canvas.get("selected")
            if selected not in {obj.get("id") for obj in objects}:
                selected = None
            headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
            self._publish({"type": "canvas_start", "identifier": identifier})
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                json={"model": self._model, "messages": [
                    {"role": "system", "content": self._system_prompt + "\n\n" + DRAW_INSTRUCTIONS},
                    {"role": "user", "content": (
                        "Canvas state: "
                        + json.dumps({"objects": objects, "selected": selected}, ensure_ascii=False)
                        + "\n\nDrawing request: " + text
                    )},
                ], "tools": [DRAW_TOOL], "tool_choice": {"type": "function", "function": {"name": "canvas_draw"}}, "stream": False},
                headers=headers, timeout=120,
            )
            if response.is_error:
                # Ollama's generic HTTPStatusError string omits its response
                # body, which often contains the actual tool/model failure.
                detail = response.text.strip().replace("\n", " ")[:1200]
                self.get_logger().error(
                    f"Canvas LLM request returned HTTP {response.status_code}: {detail}"
                )
                raise RuntimeError(
                    f"LLM returned HTTP {response.status_code}"
                    + (f": {detail}" if detail else "")
                )
            message = response.json()["choices"][0]["message"]
            actions = []
            reply = "Here is what I made for you."
            for call in message.get("tool_calls", []):
                function = call.get("function", {})
                if function.get("name") != "canvas_draw":
                    raise ValueError("Unsupported drawing tool")
                result = json.loads(function["arguments"])
                if isinstance(result.get("response"), str):
                    reply = result["response"].strip()[:1000] or reply
                requested_actions = result.get("actions", [])
                # Some local models return an empty tool action list when asked
                # to write on the canvas. Turn their short reply into visible
                # canvas text instead of failing the whole request.
                if not requested_actions:
                    note = reply if reply != "Here is what I made for you." else text[:300]
                    requested_actions = [{
                        "kind": "text", "x": 90, "y": 120, "size": 32,
                        "color": "#df78a8", "text": note,
                    }]
                actions.extend(validate_actions(requested_actions))
            if not actions:
                note = str(message.get("content") or "").strip()[:300]
                if not note:
                    note = text[:300] or "A little note for you."
                actions = validate_actions([{
                    "kind": "text", "x": 90, "y": 120, "size": 32,
                    "color": "#df78a8", "text": note,
                }])
            actions = validate_actions(actions)
            self._publish({"type": "canvas_reply", "identifier": identifier, "content": reply})
            self._publish({"type": "canvas_actions", "identifier": identifier, "actions": actions})
            self._memory.add_exchange(text, f"Prepared {len(actions)} canvas actions: " + json.dumps(actions, ensure_ascii=False))
            self._publish({"type": "canvas_done", "identifier": identifier})
        except Exception as error:
            self.get_logger().error(f"Canvas request failed: {error}")
            self._publish({"type": "canvas_error", "identifier": identifier, "content": str(error)})

    def destroy_node(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        super().destroy_node()


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = AivaChat()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
