"""Minimal ACP v1 client for stdio agent adapters.

The framework deliberately keeps this transport dependency-free.  Both
``claude-agent-acp`` and ``codex-acp`` expose newline-delimited JSON-RPC 2.0 on
stdin/stdout, so the orchestration daemon can launch either adapter and drive
the same initialize/session/prompt lifecycle.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


ACP_PROTOCOL_VERSION = 1


class ACPError(RuntimeError):
    """Base error raised by the ACP transport."""


class ACPTimeoutError(ACPError):
    """An ACP request did not finish before its deadline."""


@dataclass
class ACPResult:
    session_id: str
    stop_reason: str
    response: dict[str, Any]
    updates: list[dict[str, Any]] = field(default_factory=list)
    stderr: str = ""


class ACPClient:
    """Synchronous ACP client around one stdio agent subprocess."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str,
        env: Optional[dict[str, str]] = None,
        permission_policy: str = "allow_once",
        update_handler: Optional[Callable[[dict[str, Any]], None]] = None,
    ):
        if not command or not all(isinstance(item, str) and item for item in command):
            raise ValueError("ACP command must be a non-empty argv list")
        if permission_policy not in {"allow_once", "deny"}:
            raise ValueError("ACP permission policy must be allow_once or deny")
        self.command = list(command)
        self.cwd = os.path.abspath(cwd)
        self.env = dict(env or {})
        self.permission_policy = permission_policy
        self.update_handler = update_handler
        self.process: subprocess.Popen[str] | None = None
        self.session_id = ""
        self.updates: list[dict[str, Any]] = []
        self._next_id = 1
        self._messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stderr_lines: list[str] = []
        self._write_lock = threading.Lock()
        self._cancelled = False

    def start(self) -> None:
        if self.process is not None:
            return
        child_env = os.environ.copy()
        child_env.update(self.env)
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                env=child_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise ACPError(f"cannot start ACP agent {self.command[0]}: {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def initialize(self, timeout: float = 120) -> dict[str, Any]:
        return self.request(
            "initialize",
            {
                "protocolVersion": ACP_PROTOCOL_VERSION,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {"name": "harness-framework", "version": "1.0"},
            },
            timeout=timeout,
        )

    def new_session(self, timeout: float = 60) -> str:
        response = self.request(
            "session/new", {"cwd": self.cwd, "mcpServers": []}, timeout=timeout
        )
        session_id = response.get("sessionId", "")
        if not session_id:
            raise ACPError("ACP session/new response has no sessionId")
        self.session_id = session_id
        return session_id

    def load_session(self, session_id: str, timeout: float = 60) -> str:
        response = self.request(
            "session/load",
            {"cwd": self.cwd, "mcpServers": [], "sessionId": session_id},
            timeout=timeout,
        )
        self.session_id = response.get("sessionId") or session_id
        return self.session_id

    def prompt(
        self,
        text: str,
        *,
        timeout: float,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> ACPResult:
        if not self.session_id:
            raise ACPError("ACP session has not been created")
        response = self.request(
            "session/prompt",
            {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]},
            timeout=timeout,
            should_cancel=should_cancel,
        )
        return ACPResult(
            session_id=self.session_id,
            stop_reason=response.get("stopReason", ""),
            response=response,
            updates=list(self.updates),
            stderr="".join(self._stderr_lines)[-8000:],
        )

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        if self.process is None:
            self.start()
        request_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        cancel_sent = False
        while True:
            if should_cancel and should_cancel() and not cancel_sent:
                self.cancel()
                cancel_sent = True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if method == "session/prompt":
                    self.cancel()
                raise ACPTimeoutError(f"ACP {method} timed out after {timeout:g}s")
            try:
                message = self._messages.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if message is None:
                code = self.process.poll() if self.process else None
                detail = "".join(self._stderr_lines)[-2000:].strip()
                raise ACPError(f"ACP agent exited (code={code}){': ' + detail if detail else ''}")
            if "method" in message:
                self._handle_incoming_call(message)
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise ACPError(
                    f"ACP {method} failed ({error.get('code')}): {error.get('message')}"
                )
            result = message.get("result", {})
            if not isinstance(result, dict):
                raise ACPError(f"ACP {method} returned a non-object result")
            return result

    def cancel(self) -> None:
        if self.session_id and not self._cancelled:
            self._cancelled = True
            self._send({
                "jsonrpc": "2.0", "method": "session/cancel",
                "params": {"sessionId": self.session_id},
            })

    def close(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        self.process = None

    def _send(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None:
            raise ACPError("ACP process is not running")
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self._write_lock:
                process.stdin.write(encoded)
                process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ACPError("ACP agent pipe is closed") from exc

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            try:
                message = json.loads(line)
                if isinstance(message, dict):
                    self._messages.put(message)
            except json.JSONDecodeError:
                self._stderr_lines.append(f"invalid ACP stdout: {line}")
        self._messages.put(None)

    def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        for line in self.process.stderr:
            self._stderr_lines.append(line)

    def _handle_incoming_call(self, message: dict[str, Any]) -> None:
        method = message.get("method", "")
        params = message.get("params", {})
        if method == "session/update":
            self.updates.append(params)
            if self.update_handler:
                self.update_handler(params)
            return
        if "id" not in message:
            return
        if method == "session/request_permission":
            result = self._permission_response(params)
            self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})
            return
        self._send({
            "jsonrpc": "2.0", "id": message["id"],
            "error": {"code": -32601, "message": f"unsupported client method: {method}"},
        })

    def _permission_response(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._cancelled or self.permission_policy == "deny":
            return {"outcome": {"outcome": "cancelled"}}
        options = params.get("options", [])
        option = next(
            (item for item in options if item.get("kind") == "allow_once"),
            options[0] if options else None,
        )
        if not option:
            return {"outcome": {"outcome": "cancelled"}}
        return {
            "outcome": {"outcome": "selected", "optionId": option.get("optionId", "")}
        }

    def __enter__(self) -> "ACPClient":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
