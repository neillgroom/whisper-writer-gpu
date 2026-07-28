# Helene Developer Voice Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Whisper Writer GPU with intentional developer commands that launch or focus configured coding agents and workflows, submit tasks immediately, and speak terse local results.

**Architecture:** Keep the existing Qt, microphone, and CUDA transcription pipeline intact. Route command transcripts to an isolated length-prefixed JSON broker that deterministically parses commands, resolves repositories and configured adapters, executes bounded Windows actions, and delegates speech to a separate Pocket TTS environment.

**Tech Stack:** Python 3.11, PyQt5, pynput, PyYAML, stdlib `unittest`, Windows Terminal, PyGetWindow, Pocket TTS 2.1.0, stdlib framed JSON IPC.

## Global Constraints

- Preserve `Pause` dictation behavior and the existing `large-v3-turbo` CUDA worker.
- `Ctrl+Pause` enters explicit command mode; `Pause` plus a leading case-insensitive `Helene` prefix also enters command mode.
- Agent prompts submit immediately.
- Command routing is deterministic in version one; do not add an LLM routing call.
- Pocket TTS runs on CPU in a separate Python environment and process.
- Keep `requirements.txt`, its CUDA/cuDNN pairing, `numpy==1.24.3`, and Python 3.11 unchanged.
- Never interpolate dictated task text into a shell command and never use `shell=True`.
- Only configured named workflows may directly execute commands.
- Ambiguous repository, agent, or workflow resolution performs no action.
- Version one does not use screenshots, coordinate clicking, arbitrary application control, or an always-listening microphone.
- Use stdlib `unittest`; do not add test packages to the verified Whisper environment.

---

## File Map

### New production files

- `src/helene/__init__.py` — package exports.
- `src/helene/models.py` — shared immutable command, resolution, plan, and result dataclasses.
- `src/helene/routing.py` — explicit-command and wake-prefix routing.
- `src/helene/config.py` — Helene configuration loading and validation.
- `src/helene/registry.py` — repository, agent, and workflow resolution.
- `src/helene/parser.py` — deterministic natural-language command parser.
- `src/helene/planner.py` — converts parsed intent plus resolutions into a bounded action plan.
- `src/helene/windows_executor.py` — safe Windows Terminal, VS Code, focus, paste, and workflow execution.
- `src/helene/protocol.py` — bounded length-prefixed JSON frame functions.
- `src/helene/logging.py` — best-effort local JSONL command logging.
- `src/helene/broker.py` — orchestration of parser, planner, executor, logger, and TTS.
- `src/helene/broker_worker.py` — broker subprocess entrypoint.
- `src/helene/client.py` — Qt host handle for the broker subprocess.
- `src/helene/tts_client.py` — broker handle for the isolated TTS subprocess.
- `src/helene/tts_worker.py` — Pocket TTS model/voice lifecycle and Windows playback.
- `src/helene/qt_dispatch.py` — non-blocking Qt command dispatch thread.
- `helene_config.example.yaml` — documented, machine-neutral configuration.
- `requirements-tts.txt` — isolated Pocket TTS dependency entrypoint.
- `scripts/setup-helene.ps1` — creates the TTS environment and local configuration.
- `scripts/smoke-test-helene.py` — dry-run integration smoke test.
- `docs/HELENE.md` — installation, configuration, command grammar, and Windows manual checks.
- `.github/workflows/tests.yml` — lightweight pure-Python unit suite.

### Existing files modified

- `src/key_listener.py` — command chord arbitration and command callbacks.
- `src/main.py` — broker lifecycle, mode capture, transcript routing, dispatch, and speech interruption.
- `src/config_schema.yaml` — `helene_options.command_activation_key` and `wake_prefix`.
- `src/config.yaml`, `src/config.gpu.yaml`, `src/config.cpu.yaml` — active defaults for the two Helene entry points.
- `.gitignore` — local Helene config, logs, TTS environment, and generated audio.
- `README.md` — concise feature and setup entrypoint.

### Tests

- `tests/test_routing.py`
- `tests/test_hotkeys.py`
- `tests/test_helene_config.py`
- `tests/test_registry.py`
- `tests/test_parser.py`
- `tests/test_planner.py`
- `tests/test_windows_executor.py`
- `tests/test_protocol.py`
- `tests/test_broker.py`
- `tests/test_tts_client.py`

---

### Task 1: Pure transcript routing

**Files:**
- Create: `src/helene/__init__.py`
- Create: `src/helene/models.py`
- Create: `src/helene/routing.py`
- Create: `tests/test_routing.py`

**Interfaces:**
- Produces: `RouteDecision(kind: str, text: str, source: str)`
- Produces: `route_transcript(text: str, explicit_command: bool, wake_prefix: str = "Helene") -> RouteDecision`
- Consumes: no project state; this unit remains importable without PyQt, CUDA, or Windows APIs.

- [ ] **Step 1: Write the failing routing tests**

```python
# tests/test_routing.py
import unittest

from helene.routing import route_transcript


class RouteTranscriptTests(unittest.TestCase):
    def test_plain_pause_dictation_is_byte_for_byte_unchanged(self):
        decision = route_transcript(
            "Run the tests in Floodstream. ",
            explicit_command=False,
            wake_prefix="Helene",
        )
        self.assertEqual("dictation", decision.kind)
        self.assertEqual("Run the tests in Floodstream. ", decision.text)
        self.assertEqual("pause", decision.source)

    def test_ctrl_pause_routes_the_whole_transcript_as_a_command(self):
        decision = route_transcript(
            "Open Floodstream in Claude. ",
            explicit_command=True,
            wake_prefix="Helene",
        )
        self.assertEqual("command", decision.kind)
        self.assertEqual("Open Floodstream in Claude.", decision.text)
        self.assertEqual("ctrl_pause", decision.source)

    def test_wake_prefix_is_case_insensitive_and_strips_punctuation(self):
        decision = route_transcript(
            "hELene, open Floodstream in Claude. ",
            explicit_command=False,
            wake_prefix="Helene",
        )
        self.assertEqual("command", decision.kind)
        self.assertEqual("open Floodstream in Claude.", decision.text)
        self.assertEqual("wake_prefix", decision.source)

    def test_wake_name_inside_a_sentence_remains_dictation(self):
        decision = route_transcript(
            "I told Helene to open Floodstream. ",
            explicit_command=False,
            wake_prefix="Helene",
        )
        self.assertEqual("dictation", decision.kind)

    def test_wake_name_without_a_command_fails_as_an_empty_command(self):
        decision = route_transcript(
            "Helene. ",
            explicit_command=False,
            wake_prefix="Helene",
        )
        self.assertEqual("command", decision.kind)
        self.assertEqual("", decision.text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify the missing package failure**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_routing -v
```

Expected: `ModuleNotFoundError: No module named 'helene'`.

- [ ] **Step 3: Implement the immutable route result and routing function**

```python
# src/helene/models.py
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    kind: str
    text: str
    source: str
```

```python
# src/helene/routing.py
import re

from helene.models import RouteDecision


def route_transcript(
    text: str,
    explicit_command: bool,
    wake_prefix: str = "Helene",
) -> RouteDecision:
    if explicit_command:
        return RouteDecision("command", text.strip(), "ctrl_pause")

    prefix = re.compile(
        rf"^\s*{re.escape(wake_prefix)}(?:\s*[,.:;!?-]\s*|\s+|$)",
        re.IGNORECASE,
    )
    match = prefix.match(text)
    if match:
        return RouteDecision("command", text[match.end():].strip(), "wake_prefix")
    return RouteDecision("dictation", text, "pause")
```

`src/helene/__init__.py` exports `RouteDecision` and `route_transcript`.

- [ ] **Step 4: Run the routing tests**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_routing -v
```

Expected: five tests pass.

- [ ] **Step 5: Commit the routing boundary**

```bash
git add src/helene/__init__.py src/helene/models.py src/helene/routing.py tests/test_routing.py
git commit -m "feat: add Helene transcript routing"
```

---

### Task 2: Dual hotkey arbitration

**Files:**
- Modify: `src/key_listener.py`
- Modify: `src/config_schema.yaml`
- Modify: `src/config.yaml`
- Modify: `src/config.gpu.yaml`
- Modify: `src/config.cpu.yaml`
- Create: `tests/test_hotkeys.py`

**Interfaces:**
- Produces: `ActivationTracker.update(key: KeyCode, event_type: InputEvent) -> tuple[str, str] | None`
- Produces KeyListener callbacks: `on_command_activate` and `on_command_deactivate`
- Preserves callbacks: `on_activate` and `on_deactivate`
- Consumes: `ConfigManager.get_config_value("helene_options", "command_activation_key")`

- [ ] **Step 1: Add failing arbitration tests**

```python
# tests/test_hotkeys.py
import unittest

from key_listener import ActivationTracker, InputEvent, KeyChord, KeyCode


class ActivationTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = ActivationTracker(
            dictation_chord=KeyChord({KeyCode.PAUSE}),
            command_chord=KeyChord({
                frozenset({KeyCode.CTRL_LEFT, KeyCode.CTRL_RIGHT}),
                KeyCode.PAUSE,
            }),
        )

    def test_pause_activates_and_releases_dictation(self):
        self.assertEqual(
            ("activate", "dictation"),
            self.tracker.update(KeyCode.PAUSE, InputEvent.KEY_PRESS),
        )
        self.assertEqual(
            ("deactivate", "dictation"),
            self.tracker.update(KeyCode.PAUSE, InputEvent.KEY_RELEASE),
        )

    def test_ctrl_then_pause_activates_command_not_dictation(self):
        self.assertIsNone(
            self.tracker.update(KeyCode.CTRL_LEFT, InputEvent.KEY_PRESS)
        )
        self.assertEqual(
            ("activate", "command"),
            self.tracker.update(KeyCode.PAUSE, InputEvent.KEY_PRESS),
        )

    def test_releasing_ctrl_first_does_not_stop_recording(self):
        self.tracker.update(KeyCode.CTRL_LEFT, InputEvent.KEY_PRESS)
        self.tracker.update(KeyCode.PAUSE, InputEvent.KEY_PRESS)
        self.assertIsNone(
            self.tracker.update(KeyCode.CTRL_LEFT, InputEvent.KEY_RELEASE)
        )
        self.assertEqual(
            ("deactivate", "command"),
            self.tracker.update(KeyCode.PAUSE, InputEvent.KEY_RELEASE),
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Verify the tracker test fails**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_hotkeys -v
```

Expected: import failure for `ActivationTracker`.

- [ ] **Step 3: Add `ActivationTracker` and wire it into `KeyListener`**

Implement `ActivationTracker` next to `KeyChord`. It owns a single
`pressed_keys` set and an `active_mode`. On a key press with no active mode it
checks the command chord before the dictation chord. Once activated, it ends
only when the base activation chord is no longer active, so releasing Ctrl
before Pause does not truncate recording.

Add these callback slots:

```python
self.callbacks = {
    "on_activate": [],
    "on_deactivate": [],
    "on_command_activate": [],
    "on_command_deactivate": [],
}
```

`KeyListener.on_input_event()` translates tracker events as follows:

```python
callback_map = {
    ("activate", "dictation"): "on_activate",
    ("deactivate", "dictation"): "on_deactivate",
    ("activate", "command"): "on_command_activate",
    ("deactivate", "command"): "on_command_deactivate",
}
```

Use the existing `parse_key_combination()` for both chords. Keep the current
activation key under `recording_options.activation_key`; load the second chord
from `helene_options.command_activation_key`.

- [ ] **Step 4: Add the two command-entry defaults to every shipped config**

Add to `src/config_schema.yaml`:

```yaml
helene_options:
  command_activation_key:
    value: ctrl+pause
    type: str
    description: "The explicit developer-command hotkey."
  wake_prefix:
    value: Helene
    type: str
    description: "A leading phrase that routes ordinary dictation as a command."
```

Add to `src/config.yaml`, `src/config.gpu.yaml`, and `src/config.cpu.yaml`:

```yaml
helene_options:
  command_activation_key: ctrl+pause
  wake_prefix: Helene
```

- [ ] **Step 5: Run hotkey and routing tests**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_hotkeys tests.test_routing -v
```

Expected: eight tests pass and plain Pause behavior remains covered.

- [ ] **Step 6: Commit dual-mode input**

```bash
git add src/key_listener.py src/config_schema.yaml src/config.yaml src/config.gpu.yaml src/config.cpu.yaml tests/test_hotkeys.py
git commit -m "feat: add Ctrl Pause command mode"
```

---

### Task 3: Helene configuration and repository registry

**Files:**
- Create: `src/helene/config.py`
- Create: `src/helene/registry.py`
- Create: `helene_config.example.yaml`
- Modify: `.gitignore`
- Create: `tests/test_helene_config.py`
- Create: `tests/test_registry.py`

**Interfaces:**
- Produces: `AgentConfig`, `WorkflowConfig`, `TTSConfig`, and `HeleneConfig`
- Produces: `load_helene_config(path: pathlib.Path) -> HeleneConfig`
- Produces: `RepositoryRegistry.scan(config: HeleneConfig) -> RepositoryRegistry`
- Produces: `RepositoryRegistry.resolve(query: str) -> Resolution`
- Extends shared models with `Repository` and `Resolution`

- [ ] **Step 1: Write failing configuration tests**

```python
# tests/test_helene_config.py
import tempfile
import unittest
from pathlib import Path

from helene.config import ConfigError, load_helene_config


VALID_CONFIG = """
enabled: true
dry_run: true
development_roots:
  - C:\\\\dev
repository_aliases:
  Sextant: financial-planning-agent
default_agent: claude
editor_executable: code
agents:
  claude:
    aliases: [claude, claude code]
    executable: claude
    args: []
    prompt_mode: keyboard
    terminal: true
    startup_delay_ms: 1200
workflows:
  four-way:
    aliases: [four way, 4 way]
    executable: 4way
    args: [review]
    terminal: true
tts:
  enabled: false
  python_executable: .venv-tts/Scripts/python.exe
  voice: alba
log_path: helene-command.jsonl
"""


class HeleneConfigTests(unittest.TestCase):
    def write_config(self, text):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "helene.local.yaml"
        path.write_text(text, encoding="utf-8")
        self.addCleanup(directory.cleanup)
        return path

    def test_loads_typed_config(self):
        config = load_helene_config(self.write_config(VALID_CONFIG))
        self.assertTrue(config.enabled)
        self.assertEqual("claude", config.default_agent)
        self.assertEqual("keyboard", config.agents["claude"].prompt_mode)
        self.assertEqual(("review",), config.workflows["four-way"].args)

    def test_rejects_unknown_default_agent(self):
        invalid = VALID_CONFIG.replace(
            "default_agent: claude",
            "default_agent: missing",
        )
        with self.assertRaisesRegex(ConfigError, "default_agent"):
            load_helene_config(self.write_config(invalid))

    def test_rejects_workflow_with_string_args(self):
        invalid = VALID_CONFIG.replace("args: [review]", "args: review")
        with self.assertRaisesRegex(ConfigError, "args"):
            load_helene_config(self.write_config(invalid))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Write failing registry tests**

```python
# tests/test_registry.py
import tempfile
import unittest
from pathlib import Path

from helene.models import Resolution
from helene.registry import RepositoryRegistry


class RepositoryRegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ("floodstream", "financial-planning-agent", "newton"):
            (self.root / name / ".git").mkdir(parents=True)

    def test_exact_directory_name_wins(self):
        registry = RepositoryRegistry.scan(
            [self.root],
            {"Sextant": "financial-planning-agent"},
        )
        result = registry.resolve("Floodstream")
        self.assertEqual("resolved", result.status)
        self.assertEqual("floodstream", result.value.name)

    def test_explicit_alias_wins(self):
        registry = RepositoryRegistry.scan(
            [self.root],
            {"Sextant": "financial-planning-agent"},
        )
        result = registry.resolve("Sextant")
        self.assertEqual("financial-planning-agent", result.value.name)

    def test_ambiguous_fuzzy_match_fails_closed(self):
        (self.root / "newton-lab" / ".git").mkdir(parents=True)
        registry = RepositoryRegistry.scan([self.root], {})
        result = registry.resolve("newt")
        self.assertEqual("ambiguous", result.status)
        self.assertGreaterEqual(len(result.candidates), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Implement typed configuration with explicit validation**

Use frozen dataclasses and reject:

- missing or non-list `development_roots`;
- a `default_agent` not present in `agents`;
- a missing or non-string `editor_executable`;
- agent `prompt_mode` outside `argument` or `keyboard`;
- non-list `args`;
- workflows without an executable; and
- a TTS block without `python_executable` when enabled.

Do not silently coerce a string into an argument list. Convert valid list values
to tuples so downstream action construction is immutable.

- [ ] **Step 4: Implement bounded repository scanning and resolution**

Add to `src/helene/models.py`:

```python
@dataclass(frozen=True)
class Repository:
    name: str
    path: Path


@dataclass(frozen=True)
class Resolution:
    status: str
    value: object | None = None
    candidates: tuple[str, ...] = ()
    message: str = ""
```

`RepositoryRegistry.scan()` checks each configured root itself and its immediate
children for `.git`; it does not recursively traverse `node_modules`, virtual
environments, or the entire drive. Resolution order is explicit alias, exact
normalized repository name, then `difflib.SequenceMatcher` fuzzy score.
Require a score of at least `0.72`; treat candidates within `0.08` of the best
score as ambiguous.

- [ ] **Step 5: Add the machine-neutral example configuration**

`helene_config.example.yaml` contains:

```yaml
enabled: true
dry_run: true
development_roots:
  - C:\dev
repository_aliases:
  Sextant: financial-planning-agent
  FPA: financial-planning-agent
  My Day: my-day
  Factory: shepherd-factory
default_agent: claude
editor_executable: code
agents:
  claude:
    aliases: [claude, claude code]
    executable: claude
    args: []
    prompt_mode: keyboard
    terminal: true
    startup_delay_ms: 1200
  antigravity:
    aliases: [antigravity, anti gravity]
    executable: antigravity
    args: []
    prompt_mode: keyboard
    terminal: true
    startup_delay_ms: 1200
  grok:
    aliases: [grok, grok build]
    executable: grok
    args: []
    prompt_mode: keyboard
    terminal: true
    startup_delay_ms: 1200
workflows:
  four-way:
    aliases: [four way, 4 way, four-way review]
    executable: 4way
    args: [review]
    terminal: true
tts:
  enabled: true
  python_executable: .venv-tts\Scripts\python.exe
  voice: alba
log_path: helene-command.jsonl
```

Add these patterns to `.gitignore`:

```gitignore
helene.local.yaml
helene-command.jsonl
.venv-tts/
tts_output.wav
```

- [ ] **Step 6: Run configuration and registry tests**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_helene_config tests.test_registry -v
```

Expected: six tests pass.

- [ ] **Step 7: Commit configuration and discovery**

```bash
git add src/helene/models.py src/helene/config.py src/helene/registry.py helene_config.example.yaml .gitignore tests/test_helene_config.py tests/test_registry.py
git commit -m "feat: add Helene config and repo discovery"
```

---

### Task 4: Deterministic command parser

**Files:**
- Create: `src/helene/parser.py`
- Create: `tests/test_parser.py`
- Modify: `src/helene/models.py`

**Interfaces:**
- Produces: `CommandIntent(kind, repository, agent, workflow, task)`
- Produces: `ParseResult(intent: CommandIntent | None, error: str)`
- Produces: `parse_command(text: str) -> ParseResult`
- Consumes: a wake-prefix-free, stripped command from `route_transcript()`.

- [ ] **Step 1: Write the supported-grammar tests**

```python
# tests/test_parser.py
import unittest

from helene.parser import parse_command


class CommandParserTests(unittest.TestCase):
    def assert_intent(self, text, kind, repository=None, agent=None, task=None, workflow=None):
        result = parse_command(text)
        self.assertIsNone(result.error)
        self.assertEqual(kind, result.intent.kind)
        self.assertEqual(repository, result.intent.repository)
        self.assertEqual(agent, result.intent.agent)
        self.assertEqual(task, result.intent.task)
        self.assertEqual(workflow, result.intent.workflow)

    def test_open_repo_in_agent(self):
        self.assert_intent(
            "open Floodstream in Claude",
            "launch_agent",
            repository="Floodstream",
            agent="Claude",
        )

    def test_use_agent_on_repo_with_task(self):
        self.assert_intent(
            "use Antigravity on Sextant and review the current changes",
            "launch_agent",
            repository="Sextant",
            agent="Antigravity",
            task="review the current changes",
        )

    def test_ask_agent_to_do_task_in_repo(self):
        self.assert_intent(
            "ask Grok to investigate the failing tests in My Day",
            "launch_agent",
            repository="My Day",
            agent="Grok",
            task="investigate the failing tests",
        )

    def test_run_workflow_on_repo(self):
        self.assert_intent(
            "run four way on Floodstream",
            "run_workflow",
            repository="Floodstream",
            workflow="four way",
        )

    def test_switch_to_existing_agent_session(self):
        self.assert_intent(
            "switch to Claude in Newton",
            "focus_session",
            repository="Newton",
            agent="Claude",
        )

    def test_open_factory_is_named_target(self):
        self.assert_intent(
            "open the Factory",
            "open_repository",
            repository="Factory",
        )

    def test_unknown_command_returns_error(self):
        result = parse_command("make everything excellent")
        self.assertIsNone(result.intent)
        self.assertIn("did not recognize", result.error)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Verify parser tests fail**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_parser -v
```

Expected: missing `helene.parser`.

- [ ] **Step 3: Add command result models**

```python
@dataclass(frozen=True)
class CommandIntent:
    kind: str
    repository: str | None = None
    agent: str | None = None
    workflow: str | None = None
    task: str | None = None


@dataclass(frozen=True)
class ParseResult:
    intent: CommandIntent | None = None
    error: str | None = None
```

- [ ] **Step 4: Implement ordered anchored patterns**

Normalize only outer whitespace and a final sentence punctuation mark. Keep task
wording and capitalization intact. Check patterns in this order:

1. `ask <agent> to <task> in|on <repository>`
2. `use <agent> on|in <repository> and <task>`
3. `open <repository> in|with <agent>`
4. `switch to <agent> in|on <repository>`
5. `run <workflow> on|in <repository>`
6. `open the <repository>`
7. `open <repository>`

Return a specific empty-command error for `""`. Return a generic unrecognized
error for all nonmatching text.

- [ ] **Step 5: Run parser tests**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_parser -v
```

Expected: seven tests pass.

- [ ] **Step 6: Commit the deterministic grammar**

```bash
git add src/helene/models.py src/helene/parser.py tests/test_parser.py
git commit -m "feat: parse Helene developer commands"
```

---

### Task 5: Resolution planner and safe Windows executor

**Files:**
- Create: `src/helene/planner.py`
- Create: `src/helene/windows_executor.py`
- Modify: `src/helene/models.py`
- Create: `tests/test_planner.py`
- Create: `tests/test_windows_executor.py`

**Interfaces:**
- Produces: `ActionPlan(kind, repository, agent, workflow, prompt, display_name)`
- Produces: `PlanResult(plan: ActionPlan | None, error: str, candidates: tuple[str, ...])`
- Produces: `CommandPlanner.plan(intent: CommandIntent) -> PlanResult`
- Produces: `WindowsExecutor.execute(plan: ActionPlan, dry_run: bool) -> BrokerResult`
- Produces: `WindowsExecutor.stop_current_speech()` only as an injected callback boundary; speech implementation remains in Task 7.
- Consumes: typed config, repository registry, and parser intent.

- [ ] **Step 1: Write planner failure-closed tests**

```python
# tests/test_planner.py
import unittest
from pathlib import Path

from helene.models import CommandIntent, Repository, Resolution
from helene.planner import CommandPlanner


class FakeResolver:
    def __init__(self, results):
        self.results = results

    def resolve(self, query):
        return self.results[query]


class CommandPlannerTests(unittest.TestCase):
    def test_resolves_repo_and_agent_without_altering_prompt(self):
        repo = Repository("floodstream", Path("C:/dev/floodstream"))
        planner = CommandPlanner(
            repositories=FakeResolver({
                "Floodstream": Resolution("resolved", repo),
            }),
            agents=FakeResolver({
                "Claude": Resolution("resolved", "claude"),
            }),
            workflows=FakeResolver({}),
            default_agent="claude",
        )
        result = planner.plan(CommandIntent(
            kind="launch_agent",
            repository="Floodstream",
            agent="Claude",
            task="Review $env:PATH && do not rewrite this text",
        ))
        self.assertIsNone(result.error)
        self.assertEqual(repo, result.plan.repository)
        self.assertEqual("claude", result.plan.agent)
        self.assertEqual(
            "Review $env:PATH && do not rewrite this text",
            result.plan.prompt,
        )

    def test_ambiguous_repository_produces_no_plan(self):
        planner = CommandPlanner(
            repositories=FakeResolver({
                "Newton": Resolution(
                    "ambiguous",
                    candidates=("newton", "newton-lab"),
                ),
            }),
            agents=FakeResolver({
                "Claude": Resolution("resolved", "claude"),
            }),
            workflows=FakeResolver({}),
            default_agent="claude",
        )
        result = planner.plan(CommandIntent(
            kind="launch_agent",
            repository="Newton",
            agent="Claude",
        ))
        self.assertIsNone(result.plan)
        self.assertEqual(("newton", "newton-lab"), result.candidates)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Write safe command-vector tests**

```python
# tests/test_windows_executor.py
import unittest
from pathlib import Path
from unittest.mock import Mock

from helene.config import AgentConfig, WorkflowConfig
from helene.models import ActionPlan, Repository
from helene.windows_executor import WindowsExecutor


class WindowsExecutorTests(unittest.TestCase):
    def test_agent_prompt_is_not_shell_interpolated(self):
        popen = Mock()
        executor = WindowsExecutor(
            agents={
                "claude": AgentConfig(
                    name="claude",
                    aliases=("claude",),
                    executable="claude",
                    args=(),
                    prompt_mode="argument",
                    terminal=True,
                    startup_delay_ms=0,
                )
            },
            workflows={},
            popen=popen,
            sleep=lambda _: None,
        )
        repo = Repository("floodstream", Path("C:/dev/floodstream"))
        plan = ActionPlan(
            kind="launch_agent",
            repository=repo,
            agent="claude",
            prompt='review && echo "not shell"',
            display_name="Claude in floodstream",
        )
        result = executor.execute(plan, dry_run=False)
        argv = popen.call_args.args[0]
        self.assertIn('review && echo "not shell"', argv)
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertTrue(result.ok)

    def test_dry_run_never_calls_popen(self):
        popen = Mock()
        executor = WindowsExecutor(agents={}, workflows={}, popen=popen)
        result = executor.execute(
            ActionPlan(kind="open_repository", display_name="Factory"),
            dry_run=True,
        )
        popen.assert_not_called()
        self.assertTrue(result.ok)
        self.assertEqual("dry_run", result.code)

    def test_open_repository_uses_editor_argument_vector(self):
        popen = Mock()
        executor = WindowsExecutor(
            agents={},
            workflows={},
            editor_executable="code",
            popen=popen,
        )
        repo = Repository("floodstream", Path("C:/dev/floodstream"))
        result = executor.execute(
            ActionPlan(
                kind="open_repository",
                repository=repo,
                display_name="floodstream",
            ),
            dry_run=False,
        )
        self.assertEqual(
            ["code", str(repo.path)],
            popen.call_args.args[0],
        )
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Add action and result dataclasses**

```python
@dataclass(frozen=True)
class ActionPlan:
    kind: str
    repository: Repository | None = None
    agent: str | None = None
    workflow: str | None = None
    prompt: str | None = None
    display_name: str = ""


@dataclass(frozen=True)
class PlanResult:
    plan: ActionPlan | None = None
    error: str | None = None
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrokerResult:
    ok: bool
    code: str
    spoken_text: str
    detail: str = ""
```

- [ ] **Step 4: Implement resolution planning**

`CommandPlanner` resolves every non-null query through its matching registry.
It uses the configured default agent only when the intent omits an agent.
Any `missing` resolution returns
`PlanResult(plan=None, error="not found", candidates=())`. Any ambiguous
resolution returns `PlanResult(plan=None, error="ambiguous",
candidates=resolution.candidates)`. It never repairs, summarizes, or otherwise
transforms `intent.task`.

- [ ] **Step 5: Implement safe Windows execution**

Use `subprocess.Popen(argv, cwd=repository.path)` with a list argument and no
`shell` keyword. Build visible terminal launches as:

```python
[
    "wt.exe",
    "new-tab",
    "--title",
    f"Helene · {agent.name} · {repository.name}",
    "-d",
    str(repository.path),
    agent.executable,
    *rendered_args,
]
```

For `prompt_mode: argument`, append the prompt as one argument after rendering
configured static args. For `prompt_mode: keyboard`, start the agent without
the prompt, wait `startup_delay_ms`, locate the unique title through
`pygetwindow.getWindowsWithTitle`, activate it, copy the exact prompt with
`pyperclip.copy`, then press Ctrl+V and Enter through pynput.

Maintain an in-memory map keyed by `(agent, repository.path)` to terminal
titles. Before launching a new session, verify a mapped title still resolves to
a window. `focus_session` fails with code `session_missing` if no valid mapped
session exists.

Named workflows use only the configured executable and static argument tuple.
They receive the repository path only as `cwd`; dictated text never becomes a
workflow argument.

`open_repository` launches
`[config.editor_executable, str(repository.path)]` with the repository as
`cwd`. The editor executable is configuration, while the repository path is a
separate argument.

Keep Windows-only imports inside methods so pure planning and dry-run tests run
on Linux CI.

- [ ] **Step 6: Run planner and executor tests**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_planner tests.test_windows_executor -v
```

Expected: five tests pass.

- [ ] **Step 7: Commit bounded execution**

```bash
git add src/helene/models.py src/helene/planner.py src/helene/windows_executor.py tests/test_planner.py tests/test_windows_executor.py
git commit -m "feat: plan and execute bounded developer actions"
```

---

### Task 6: Framed broker process, logging, and dry-run integration

**Files:**
- Create: `src/helene/protocol.py`
- Create: `src/helene/logging.py`
- Create: `src/helene/broker.py`
- Create: `src/helene/broker_worker.py`
- Create: `src/helene/client.py`
- Create: `tests/test_protocol.py`
- Create: `tests/test_broker.py`

**Interfaces:**
- Produces: `read_json(stream, max_bytes: int = 1_048_576) -> dict | None`
- Produces: `write_json(stream, payload: dict) -> None`
- Produces: `CommandBroker.handle(text: str) -> BrokerResult`
- Produces: `BrokerClient.execute(text: str) -> BrokerResult`
- Produces: `BrokerClient.stop_speech() -> None`
- Produces: `BrokerClient.close() -> None`
- Consumes: parser, planner, executor, typed config, and optional TTS client.

- [ ] **Step 1: Add frame-boundary tests**

```python
# tests/test_protocol.py
import io
import struct
import unittest

from helene.protocol import ProtocolError, read_json, write_json


class ProtocolTests(unittest.TestCase):
    def test_json_round_trip(self):
        stream = io.BytesIO()
        write_json(stream, {"action": "execute", "text": "open Floodstream"})
        stream.seek(0)
        self.assertEqual(
            {"action": "execute", "text": "open Floodstream"},
            read_json(stream),
        )

    def test_rejects_oversized_frame_before_reading_body(self):
        stream = io.BytesIO(struct.pack("!I", 1_048_577))
        with self.assertRaisesRegex(ProtocolError, "too large"):
            read_json(stream)

    def test_clean_eof_returns_none(self):
        self.assertIsNone(read_json(io.BytesIO()))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add broker behavior tests with fakes**

```python
# tests/test_broker.py
import unittest
from unittest import mock

from helene.broker import CommandBroker
from helene.models import BrokerResult, ParseResult


class FakeParser:
    def __init__(self, result):
        self.result = result

    def __call__(self, text):
        return self.result


class BrokerTests(unittest.TestCase):
    def test_parse_failure_does_not_call_executor(self):
        executor = mock.Mock()
        broker = CommandBroker(
            parser=FakeParser(ParseResult(error="I did not recognize that command.")),
            planner=mock.Mock(),
            executor=executor,
            logger=mock.Mock(),
            tts=None,
            dry_run=False,
        )
        result = broker.handle("make everything excellent")
        self.assertFalse(result.ok)
        self.assertEqual("parse_error", result.code)
        executor.execute.assert_not_called()

    def test_tts_failure_does_not_reverse_successful_execution(self):
        planned = mock.Mock(plan="plan", error=None, candidates=())
        planner = mock.Mock()
        planner.plan.return_value = planned
        executor = mock.Mock()
        executor.execute.return_value = BrokerResult(
            True,
            "started",
            "Claude started in Floodstream.",
        )
        tts = mock.Mock()
        tts.speak.side_effect = RuntimeError("tts unavailable")
        broker = CommandBroker(
            parser=FakeParser(ParseResult(intent="intent")),
            planner=planner,
            executor=executor,
            logger=mock.Mock(),
            tts=tts,
            dry_run=False,
        )
        result = broker.handle("open Floodstream in Claude")
        self.assertTrue(result.ok)
        self.assertIn("tts unavailable", result.detail)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Implement bounded framed JSON**

Use four-byte unsigned big-endian lengths. Reject zero-length, oversized,
non-object JSON, truncated headers, and truncated bodies with `ProtocolError`.
`write_json()` uses compact UTF-8 JSON and flushes the stream.

- [ ] **Step 4: Implement best-effort JSONL logging**

`CommandLogger.record()` writes one object per line with:

```json
{
  "timestamp": "2026-07-28T12:34:56.789012-04:00",
  "command": "open Floodstream in Claude",
  "code": "started",
  "ok": true,
  "duration_ms": 142,
  "detail": ""
}
```

Open with UTF-8 append mode for each record. Catch `OSError` inside the logger
so log failure never changes the broker result.

- [ ] **Step 5: Implement `CommandBroker.handle()`**

The method:

1. parses;
2. plans;
3. executes with configured `dry_run`;
4. asks TTS to speak `spoken_text` when nonempty;
5. appends TTS failure detail without changing a successful execution to
   failure;
6. records duration and outcome; and
7. returns `BrokerResult`.

Planner ambiguity becomes code `ambiguous` and includes candidate names in the
spoken response. Missing agents, workflows, or repositories use code
`not_found`.

- [ ] **Step 6: Implement broker worker and client lifecycle**

Worker actions:

- `execute` with string `text`;
- `stop_speech`;
- `shutdown`.

On startup the worker loads `helene.local.yaml`, constructs registries,
planner, executor, logger, and optional TTS client, then writes
`{"status": "ready"}`.

`BrokerClient` launches:

```python
[
    sys.executable,
    "-u",
    os.path.join(source_root, "helene", "broker_worker.py"),
    "--config",
    config_path,
]
```

Use `subprocess.CREATE_NO_WINDOW` on Windows. Serialize request/response pairs
through a `threading.Lock`. `close()` sends shutdown, waits two seconds, then
kills only its own still-running child.

- [ ] **Step 7: Run protocol and broker tests**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_protocol tests.test_broker -v
```

Expected: five tests pass.

- [ ] **Step 8: Commit the isolated broker**

```bash
git add src/helene/protocol.py src/helene/logging.py src/helene/broker.py src/helene/broker_worker.py src/helene/client.py tests/test_protocol.py tests/test_broker.py
git commit -m "feat: add isolated Helene command broker"
```

---

### Task 7: Isolated local Pocket TTS

**Files:**
- Create: `requirements-tts.txt`
- Create: `src/helene/tts_client.py`
- Create: `src/helene/tts_worker.py`
- Create: `tests/test_tts_client.py`
- Modify: `src/helene/broker_worker.py`

**Interfaces:**
- Produces: `TTSClient.speak(text: str) -> None`
- Produces: `TTSClient.stop() -> None`
- Produces: `TTSClient.close() -> None`
- Worker actions: `speak`, `stop`, and `shutdown`
- Consumes: `TTSConfig.python_executable`, `TTSConfig.voice`, and framed JSON.

- [ ] **Step 1: Write failing TTS client lifecycle tests**

```python
# tests/test_tts_client.py
import io
import unittest
from unittest import mock

from helene.protocol import write_json
from helene.tts_client import TTSClient


class FakeProcess:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        write_json(self.stdout, {"status": "ready"})
        self.stdout.seek(0)
        self.stderr = io.BytesIO()
        self.returncode = None

    def poll(self):
        return None


class TTSClientTests(unittest.TestCase):
    def test_launches_configured_python_not_whisper_python(self):
        process = FakeProcess()
        popen = mock.Mock(return_value=process)
        TTSClient(
            python_executable=r".venv-tts\Scripts\python.exe",
            voice="alba",
            popen=popen,
        )
        argv = popen.call_args.args[0]
        self.assertEqual(r".venv-tts\Scripts\python.exe", argv[0])
        self.assertIn("tts_worker.py", argv[2])
        self.assertEqual("alba", argv[-1])

    def test_empty_speech_is_ignored(self):
        client = TTSClient.__new__(TTSClient)
        client._request = mock.Mock()
        client.speak("   ")
        client._request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add the isolated TTS requirement**

```text
pocket-tts==2.1.0
```

Do not add Pocket TTS, PyTorch, or a newer NumPy to `requirements.txt`.

- [ ] **Step 3: Implement the TTS worker with a hot model and voice**

At worker startup:

```python
from pocket_tts import TTSModel

model = TTSModel.load_model()
voice_state = model.get_state_for_audio_prompt(args.voice)
```

For `speak`, generate a 1-D PCM tensor, write it to a stable
`tts_output.wav` using `scipy.io.wavfile.write`, then call:

```python
winsound.PlaySound(
    output_path,
    winsound.SND_ASYNC | winsound.SND_FILENAME | winsound.SND_NODEFAULT,
)
```

For `stop`, call `winsound.PlaySound(None, 0)`. Because playback is asynchronous,
the worker remains able to read stop requests. For `shutdown`, stop playback
before exiting. Return an acknowledgment for every request.

- [ ] **Step 4: Implement `TTSClient`**

Launch the worker with the configured TTS-environment Python, not
`sys.executable`. Reuse `read_json` and `write_json`. Wait for the ready frame.
Serialize requests with a lock. Treat a dead child or an error acknowledgment
as `RuntimeError`. `close()` uses the same two-second graceful-then-kill pattern
as the broker client.

- [ ] **Step 5: Connect optional TTS in the broker worker**

When `config.tts.enabled` is true, construct `TTSClient`; otherwise pass `None`.
If startup fails, log the failure to stderr and continue with `tts=None` so
developer commands remain available without speech.

- [ ] **Step 6: Run TTS and broker tests**

Run in the Whisper environment; the real Pocket package is not imported by
these mocked tests:

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_tts_client tests.test_broker -v
```

Expected: four tests pass.

- [ ] **Step 7: Commit local voice output**

```bash
git add requirements-tts.txt src/helene/tts_client.py src/helene/tts_worker.py src/helene/broker_worker.py tests/test_tts_client.py
git commit -m "feat: add isolated Pocket TTS voice"
```

---

### Task 8: Qt host integration without dictation regression

**Files:**
- Create: `src/helene/qt_dispatch.py`
- Modify: `src/main.py`
- Modify: `src/key_listener.py`
- Create: `tests/test_host_policy.py`
- Modify: `src/helene/routing.py`

**Interfaces:**
- Produces: `HostAction(kind: str, text: str)`
- Produces: `decide_host_action(result: str, explicit_command: bool, wake_prefix: str) -> HostAction`
- Produces: `CommandDispatchThread(client: BrokerClient, text: str)`
- Consumes: KeyListener command callbacks, broker client, routing decision, and existing `ResultThread.resultSignal`.

- [ ] **Step 1: Write the host-policy regression tests**

```python
# tests/test_host_policy.py
import unittest

from helene.routing import decide_host_action


class HostPolicyTests(unittest.TestCase):
    def test_plain_pause_types_exact_existing_result(self):
        action = decide_host_action(
            "Review the changes. ",
            explicit_command=False,
            wake_prefix="Helene",
        )
        self.assertEqual("type", action.kind)
        self.assertEqual("Review the changes. ", action.text)

    def test_ctrl_pause_dispatches_without_typing(self):
        action = decide_host_action(
            "Review the changes. ",
            explicit_command=True,
            wake_prefix="Helene",
        )
        self.assertEqual("dispatch", action.kind)

    def test_wake_prefix_dispatches_without_typing(self):
        action = decide_host_action(
            "Helene, review the changes. ",
            explicit_command=False,
            wake_prefix="Helene",
        )
        self.assertEqual("dispatch", action.kind)
        self.assertEqual("review the changes.", action.text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add the pure host policy**

`HostAction` is a frozen dataclass. `decide_host_action()` calls
`route_transcript()` and maps `dictation` to `type` and `command` to `dispatch`.
Keep this pure so the regression is testable without importing PyQt.

- [ ] **Step 3: Implement non-blocking Qt dispatch**

`CommandDispatchThread(QThread)` exposes:

```python
resultSignal = pyqtSignal(object)
```

Its `run()` calls `client.execute(text)` and emits a `BrokerResult`. Convert
unexpected exceptions to a failure `BrokerResult` with code
`broker_unavailable`.

- [ ] **Step 4: Wire command lifecycle into `WhisperWriterApp`**

During component initialization:

- load `helene.local.yaml` only if it exists;
- create `BrokerClient` when Helene is enabled;
- set `self.explicit_command = False`;
- set `self.command_thread = None`;
- register `on_command_activate` and `on_command_deactivate`.

At either activation:

- ask the broker to stop speech on a short background call;
- set `explicit_command` to the matching mode before recording starts; and
- call the existing recording start path.

Both deactivation callbacks stop hold-to-record through one shared method.

In `on_transcription_complete(result)`:

1. capture and immediately reset `self.explicit_command`;
2. call `decide_host_action`;
3. for `type`, execute the existing `self.input_simulator.typewrite(result)`
   path unchanged;
4. for `dispatch`, do not type; reject an empty command with a tray message;
5. start one `CommandDispatchThread`; and
6. re-arm the key listener independently of broker completion.

When dispatch finishes, show the `spoken_text` or error through
`QSystemTrayIcon.showMessage`. The broker has already initiated speech.

In cleanup, stop the key listener, wait briefly for an active command thread,
close the broker client, then clean up input simulation.

- [ ] **Step 5: Run all pure tests**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Expected: the complete suite passes, including the exact ordinary-dictation
regression.

- [ ] **Step 6: Run syntax compilation without importing CUDA or PyQt**

Run:

```powershell
python -m compileall -q src tests
```

Expected: exit code 0.

- [ ] **Step 7: Commit host integration**

```bash
git add src/main.py src/key_listener.py src/helene/qt_dispatch.py src/helene/routing.py tests/test_host_policy.py
git commit -m "feat: connect Helene commands to Whisper Writer"
```

---

### Task 9: Setup, smoke checks, CI, and documentation

**Files:**
- Create: `scripts/setup-helene.ps1`
- Create: `scripts/smoke-test-helene.py`
- Create: `docs/HELENE.md`
- Create: `.github/workflows/tests.yml`
- Modify: `README.md`

**Interfaces:**
- Setup creates `.venv-tts` and `helene.local.yaml` without overwriting either.
- Smoke script exercises configuration, parse, plan, and dry-run without
  launching agents.
- CI runs only pure tests and does not install CUDA or Pocket TTS.

- [ ] **Step 1: Add the PowerShell setup script**

The script:

1. verifies `py -3.11`;
2. creates `.venv-tts` only when absent;
3. runs `.venv-tts\Scripts\python.exe -m pip install -r requirements-tts.txt`;
4. copies `helene_config.example.yaml` to `helene.local.yaml` only when absent;
5. prints the exact fields the operator must edit; and
6. runs a one-sentence Pocket TTS check through `tts_worker.py`.

Use `$ErrorActionPreference = "Stop"` and `Join-Path`; do not assume the caller
started in the repository directory.

- [ ] **Step 2: Add a deterministic dry-run smoke script**

The script accepts `--config`, loads config, requires `dry_run: true`, builds
the real registry/parser/planner/executor stack, and runs:

```text
open Floodstream in Claude
run four way on Floodstream
open the Factory
```

It prints one JSON result per line and exits nonzero if any result is not
successful. It never flips dry-run off and never launches a process.

- [ ] **Step 3: Add lightweight CI**

```yaml
name: tests

on:
  push:
  pull_request:

jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install PyYAML==6.0.1
      - run: PYTHONPATH=src python -m unittest discover -s tests -v
      - run: python -m compileall -q src tests
```

Keep Windows-only imports lazy so this job remains green.

- [ ] **Step 4: Document installation and real Windows smoke checks**

`docs/HELENE.md` documents:

- why the Whisper and Pocket environments are separate;
- copying and editing `helene.local.yaml`;
- real executable-name discovery with `Get-Command claude`,
  `Get-Command antigravity`, `Get-Command grok`, `Get-Command code`, and
  `Get-Command wt`;
- `Pause`, `Ctrl+Pause`, and leading “Helene” behavior;
- all supported command forms;
- repository aliases;
- `dry_run`;
- choosing a built-in Pocket voice or local reference WAV;
- local JSONL history;
- failure codes and recovery; and
- the eight manual smoke checks from the design specification.

Update `README.md` with a short “Helene developer control” section linking to
the full guide. Do not dilute the existing CUDA troubleshooting material.

- [ ] **Step 5: Run the complete validation**

Run:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m compileall -q src tests
python scripts/smoke-test-helene.py --config helene.local.yaml
```

Expected:

- all unit tests pass;
- compilation exits 0;
- the smoke script emits three successful `dry_run` JSON results; and
- no agent, terminal, editor, or workflow starts.

- [ ] **Step 6: Inspect the final diff for frozen-environment drift**

Run:

```bash
git diff origin/main...HEAD -- requirements.txt requirements-cpu.txt
git diff --check
git status -sb
```

Expected:

- no diff for either verified Whisper requirements file;
- no whitespace errors; and
- only intended Helene implementation files remain.

- [ ] **Step 7: Commit setup and documentation**

```bash
git add scripts/setup-helene.ps1 scripts/smoke-test-helene.py docs/HELENE.md .github/workflows/tests.yml README.md
git commit -m "docs: add Helene setup and validation"
```

---

## Final Review Gate

- [ ] Confirm every acceptance criterion in the design has a passing automated
  test, dry-run result, or explicit Windows manual check.
- [ ] Confirm ordinary Pause dictation still sends the original post-processed
  string to `InputSimulator.typewrite()` without trimming.
- [ ] Confirm Ctrl+Pause is captured before recording and does not also trigger
  the regular Pause callback.
- [ ] Confirm the wake prefix matches only at the beginning.
- [ ] Confirm no path sends dictated task text through `shell=True`, PowerShell
  `-Command`, `cmd /c`, or a formatted command string.
- [ ] Confirm only configured named workflows can execute directly.
- [ ] Confirm broker failure cannot disable ordinary dictation.
- [ ] Confirm TTS failure cannot reverse a successful developer action.
- [ ] Confirm broker and TTS children are closed on app shutdown.
- [ ] Confirm Pocket TTS and its dependency graph remain outside the Whisper
  environment.
- [ ] Confirm `helene.local.yaml`, command history, TTS output, and `.venv-tts`
  are gitignored.
- [ ] Push `agent/helene-voice-controller` and update draft PR #1 with the full
  implementation summary and validation results.
