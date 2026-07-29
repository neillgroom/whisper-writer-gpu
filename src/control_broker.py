"""Deterministic Windows developer-control broker.

The broker intentionally runs outside the Qt/Whisper process. It accepts framed JSON on
stdin and writes framed JSON to stdout. No pickle and no arbitrary deserialization.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path(__file__).with_name("control_config.yaml")

KNOWN_APP_PATHS = {
    "vs code": (
        r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
        r"%PROGRAMFILES%\Microsoft VS Code\Code.exe",
        r"%PROGRAMFILES(X86)%\Microsoft VS Code\Code.exe",
    ),
    "chrome": (
        r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
        r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    ),
}


class ControlError(RuntimeError):
    pass


def load_config(path: str | os.PathLike[str] = DEFAULT_CONFIG) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def strip_wake_word(text: str) -> tuple[str, bool]:
    match = re.match(r"^\s*helene[\s,;:!-]*(.*)$", text, flags=re.IGNORECASE)
    return (match.group(1).strip(), True) if match else (text.strip(), False)


class IntentParser:
    """Small, predictable parser; task bodies are never rewritten by a model."""

    def parse(self, text: str) -> tuple[str, dict[str, Any]]:
        text, _ = strip_wake_word(text)
        if not text:
            raise ControlError("I heard the wake word but no command")

        shortcut_match = re.match(
            r"^(?:press|send)\s+(?P<shortcut>.+)$", text, flags=re.IGNORECASE
        )
        if shortcut_match:
            parts = [
                part for part in re.split(
                    r"(?:\s*\+\s*|\s+)",
                    shortcut_match.group("shortcut").strip().casefold(),
                )
                if part
            ]
            aliases = {"control": "ctrl", "windows": "win", "meta": "win"}
            parts = [aliases.get(part, part) for part in parts]
            modifiers = {"ctrl", "alt", "shift", "win"}
            if parts and (len(parts) == 1 or any(part in modifiers for part in parts)):
                return "press_shortcut", {"shortcut": "+".join(parts)}

        patterns: list[tuple[str, str, tuple[str, ...]]] = [
            ("run_workflow", r"^(?:run|start|launch)\s+(?:the\s+)?(?P<workflow>four[\s-]?way(?:\s+review)?)(?:\s+(?:on|for)\s+(?P<project>.+))?$", ("workflow", "project")),
            ("open_project", r"^(?:open|launch)\s+(?:the\s+)?(?:project|repo|repository)\s+(?P<project>.+)$", ("project",)),
            ("start_agent", r"^(?:start|launch|open)\s+(?P<agent>claude(?:\s+code)?|antigravity|grok)(?:\s+(?:in|on|for)\s+(?P<project>.*?))?(?:\s+(?:and\s+)?(?:ask|tell)\s+(?:it\s+)?to\s+(?P<task>.+))?$", ("agent", "project", "task")),
            ("focus_window", r"^(?:focus|switch\s+to|go\s+to)\s+(?P<window>.+)$", ("window",)),
            ("type_text", r"^type\s+(?P<text>.+)$", ("text",)),
            ("run_command", r"^(?:run|execute)\s+(?P<command>tests?|test suite|git status)(?:\s+(?:in|on|for)\s+(?P<project>.+))?$", ("command", "project")),
            ("open_app", r"^(?:open|launch|start)\s+(?P<app>.+)$", ("app",)),
        ]
        for operation, pattern, names in patterns:
            match = re.match(pattern, text, flags=re.IGNORECASE)
            if match:
                values = {
                    name: (match.groupdict().get(name) or "").strip()
                    for name in names
                }
                return operation, values
        raise ControlError(f"I don't have a command for: {text}")


class WindowsController:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.project_roots = [
            Path(os.path.expandvars(root)).expanduser()
            for root in config.get("project_roots", [])
        ]

    def _require_windows(self) -> None:
        if os.name != "nt":
            raise ControlError("Windows control actions can only run on Windows")

    def _lookup(self, section: str, name: str) -> Any:
        wanted = normalize_name(name)
        entries = self.config.get(section, {})
        for key, value in entries.items():
            if normalize_name(key) == wanted:
                return value
        raise ControlError(f"Unknown {section.rstrip('s')}: {name}")

    def find_project(self, name: str) -> Path:
        aliases = self.config.get("projects", {})
        wanted = normalize_name(name)
        for alias, configured_path in aliases.items():
            if normalize_name(alias) == wanted:
                path = Path(os.path.expandvars(configured_path)).expanduser()
                if path.is_dir():
                    return path

        candidates: list[Path] = []
        for root in self.project_roots:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if child.is_dir() and normalize_name(child.name) == wanted:
                    candidates.append(child)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise ControlError(f"More than one project matches {name}")
        raise ControlError(f"Project not found: {name}")

    def _resolve_app(self, app: str, command: Any) -> tuple[str | list[str], bool]:
        """Resolve an app before reporting success; Popen alone is not proof."""
        if isinstance(command, list):
            executable = str(command[0])
            if os.path.isabs(executable) and not os.path.isfile(executable):
                raise ControlError(f"Configured executable is missing: {executable}")
            if not os.path.isabs(executable) and not shutil.which(executable):
                raise ControlError(f"Configured executable is not on PATH: {executable}")
            return command, False

        if os.path.isabs(command) and os.path.isfile(command):
            return command, False
        if shutil.which(command):
            # Command shims such as code.cmd need cmd.exe; real .exe files do not.
            return command, command.casefold().endswith((".cmd", ".bat"))

        for candidate in KNOWN_APP_PATHS.get(normalize_name(app), ()):
            expanded = os.path.expandvars(candidate)
            if os.path.isfile(expanded):
                return expanded, False

        raise ControlError(
            f"Could not find {app}. Install it or set its exact executable path in control_config.yaml"
        )

    def open_app(self, app: str) -> str:
        self._require_windows()
        command = self._lookup("apps", app)
        resolved, use_shell = self._resolve_app(app, command)
        subprocess.Popen(resolved, shell=use_shell)
        return f"Opened {app}"

    def open_project(self, project: str) -> str:
        self._require_windows()
        path = self.find_project(project)
        editor = self.config.get("project_editor", "code")
        subprocess.Popen([editor, str(path)])
        return f"Opened {path.name}"

    def type_text(self, text: str) -> str:
        self._require_windows()
        from pynput.keyboard import Controller

        keyboard = Controller()
        keyboard.type(text)
        return "Typed it"

    def press_shortcut(self, shortcut: str) -> str:
        self._require_windows()
        from pynput.keyboard import Controller, Key

        aliases = {
            "control": Key.ctrl,
            "ctrl": Key.ctrl,
            "alt": Key.alt,
            "shift": Key.shift,
            "win": Key.cmd,
            "windows": Key.cmd,
            "meta": Key.cmd,
            "enter": Key.enter,
            "escape": Key.esc,
            "esc": Key.esc,
            "tab": Key.tab,
            "space": Key.space,
        }
        keys = []
        for part in re.split(r"\s*\+\s*", shortcut.casefold()):
            keys.append(aliases.get(part, part if len(part) == 1 else None))
        if any(key is None for key in keys):
            raise ControlError(f"Unsupported shortcut: {shortcut}")
        keyboard = Controller()
        for key in keys:
            keyboard.press(key)
        for key in reversed(keys):
            keyboard.release(key)
        return f"Pressed {shortcut}"

    def focus_window(self, window: str) -> str:
        self._require_windows()
        import pygetwindow

        matches = [
            item for item in pygetwindow.getAllWindows()
            if window.casefold() in item.title.casefold()
        ]
        if not matches:
            raise ControlError(f"Window not found: {window}")
        target = matches[0]
        if target.isMinimized:
            target.restore()
        target.activate()
        return f"Focused {target.title}"

    def click_control(self, window: str, control: str) -> str:
        self._require_windows()
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise ControlError("click_control requires pywinauto") from exc
        target = Desktop(backend="uia").window(title_re=f".*{re.escape(window)}.*")
        target.child_window(title=control).click_input()
        return f"Clicked {control}"

    def run_command(self, command: str, project: str = "") -> str:
        self._require_windows()
        aliases = {"test": "tests", "test suite": "tests"}
        command = aliases.get(normalize_name(command), normalize_name(command))
        argv = self._lookup("commands", command)
        cwd = self.find_project(project) if project else ROOT
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen(argv, cwd=cwd, creationflags=flags, shell=isinstance(argv, str))
        return f"Started {command} in {cwd.name}"

    def start_agent(self, agent: str, project: str = "", task: str = "") -> str:
        self._require_windows()
        normalized = normalize_name(agent)
        if normalized == "claude":
            normalized = "claude code"
        executable = self._lookup("agents", normalized)
        cwd = self.find_project(project) if project else ROOT
        command = [executable]
        if task:
            command.append(task)
        command_line = subprocess.list2cmdline(command)
        subprocess.Popen(
            ["cmd.exe", "/d", "/k", command_line],
            cwd=cwd,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        suffix = f" in {cwd.name}" if project else ""
        return f"Started {agent}{suffix}"

    def run_workflow(self, workflow: str, project: str = "") -> str:
        self._require_windows()
        normalized = normalize_name(workflow)
        if normalized in {"four way", "four way review"}:
            if not project:
                raise ControlError("Four-way review needs a project name")
            target = self.find_project(project)
            factory = self.config.get("factory", {})
            factory_root = Path(
                os.path.expandvars(factory.get("root", r"C:\Projects\shepherd-factory"))
            ).expanduser()
            argv = [
                *factory.get("gate_command", ["pnpm", "tsx", "src/cli.ts", "gate"]),
                "--repo",
                str(target),
                "--base",
                factory.get("base", "main"),
                "--intent",
                factory.get("intent", "Voice-requested four-way review"),
            ]
            command_line = subprocess.list2cmdline(argv)
            subprocess.Popen(
                ["cmd.exe", "/d", "/k", command_line],
                cwd=factory_root,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            return f"Started four-way gate for {target.name}"

        argv = self._lookup("workflows", normalized)
        cwd = self.find_project(project) if project else ROOT
        subprocess.Popen(
            argv,
            cwd=cwd,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            shell=isinstance(argv, str),
        )
        return f"Started four-way review in {cwd.name}"


class Broker:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or load_config()
        self.parser = IntentParser()
        self.controller = WindowsController(self.config)

    def dispatch(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if operation == "route":
            operation, arguments = self.parser.parse(str(arguments.get("text", "")))
        if operation == "shutdown":
            return {"message": "Control broker stopped", "shutdown": True}
        method = getattr(self.controller, operation, None)
        if operation.startswith("_") or not callable(method):
            raise ControlError(f"Unsupported operation: {operation}")
        message = method(**arguments)
        if self.config.get("speak_responses", True):
            speak(message)
        return {"message": message, "operation": operation}


def speak(message: str) -> None:
    if os.name != "nt":
        return
    powershell = shutil.which("powershell.exe")
    if not powershell:
        powershell = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        )
    if not os.path.isfile(powershell):
        return
    message_encoded = base64.b64encode(message.encode("utf-8")).decode("ascii")
    script = (
        "Add-Type -AssemblyName System.Speech;"
        f"$m=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{message_encoded}'));"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$s.Speak($m)"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    subprocess.Popen(
        [powershell, "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def read_exact(stream, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise EOFError
        data.extend(chunk)
    return bytes(data)


def serve() -> None:
    broker = Broker()
    while True:
        try:
            length = struct.unpack(">I", read_exact(sys.stdin.buffer, 4))[0]
            request = json.loads(read_exact(sys.stdin.buffer, length).decode("utf-8"))
            request_id = request.get("id")
            try:
                result = broker.dispatch(
                    str(request.get("operation", "")),
                    request.get("arguments") or {},
                )
                response = {"id": request_id, "ok": True, **result}
            except Exception as exc:
                response = {"id": request_id, "ok": False, "error": str(exc)}
            payload = json.dumps(response).encode("utf-8")
            sys.stdout.buffer.write(struct.pack(">I", len(payload)))
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()
            if response.get("shutdown"):
                return
        except EOFError:
            return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parse", help="Print the parsed form of one command")
    args = parser.parse_args()
    if args.parse:
        operation, arguments = IntentParser().parse(args.parse)
        print(json.dumps({"operation": operation, "arguments": arguments}, indent=2))
        return
    serve()


if __name__ == "__main__":
    main()
