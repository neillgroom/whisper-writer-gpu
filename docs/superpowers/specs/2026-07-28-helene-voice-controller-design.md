# Helene Developer Voice Controller — Design

Date: 2026-07-28  
Repository: `neillgroom/whisper-writer-gpu`  
Status: Approved design, pending implementation plan

## Objective

Extend Whisper Writer GPU into a voice-first developer controller without
changing its proven Windows/CUDA transcription path.

Version one lets Neill:

- continue ordinary push-to-talk dictation with `Pause`;
- issue developer commands with `Ctrl+Pause`;
- issue developer commands by beginning ordinary `Pause` dictation with
  “Helene”;
- launch or focus coding-agent sessions in a named repository;
- submit the dictated task immediately;
- run configured development workflows; and
- receive a short local spoken confirmation.

The first release is deliberately not a general-purpose visual desktop agent.

## Interaction Contract

### Ordinary dictation

Holding and releasing `Pause` behaves exactly as it does today unless the final
transcript begins with the wake prefix “Helene”. Normal transcripts continue
directly to `InputSimulator.typewrite()`.

### Explicit command

Holding `Ctrl+Pause` marks the recording as a command before transcription
begins. The resulting transcript is sent to the command broker.

### Wake-prefix command

A normal `Pause` transcript beginning with “Helene” is also sent to the command
broker. Matching is case-insensitive and tolerates punctuation immediately
after the name. The prefix is removed before parsing.

This is not an always-listening wake word. Hands-free wake-word detection is
outside version one.

### Submission

Agent tasks are submitted immediately. Helene does not stage a prompt and wait
for a second confirmation.

## Architecture

Whisper Writer remains the host application. Its Qt lifecycle, microphone
capture, hot model, GPU subprocess, transcription protocol, and normal typing
path remain intact.

The new command path contains three isolated units:

1. **Input router**
   - Tracks whether `Ctrl` is held when the activation recording begins.
   - Detects and strips the “Helene” prefix.
   - Sends ordinary transcripts to the existing typing path.
   - Sends command transcripts to the broker client.

2. **Command broker**
   - Runs outside the Qt process.
   - Communicates with the host through length-prefixed JSON frames, following
     the existing transcription-worker pattern.
   - Parses supported command forms.
   - Resolves repositories, agents, sessions, and named workflows.
   - Delegates execution to bounded adapters.
   - Returns a structured outcome containing status, spoken text, and
     diagnostic detail.

3. **Pocket TTS worker**
   - Runs as a separate local process in a separate Python environment.
   - Uses a configured built-in Pocket TTS voice or a local reference WAV.
   - Streams or plays short broker responses.
   - Can be interrupted when a new activation hotkey is pressed.

Keeping the broker and TTS dependencies outside the Qt/CUDA process avoids
destabilizing the verified Whisper dependency freeze.

## Command Model

Version one uses deterministic intent parsing. It does not make a separate LLM
call merely to decide which coding agent should receive the task. The dictated
task body is passed verbatim to the selected coding agent, which already has
the language-model capability needed to understand it.

Supported command families:

- `open <repository> [in <agent>]`
- `use <agent> on <repository> [and <task>]`
- `ask <agent> to <task> [in/on <repository>]`
- `run <workflow> [on <repository>]`
- `switch to <agent> [in <repository>]`
- `open the Factory`

Representative commands:

- “Helene, open Floodstream in Claude.”
- “Helene, use Antigravity on Sextant and review the current changes.”
- “Helene, ask Grok to investigate the failing tests in My Day.”
- “Helene, run four-way review on Floodstream.”
- “Helene, switch to Claude in Newton.”

Unknown or ambiguous commands do not execute. The broker returns a concise
spoken explanation and records diagnostic detail locally.

## Repository Resolution

The broker scans configured development roots for Git repositories. It builds
an in-memory registry from directory names and explicit aliases.

Explicit aliases take precedence. Examples:

- `Sextant` -> `financial-planning-agent`
- `FPA` -> `financial-planning-agent`
- `Factory` -> its configured project or dashboard path
- `My Day` -> `my-day`

Matching is case-insensitive. Normalized exact matches beat fuzzy matches. If
the best match is not unique, no action is taken.

## Agent Adapters

Version one includes configurable adapters for:

- Claude Code;
- Antigravity;
- Grok Build; and
- VS Code as an editor target.

Each adapter declares:

- executable or launch command;
- argument template;
- whether an initial prompt can be passed as an argument;
- whether it needs a visible Windows Terminal;
- terminal title template;
- startup/readiness delay when keyboard submission is required; and
- optional environment variables.

Where an agent accepts an initial prompt argument, the broker launches it with
an argument vector rather than a shell-interpolated command. Otherwise it
opens a uniquely titled terminal in the repository, focuses that terminal,
pastes the task, and presses Enter.

The broker maintains a runtime mapping of repository-and-agent pairs to the
sessions it launched. It focuses a valid existing session when possible and
starts a new one when necessary.

## Named Workflows

Named workflows are an allowlist in configuration, not arbitrary generated
shell commands. Each workflow declares its executable, arguments, working
directory behavior, and whether it should run in a visible terminal.

Initial intended workflows include:

- four-way review;
- repository test command;
- repository status; and
- opening the Factory.

The implementation may ship with examples, but machine-specific commands and
paths live in the local configuration.

## Configuration

A YAML configuration defines:

- development roots;
- repository aliases;
- default coding agent;
- agent adapter commands and argument templates;
- named workflows;
- terminal behavior;
- command-mode hotkey;
- wake prefix;
- Pocket TTS executable/service location;
- selected voice or reference WAV;
- spoken-response verbosity;
- log location; and
- `dry_run`.

The repository ships a documented example configuration. Machine-specific
paths remain in a gitignored local override.

## Spoken Responses

Helene speaks only concise operational outcomes, for example:

- “Claude started in Floodstream.”
- “Focused Antigravity in Sextant.”
- “Four-way review started.”
- “I found two Newton repositories.”
- “Grok Build is not configured.”

No conversational filler is added. Pressing an activation hotkey during
playback stops the current speech immediately.

Pocket TTS runs locally on CPU so the RTX 4080 remains available to the hot
Whisper model. The TTS worker uses its own environment because Pocket TTS and
the verified Whisper environment require incompatible NumPy versions.

## Execution Boundaries

- Dictated task text is never interpolated into a shell command.
- Agent prompts are delivered as process arguments or keyboard input.
- Direct workflow execution is limited to configured named workflows.
- Process launches use argument arrays and avoid `shell=True`.
- Ambiguous repository, agent, or workflow resolution fails closed.
- Version one does not inspect screenshots, click arbitrary coordinates, or
  operate arbitrary applications.

## Logging and History

Command attempts produce local structured records containing:

- timestamp;
- normalized intent;
- resolved repository, agent, or workflow;
- execution outcome;
- duration; and
- error category and diagnostic message when applicable.

Logs are gitignored. Verbatim command text remains local. Logging failure never
blocks dictation or command execution.

## Failure Behavior

- Broker unavailable: ordinary dictation continues; commands receive a visible
  tray notification and no action occurs.
- TTS unavailable: the command may still execute; the outcome is shown through
  the tray/status surface and logged.
- Agent unavailable: no terminal is opened with a broken command; Helene
  reports that the adapter is not configured or executable.
- Repository ambiguity: candidate names are reported; no action occurs.
- Window focus or keyboard submission failure: the session remains visible and
  the task is copied to the clipboard when possible.
- Child processes are closed cleanly during application shutdown.

## Validation

### Automated tests

- command-mode modifier tracking;
- wake-prefix detection and stripping;
- regression test that ordinary `Pause` transcripts still type normally;
- deterministic command parsing;
- repository alias and ambiguity resolution;
- adapter argument construction;
- workflow allowlist enforcement;
- broker framing and malformed-message handling;
- mocked process launch, window focus, clipboard, keyboard, and TTS behavior;
- child-process cleanup; and
- dry-run output.

### Windows smoke test

A documented smoke-test script verifies:

1. ordinary `Pause` dictation types into Notepad;
2. `Ctrl+Pause` resolves a command without typing it into Notepad;
3. “Helene” prefix routing works through ordinary `Pause`;
4. VS Code opens at a configured test repository;
5. a configured agent opens in a visible terminal and receives a harmless
   prompt;
6. a named harmless workflow executes;
7. a short TTS confirmation plays; and
8. pressing an activation hotkey interrupts playback.

## Out of Scope for Version One

- always-listening wake-word detection;
- screenshot interpretation;
- coordinate-based clicking;
- general browser or Office automation;
- autonomous multi-step desktop planning;
- email, messaging, or calendar actions;
- remote/mobile control;
- dynamically generated shell commands; and
- replacing the existing Whisper transcription pipeline.

## Acceptance Criteria

The release is complete when:

1. existing push-to-talk dictation remains behaviorally unchanged;
2. `Ctrl+Pause` and the “Helene” prefix both enter command mode;
3. at least Claude Code, Antigravity, and Grok Build can be represented by
   configuration and launched through the common adapter interface;
4. a named repository can be resolved and opened in VS Code;
5. a dictated task can launch or focus an agent session and submit
   immediately;
6. a configured named workflow can run;
7. Helene provides a short local Pocket TTS result without consuming Whisper's
   GPU allocation;
8. ambiguous or unknown commands perform no action;
9. dry-run mode demonstrates the complete resolved action without execution;
   and
10. the automated suite and Windows smoke test pass.
