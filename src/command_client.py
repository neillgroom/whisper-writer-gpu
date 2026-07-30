"""Length-prefixed JSON client for the Windows control broker."""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import threading
from typing import Any


class BrokerError(RuntimeError):
    pass


class CommandBrokerClient:
    """Own a control-broker subprocess without importing Windows automation into Qt."""

    def __init__(self, broker_path: str | None = None):
        if broker_path is None:
            broker_path = os.path.join(os.path.dirname(__file__), "control_broker.py")
        self._process = subprocess.Popen(
            [sys.executable, "-u", broker_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._lock = threading.Lock()
        self._next_id = 1

    @staticmethod
    def _read_exact(stream, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = stream.read(size - len(chunks))
            if not chunk:
                raise BrokerError("Control broker closed unexpectedly")
            chunks.extend(chunk)
        return bytes(chunks)

    def request(self, operation: str, **arguments: Any) -> dict[str, Any]:
        if self._process.poll() is not None:
            raise BrokerError("Control broker is not running")
        assert self._process.stdin is not None
        assert self._process.stdout is not None

        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            payload = json.dumps(
                {"id": request_id, "operation": operation, "arguments": arguments}
            ).encode("utf-8")
            self._process.stdin.write(struct.pack(">I", len(payload)))
            self._process.stdin.write(payload)
            self._process.stdin.flush()

            length = struct.unpack(">I", self._read_exact(self._process.stdout, 4))[0]
            response = json.loads(
                self._read_exact(self._process.stdout, length).decode("utf-8")
            )
            if response.get("id") != request_id:
                raise BrokerError("Control broker returned a mismatched response")
            if not response.get("ok"):
                raise BrokerError(response.get("error") or "Control command failed")
            return response

    def route(self, text: str) -> dict[str, Any]:
        return self.request("route", text=text)

    def close(self) -> None:
        if self._process.poll() is not None:
            return
        try:
            self.request("shutdown")
        except (BrokerError, BrokenPipeError, OSError):
            self._process.terminate()
        finally:
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()

