# Plan: vibekeys fixes — pad MIC, doubled dictation, Shift latch, drift port, AHK scoping

Date: 2026-11-20 · Repo: whisper-writer-gpu-wake · Base: main (358e85f) · Policy: review-routing-2026-10-07-v1
Rev 5 (2026-11-22) - adds chunk c0 `swallow-key`, which goes FIRST. Neill approved this cut in session on
2026-11-22 with no fourth plan board (plan cap spent at b3). Revs 1-4 folded in plan boards 1-3.

## Problem

Three live symptoms on the whisper-writer-wake install (`C:\Tools\whisper-writer-wake`), all Neill-confirmed, plus
unreviewed live-only code drift and an approved-but-unapplied AHK change.

## Evidence (captured 2026-11-20)

1. **Pad MIC "dead"** — capture4 (`%TEMP%\opencode\vibekeys\capture4.txt`): every pad MIC tap emits a physical
   `PAUSE` (vk=0x13); whisper then injects `LSHIFT release` + `SPACE down/up` — the defensive Shift release from
   `_typewrite_pynput` followed by typing a **lone space**. The pad binding (btn0=pause, uploaded 11-18) is alive;
   the button transcribes silence and types an invisible space, which feels like "nothing".
2. **Doubled dictation** — history.log lines like `Testing, one, two, three. Testing, one, two, three.` in ONE line
   (single transcribe result). `is_hallucination` loops unit sizes only to `len(words) // MAX_PHRASE_REPEATS`
   (`//3`), so an exact 2× repeat of a 3+-word sentence (8 words, unit size 4) is never tested and escapes the
   filter. This is Whisper's short-clip repetition artifact; `vad_filter: true` did not stop it.
3. **"Deletes everything"** — the defensive Shift release at `_typewrite_pynput` start clears only `Key.shift`
   (LEFT). A latched RIGHT Shift (`Key.shift_r`; capture3 shows RSHIFT events) survives it, so the next typed
   character selection-extends and REPLACES text. (Installed pynput maps `Key.shift`/`Key.shift_l` to VK.LSHIFT
   and `Key.shift_r` to VK.RSHIFT — releasing only `Key.shift` cannot clear a right-Shift latch.)
4. **Single instance confirmed** — the venv `python/pythonw` are launcher stubs fronting base Python311
   (stub+real process pairs = one app chain; ledger 2026-10-18 documented the same). NOT doubling.
5. **Live drift vs main @ 358e85f** (bidirectional — live is neither ahead nor behind as a whole):
   - live → repo: `src/input_simulation.py` (+75: real Shift+VK events, `paste` input method),
     `src/transcription.py` (+47: `is_hallucination` filter), `src/result_thread.py` (1 line: `extra_settings`
     when device is not None), `src/config_schema.yaml` (condition/vad defaults, `sound_device` `type: int`,
     `paste` option), `src/config.yaml` (`vad_filter: true`, `condition_on_previous_text: false`,
     `wake_options` section), `tests/test_hallucination_guard.py` (live-only, +6 tests), `tools/pad-upload/`
     (live-only: `ch57x_upload.py`, `final.json`, `markers.json` — pad remap tooling).
   - repo → live (live is STALE): `src/wake_setup.py` + `tests/test_wake_setup.py` — the repo has the 0-byte
     .tflite pair-repair fix (CodeRabbit cycle 2, 555d081) that the 10-18 deploy predates. These deploy to live
     in the deploy step; nothing to port back.
6. **vibekeys.ahk** — Retry (`^!+r` = Up+Enter) and YOLO (`^!+e` = Tab+Enter) fire globally; Neill approved
   terminal-only scoping (2026-11-20 session).

## Chunks (each = one behavior, one board)

### c0 `swallow-key` (added rev 5 - goes first)

**Neill's report, 2026-11-22:** holding the dictation key (pad MIC and keyboard Pause/Break, "both do it") in a
terminal prompt (Claude Code / OpenCode) "eats characters one by one"; "it's only when there's stuff that's on
the line that it deletes it"; Hey Jarvis is fine.

**Measured cause (probe run 2026-11-22, Windows Terminal 1.25.3131.0, own window, injected held Pause):**
- The held key auto-repeats (capture7: 181 physical PAUSE key-downs, 5 key-ups, about 30 per second).
- `PynputBackend.start` installs a plain `keyboard.Listener`: it observes the key and never consumes it, so
  every repeat reaches the focused window.
- Windows Terminal hands a VT-input console app one byte `0x1A` (Ctrl+Z) per Pause key-down; with the full
  kitty keyboard flags it sends `CSI 57362 ... u` per key-down; with win32-input-mode `CSI 19;69;0;1;32;1 _`.
  A prompt that binds Ctrl+Z to undo removes typed text one step per repeat. A blank line has nothing to undo.
- The wake path holds no key, so it is unaffected.

This replaces evidence item 3's explanation of the delete symptom: the right-Shift latch is UNPROVEN as its
cause (it does not explain delete-while-holding, delete-only-when-the-line-has-text, or Jarvis being fine).
c3's Shift/clipboard hardening stands or falls on its own merits, not on this symptom.

**Change (`src/key_listener.py` only - byte-identical in live and main, so c0 deploys without c1):**
- `KeyListener` derives the keys to swallow from the activation chord: only a lone allowlisted non-text key
  (Pause, Scroll Lock, F1-F20) pressed with no modifier held. Everything else swallows nothing (unchanged).
  - Character keys (letters, digits, punctuation, space, enter, tab) are never swallowed: the app's own
    typing goes through the same SendInput path, so swallowing them would eat dictated text and self-trigger.
  - A press with Ctrl/Alt/Shift/Win already down passes through (down and up), so Alt+F4, Win+Pause etc.
    keep working; repeats and the up of an already-swallowed press stay swallowed.
  - An activation string with an unknown token (e.g. `win+space`) swallows nothing.
- `PynputBackend.start` passes `win32_event_filter=` to `keyboard.Listener`. For a swallowed key the filter
  translates key-down/up to the existing `(KeyCode, InputEvent)` pair, calls `self.on_input_event(...)`
  directly, then `suppress_event()`. Direct dispatch is required: pynput 1.7.6 does not call
  `on_press`/`on_release` for a suppressed event.
- `KeyChord` set logic already folds the repeats into one activation; reused unchanged.
- No new config option, no new dependency. If the app is not running the key behaves as before.
- Verify: `tests/test_key_swallow.py` - swallowed key dispatched once and suppressed; 20 repeats = one
  activate then one deactivate on release; other keys pass through; a modifier chord swallows nothing; the
  non-Windows start path is unchanged. Full suite. Live: injected 2 s Pause hold into a test window holding
  text - the window receives zero Pause events and the app records.

**Rides with the c0 deploy (config only, live `src/config.yaml`, not the repo template):**
`recording_options.silence_duration` 900 -> 2500. Neill, 2026-11-22: "I'd like Jarvis to give me an extra
second of pause if I'm thinking ... maybe an extra second or two seconds." Hold-to-record never reads this
timer, so Pause dictation is unaffected; a Jarvis transcript lands about 1.6 s later than before.

### c1 `port-drift`
Make the repo tree match the running install (and carry the newer repo code in the same branch):
- Port verbatim from live: `src/input_simulation.py`, the `is_hallucination` block of `src/transcription.py`,
  `src/config_schema.yaml`, `tests/test_hallucination_guard.py`,
  `tools/pad-upload/{ch57x_upload.py,final.json,markers.json}`.
- Amended at code board 1: the live `paste` input method was NOT ported (unused — live runs `pynput`); its
  schema option was dropped too. Shift handling was completed here instead of c3: per-character
  `VkKeyScanW` lookup (Shift-only → real Shift + VK, else the original character), `should_continue()`
  checked before the defensive release, and all three Shift keys released. ResultThread now initializes COM
  on its own thread (key recordings may open WASAPI with the wake listener off).
- `src/result_thread.py`: port the live one-liner — and state the semantic change explicitly: repo guards
  `extra_settings` on `is_wake`, live guards on `device is not None`, which applies
  `WasapiSettings(auto_convert=True)` to key recordings on the configured device (the pad MIC path). Because
  `audio_devices.extra_settings` returns `WasapiSettings` unconditionally, a NON-WASAPI `sound_device` index
  would raise `PortAudioError` — so this chunk also changes `src/audio_devices.py` to return
  `WasapiSettings` only when the device's host API is WASAPI — detected the same way `resolve_input` does it
  (compare `query_hostapis()[hostapi]['name']` for `'WASAPI' in name`, NOT the integer hostapi index; a bare
  `== "WASAPI"` compare would silently return None for live device 15 and drop `auto_convert`). The whole
  lookup is wrapped in try/except with an explicit `0 <= device < len(query_devices())` bound (a stale index,
  an empty list, or negative-index wraparound returns no extra settings, never raises). Tests: non-WASAPI
  index → None; stale/out-of-range index → None; WASAPI index → WasapiSettings; the pre-existing
  `test_audio_devices.py`/`test_wake_listener.py` expectations get host-API mocks instead of touching real
  Windows MME indices.
- `tests/test_typewrite.py` is EXISTING repo code (3 lowercase-pynput tests) — it is not a port; c3 extends it.
- `src/config.yaml`: port `vad_filter: true`, `condition_on_previous_text: false`, and the `wake_options`
  section **with `enabled: false`** as the repo default (fresh clones without .onnx models must not warn at
  boot). `sound_device` stays `null` in the repo template (machine-specific; live keeps 15).
- `src/ui/settings_window.py`: render null numerics as empty, not `'None'` —
  `create_line_edit('' if current_value is None else str(current_value))` — AND the Save path maps an empty
  string back to `None` for `type: int`/`float` settings (`int('')` raises `ValueError`; without this, Save
  still crashes on a fresh clone with a different traceback). Required because `sound_device` becomes
  `type: int` with a `null` default.
- Verify: repo test suite (pytest — 82 existing + 6 ported = 88 green expected) + `py_compile` on changed
  files. The ported `test_hallucination_guard.py` covers `is_hallucination`; the ported `test_typewrite.py`
  covers the pynput/paste paths c2/c3 build on.

### c2 `silence-guard`
Ordering is load-bearing (plan-board-1 P1):
1. `post_process_transcription` runs `is_hallucination` on the ORIGINAL stripped text and returns `''` before
   any collapse — a 3× loop of a short phrase must still be dropped, never collapsed-and-typed
   (`'I am here. ' * 3` → `''`).
2. Collapse applies only to survivors: when the whole transcript is an exact k× repeat (k≥2) of a unit of ≥4
   words measured on the FOLDED keys — the same `_phrase_key` normalization (`[^\w\s]` stripped, casefolded)
   used for matching, NOT raw `\S+` spans, so a spaced `-` or standalone `...` never inflates the unit length
   ("Wait - one second. Wait - one second." is 6 folded words, unit 3, and must be typed whole). The reported
   doubled line is 8 folded words, unit 4, and collapses. A 2× of a 3-word unit such as
   "I don't know. I don't know." is ordinary speech and must be typed whole — it also escapes
   `is_hallucination`, which is exactly why the floor is 4 folded words. Slice offsets still come from the
   whitespace-delimited `\S+` tokens of the RAW text; the kept text is the original string up to the end of
   copy 1's last token span, so copy 1's punctuation and capitalization are preserved verbatim (a contraction's
   apostrophe is inside its token span, so no mis-slicing).
3. Small-phrase repeats that escape both the 3× loop (len//3) and the 4-word floor are caught directly in
   `is_hallucination`: reject k≥2 repeats of a `SILENCE_PHRASES` phrase or an `initial_prompt` sentence at ANY
   word count ("Thank you. Thank you." is dropped).
4. Run `is_hallucination` on the collapsed result too: a ≥4-word `SILENCE_PHRASES` phrase or a doubled
   `initial_prompt` sentence that collapses to 1× must then be dropped, not typed.
5. Every collapse is AUDITABLE where it survives `pythonw.exe`: append one JSON-encoded line to the same
   `history.log` (`{"ts": ..., "event": "COLLAPSED", "original": ..., "kept": ...}`) — JSON so a transcript
   containing tabs or newlines cannot corrupt the tab-delimited consumer format; `console_print` alone is
   discarded under `pythonw.exe`. The write is wrapped in try/except with the path injected/patchable, so a
   locked or unreadable history file can never abort `ResultThread.run()`, and pytest never pollutes the live
   `history.log`.
6. Blank transcripts never reach history: return `''` when `not transcription` BEFORE `add_trailing_space`
   appends the lone space, so empty/silence transcripts no longer log or type `' '`.
- `main.py` `on_transcription_complete`: gate ONLY the `typewrite` call and the `noise_on_completion` beep on
  `result.strip()`; the re-arm tail (`start_result_thread()` in continuous mode / `key_listener.start()`)
  ALWAYS runs — an early return there would leave the pad MIC dead after a silence artifact. History logging
  unchanged.
- Verify: unit tests — 2× clean repeat of a ≥4-word unit collapses; 2× repeat with punctuation drift collapses
  and keeps copy 1's punctuation; 2× of a 3-word unit ("I don't know. I don't know.") is typed WHOLE; 3×
  short-unit loop still fully dropped (unit ≥3 words, asserted against
  `post_process_transcription('I am here. I am here. I am here.') == ''`); 2× ≥4-word silence phrase dropped;
  2× initial-prompt sentence dropped; non-repeat untouched; collapse writes the COLLAPSED line to history.log;
  whitespace-only result types nothing but re-arms the listener (`source='key'` asserted). Full suite.

### c3 `shift-latch`
> Status after c1 board 1: the Shift-release bullet and the uppercase Shift+VK tests are DONE in c1. The
> paste/clipboard bullets below are MOOT — `_typewrite_paste` was never ported — unless paste is reintroduced.

- `input_simulation.py`: defensive release covers `Key.shift_l`, `Key.shift_r`, and `Key.shift` (all three;
  releasing an unpressed key is a no-op) at the start of `_typewrite_pynput`, AND before the Ctrl+V in
  `_typewrite_paste` (a latched Shift would turn the paste into Ctrl+Shift+V).
- Clipboard hardening on the paste path: EVERY clipboard call gets a guard — the pre-loop `pyperclip.copy(text)`,
  the in-loop `pyperclip.paste()` read-back, and the in-loop `pyperclip.copy(text)`. On any clipboard failure:
  skip the Ctrl+V entirely and return the untyped remainder — a swallowed copy failure followed by Ctrl+V would
  paste the user's STALE clipboard, which is worse than typing nothing. The ONE fallback is named per caller:
  on the key path, `main.py on_transcription_complete` falls back to `_typewrite_pynput` typing for a
  clipboard-failure remainder (and shows a tray notice if even per-key typing returns a remainder); on the wake
  path the existing tray notice stays, with its message naming the real cause (clipboard, not just focus
  change). `key_listener.start()`/re-arm stays OUTSIDE any try, so a locked clipboard can never leave the pad
  dead.
- `_typewrite_paste` cleanup while touching it: remove the pre-paste `pyperclip.copy(backup)` clobber (it
  wastes clipboard calls on success and can overwrite newer user clipboard data with stale backup if
  `copy(text)` fails — at the START of the next paste the backup is restored only if the clipboard still
  holds the transcript we put there (read-back equals the last pasted text); otherwise the backup is
  dropped, so a copy the user made in between is never overwritten), and re-check
  `should_continue()` immediately before the Ctrl+V after the retry sleeps.
- Verify: extend `tests/test_typewrite.py` (existing repo file, currently 3 lowercase-pynput tests, no
  `pressed()` on its FakeKeyboard): add uppercase Shift+VK tests and `_typewrite_paste` tests (success; copy
  failure returns remainder without Ctrl+V; all three Shift releases attempted on both paths). Full suite.
  The physical verification (a latched right Shift no longer selection-extends) is part of the live deploy
  check.

### c4 `vibekeys-terminal-only` (outside the repo — flagged)
- `C:\Users\neill\bin\vibekeys.ahk` (not version-controlled): gate Retry and YOLO handlers with
  `WinActive("ahk_exe WindowsTerminal.exe")` — NOT a bare `WinGetProcessName("A")`, which throws an uncaught
  modal `TargetError`/`OSError` when no window is active or the foreground window is elevated. Outside
  Windows Terminal the two handlers do nothing. ~10-line diff, verified live by Neill. Rides the plan review,
  not a repo board.

## Deploy (separate authorization, after chunk acceptance)

1. Snapshot: copy live `C:\Tools\whisper-writer-wake\src` in its ENTIRETY (including `src\main.py` — every
   file the deploy overwrites lives under `src\`; verified live: no root-level `main.py`) to a timestamped
   backup (`...\whisper-writer-wake\bak-<date>-src\`). Rollback = restore the backup and restart.
   **c0-only deploy (done 2026-11-22, backup `bak-20261122-102337-src`):** copied ONLY
   `src/key_listener.py`, plus the live-config `silence_duration` edit (never the repo `config.yaml`). The
   full list in step 2 applies only after c1 is accepted: before c1 ports the live-only changes, copying
   `input_simulation.py`/`transcription.py` from the repo would overwrite them.
2. (After c1 is accepted) Copy CODE files only: `input_simulation.py`, `transcription.py`, `result_thread.py`, `audio_devices.py`,
   `main.py`, `key_listener.py`, `config_schema.yaml`, `wake_setup.py`, `ui/settings_window.py`. **Never copy `config.yaml`** —
   live keeps `sound_device: 15` and `wake_options.enabled: true`. (`main.py` is required: c2's
   `on_transcription_complete` gate lives there; without it the install still types the lone space.)
3. Restart the app (one clean chain). Rollback = restore the snapshot and restart.
4. Neill re-tests: pad MIC hold = dictation; no lone spaces; no doubled lines; no delete-after-dictation;
   Retry/YOLO inert outside Windows Terminal.

## Out of scope

AirPods/Bluetooth device-hang class (deferred by Neill's 2026-10-18 ruling), Chunk 2 (AirPods toggle),
Chunk 3 (Hey Claude), pad remap changes (bindings verified alive today).
