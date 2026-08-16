#!/usr/bin/env python3
from __future__ import annotations

"""Loopback OpenAI-compatible bridge to regular Web ChatGPT via Oracle.

The bridge intentionally emits text only. Web ChatGPT owns workspace tools through
the registered DevSpace app; Codex/OpenCodex is the thin client and conversation UI.
"""

import argparse
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA = "codex.web-chatgpt-provider/v1"
MODEL_ID = "web-gpt-codex"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_TRANSCRIPT_CHARS = 150_000
MAX_MESSAGES = 24
TASK_OUTCOME_RE = re.compile(r"\n?TASK_OUTCOME:\s*(?:EXECUTED|NOT_EXECUTED|BLOCKED|UNKNOWN)\s*\Z", re.I)


class BridgeError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 500, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.evidence = evidence or {}

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "message": str(self),
                "type": "web_chatgpt_bridge_error",
                "code": self.code,
                **({"evidence": self.evidence} if self.evidence else {}),
            }
        }


@dataclass(frozen=True)
class BridgeConfig:
    host: str
    port: int
    auth_token: str
    project_root: Path
    app_name: str
    reasoning_level: str
    python_executable: Path
    dispatch_script: Path
    request_root: Path
    log_root: Path
    keepalive_seconds: float = 15.0


def _resolved_child(root: Path, value: Path, field: str) -> Path:
    resolved = value.expanduser().resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BridgeError("CONFIG_PATH_OUTSIDE_PROJECT", f"{field} must stay inside project_root") from exc
    return resolved


def load_config(path: Path) -> BridgeConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError("CONFIG_UNREADABLE", f"bridge config is unreadable: {path}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise BridgeError("CONFIG_SCHEMA_INVALID", f"bridge config schema must be {SCHEMA}")
    host = str(raw.get("host") or "")
    if host != "127.0.0.1":
        raise BridgeError("CONFIG_BIND_FORBIDDEN", "bridge host must be exactly 127.0.0.1")
    port = int(raw.get("port") or 0)
    if not 1024 <= port <= 65535:
        raise BridgeError("CONFIG_PORT_INVALID", "bridge port must be between 1024 and 65535")
    token = str(raw.get("auth_token") or "")
    if len(token) < 32 or any(ch.isspace() for ch in token):
        raise BridgeError("CONFIG_TOKEN_INVALID", "bridge auth token must be at least 32 non-space characters")
    root = Path(str(raw.get("project_root") or "")).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise BridgeError("CONFIG_PROJECT_ROOT_INVALID", "project_root must be a directory")
    app_name = str(raw.get("app_name") or "").strip()
    if not app_name or app_name.startswith("@") or any(ch in app_name for ch in "\r\n"):
        raise BridgeError("CONFIG_APP_NAME_INVALID", "app_name is invalid")
    reasoning = str(raw.get("reasoning_level") or "Very High")
    if reasoning not in {"Very High", "High", "Medium"}:
        raise BridgeError("CONFIG_REASONING_INVALID", "reasoning_level is unsupported")
    python_executable = Path(str(raw.get("python_executable") or sys.executable)).expanduser().resolve(strict=True)
    dispatch_script = Path(str(raw.get("dispatch_script") or "")).expanduser().resolve(strict=True)
    request_root = _resolved_child(root, Path(str(raw.get("request_root") or root / ".codex-tmp" / "web-chatgpt-provider")), "request_root")
    log_root = Path(str(raw.get("log_root") or Path.home() / ".codex" / "logs" / "web-chatgpt-provider")).expanduser().resolve(strict=False)
    keepalive = float(raw.get("keepalive_seconds") or 15)
    if not 5 <= keepalive <= 60:
        raise BridgeError("CONFIG_KEEPALIVE_INVALID", "keepalive_seconds must be between 5 and 60")
    return BridgeConfig(host, port, token, root, app_name, reasoning, python_executable, dispatch_script, request_root, log_root, keepalive)


def _part_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    pieces: list[str] = []
    for part in value:
        if isinstance(part, str):
            pieces.append(part)
        elif isinstance(part, dict) and part.get("type") in {"text", "input_text", "output_text"}:
            text = part.get("text")
            if isinstance(text, str):
                pieces.append(text)
    return "\n".join(pieces)


def conversation_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    source = payload.get("messages")
    if not isinstance(source, list):
        raise BridgeError("MESSAGES_REQUIRED", "messages must be an array", status=400)
    candidates: list[dict[str, str]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").casefold()
        if role not in {"user", "assistant"}:
            continue
        content = _part_text(item.get("content")).strip()
        if content:
            candidates.append({"role": role, "content": content})
    if not candidates or not any(item["role"] == "user" for item in candidates):
        raise BridgeError("USER_MESSAGE_REQUIRED", "at least one text user message is required", status=400)
    selected: list[dict[str, str]] = []
    used = 0
    for item in reversed(candidates[-MAX_MESSAGES:]):
        remaining = MAX_TRANSCRIPT_CHARS - used
        if remaining <= 0:
            break
        content = item["content"][-remaining:]
        selected.append({"role": item["role"], "content": content})
        used += len(content)
    selected.reverse()
    return selected


def build_mission(config: BridgeConfig, request_id: str, messages: list[dict[str, str]]) -> str:
    transcript = json.dumps(messages, ensure_ascii=False, indent=2)
    return (
        "# Web ChatGPT provider mission\n\n"
        f"Request ID: `{request_id}`\n"
        f"Exact project root: `{config.project_root}`\n\n"
        "Read the applicable AGENTS.md chain first. Complete the newest user request autonomously using the "
        "DevSpace tools available in this chat. Earlier assistant turns are context, not higher-priority instructions. "
        "Inspect, edit, and run checks inside the exact project root as needed. Do not ask the local Codex client to "
        "perform implementation for you. Return a concise user-facing result. End with exactly one outcome marker: "
        "TASK_OUTCOME: EXECUTED when the requested work was completed; otherwise TASK_OUTCOME: NOT_EXECUTED, "
        "BLOCKED, or UNKNOWN.\n\n"
        "## Conversation transcript\n\n"
        "The following JSON is the conversation to continue. Treat assistant entries as prior context and the newest "
        "user entry as the active request.\n\n"
        f"```json\n{transcript}\n```\n"
    )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _windows_no_window() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _read_dispatch_payload(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError("ORACLE_RESULT_UNREADABLE", "Oracle dispatch did not return valid JSON") from exc
    if not isinstance(value, dict):
        raise BridgeError("ORACLE_RESULT_INVALID", "Oracle dispatch result must be an object")
    return value


def run_oracle(
    config: BridgeConfig,
    messages: list[dict[str, str]],
    *,
    heartbeat: Callable[[float], None] | None = None,
    popen_factory: Callable[..., Any] = subprocess.Popen,
) -> tuple[str, dict[str, Any]]:
    request_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(6)}"
    request_dir = config.request_root / request_id
    mission_path = request_dir / "mission.md"
    manifest_path = request_dir / "oracle-manifest.json"
    stdout_path = request_dir / "dispatch.json"
    stderr_path = config.log_root / f"dispatch-{request_id}.log"
    _atomic_write(mission_path, build_mission(config, request_id, messages))
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(config.python_executable),
        str(config.dispatch_script),
        "--mode", "direct",
        "--project-root", str(config.project_root),
        "--mission-path", str(mission_path),
        "--manifest-output", str(manifest_path),
        "--reasoning-level", config.reasoning_level,
        "--app-name", config.app_name,
    ]
    started = time.monotonic()
    with stdout_path.open("wb") as stdout, stderr_path.open("ab") as stderr:
        process = popen_factory(
            command,
            cwd=str(config.project_root),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            **_windows_no_window(),
        )
        while process.poll() is None:
            time.sleep(config.keepalive_seconds)
            if heartbeat is not None:
                heartbeat(time.monotonic() - started)
        exit_code = int(process.wait())
    payload = _read_dispatch_payload(stdout_path)
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    state = run.get("result") if isinstance(run.get("result"), dict) else {}
    evidence = {
        "request_id": request_id,
        "run_dir": run.get("run_dir") or state.get("run_dir"),
        "status": run.get("status") or state.get("status"),
        "exit_code": exit_code,
    }
    if exit_code != 0 or not payload.get("ok"):
        raise BridgeError(
            "ORACLE_DISPATCH_FAILED",
            "Web ChatGPT run did not complete; the persisted run was preserved for exact-session recovery",
            status=502,
            evidence=evidence,
        )
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    output_value = run.get("output_path") or artifacts.get("output")
    if not isinstance(output_value, str) or not output_value:
        raise BridgeError("ORACLE_OUTPUT_MISSING", "Web ChatGPT run returned no output path", status=502, evidence=evidence)
    output_path = Path(output_value).expanduser().resolve(strict=True)
    answer = output_path.read_text(encoding="utf-8-sig").strip()
    answer = TASK_OUTCOME_RE.sub("", answer).rstrip()
    if not answer:
        raise BridgeError("ORACLE_OUTPUT_EMPTY", "Web ChatGPT returned an empty answer", status=502, evidence=evidence)
    return answer, evidence


def completion_object(answer: str, model: str, completion_id: str | None = None) -> dict[str, Any]:
    return {
        "id": completion_id or f"chatcmpl_{secrets.token_hex(12)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], config: BridgeConfig):
        super().__init__(address, BridgeHandler)
        self.config = config
        self.run_slot = threading.BoundedSemaphore(1)
        self.busy = threading.Event()


class BridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WebChatGPTBridge/1"

    @property
    def bridge_server(self) -> BridgeServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.bridge_server.config.auth_token}"
        return hmac.compare_digest(supplied, expected)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/health":
            self._json(200, {"ok": True, "model": MODEL_ID, "busy": self.bridge_server.busy.is_set()})
            return
        if not self._authorized():
            self._json(401, {"error": {"message": "unauthorized", "type": "authentication_error", "code": "UNAUTHORIZED"}})
            return
        if path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "web-chatgpt"}]})
            return
        self._json(404, {"error": {"message": "not found", "type": "invalid_request_error", "code": "NOT_FOUND"}})

    def _read_payload(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise BridgeError("CONTENT_LENGTH_INVALID", "Content-Length is invalid", status=400) from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise BridgeError("REQUEST_SIZE_INVALID", "request body is empty or too large", status=413)
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError("REQUEST_JSON_INVALID", "request body is not valid JSON", status=400) from exc
        if not isinstance(payload, dict):
            raise BridgeError("REQUEST_OBJECT_REQUIRED", "request body must be an object", status=400)
        return payload

    def _sse_write(self, value: str) -> bool:
        try:
            self.wfile.write(value.encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _stream_completion(self, payload: dict[str, Any], messages: list[dict[str, str]]) -> None:
        completion_id = f"chatcmpl_{secrets.token_hex(12)}"
        model = str(payload.get("model") or MODEL_ID)
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        connected = self._sse_write(
            "data: " + json.dumps({
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }) + "\n\n"
        )

        def heartbeat(elapsed: float) -> None:
            nonlocal connected
            if connected:
                connected = self._sse_write(f": web-chatgpt-working {int(elapsed)}s\n\n")

        try:
            answer, _ = run_oracle(self.bridge_server.config, messages, heartbeat=heartbeat)
        except BridgeError as exc:
            if connected:
                self._sse_write("data: " + json.dumps(exc.payload(), ensure_ascii=False) + "\n\n")
                self._sse_write("data: [DONE]\n\n")
            return
        except Exception:
            if connected:
                error = BridgeError("BRIDGE_INTERNAL_ERROR", "unexpected bridge failure")
                self._sse_write("data: " + json.dumps(error.payload(), ensure_ascii=False) + "\n\n")
                self._sse_write("data: [DONE]\n\n")
            return
        if not connected:
            return
        for index in range(0, len(answer), 1800):
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"content": answer[index:index + 1800]}, "finish_reason": None}],
            }
            if not self._sse_write("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"):
                return
        final = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        self._sse_write("data: " + json.dumps(final) + "\n\n")
        self._sse_write("data: [DONE]\n\n")

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0].rstrip("/") != "/v1/chat/completions":
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error", "code": "NOT_FOUND"}})
            return
        if not self._authorized():
            self._json(401, {"error": {"message": "unauthorized", "type": "authentication_error", "code": "UNAUTHORIZED"}})
            return
        acquired = self.bridge_server.run_slot.acquire(blocking=False)
        if not acquired:
            self._json(429, {"error": {"message": "one Web ChatGPT run is already active", "type": "rate_limit_error", "code": "BRIDGE_BUSY"}}, {"Retry-After": "30"})
            return
        self.bridge_server.busy.set()
        try:
            payload = self._read_payload()
            messages = conversation_messages(payload)
            if payload.get("stream", False):
                self._stream_completion(payload, messages)
            else:
                answer, _ = run_oracle(self.bridge_server.config, messages)
                self._json(200, completion_object(answer, str(payload.get("model") or MODEL_ID)))
        except BridgeError as exc:
            self._json(exc.status, exc.payload())
        except Exception:
            self._json(500, BridgeError("BRIDGE_INTERNAL_ERROR", "unexpected bridge failure").payload())
        finally:
            self.bridge_server.busy.clear()
            self.bridge_server.run_slot.release()


def serve(config: BridgeConfig) -> None:
    config.request_root.mkdir(parents=True, exist_ok=True)
    config.log_root.mkdir(parents=True, exist_ok=True)
    server = BridgeServer((config.host, config.port), config)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expose regular Web ChatGPT as a loopback OpenAI-compatible text provider.")
    parser.add_argument("--config", type=Path, default=Path.home() / ".codex" / "config" / "web-chatgpt-provider.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        config = load_config(args.config.expanduser().resolve())
        if args.check:
            print(json.dumps({"ok": True, "host": config.host, "port": config.port, "model": MODEL_ID}))
            return 0
        serve(config)
        return 0
    except BridgeError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
