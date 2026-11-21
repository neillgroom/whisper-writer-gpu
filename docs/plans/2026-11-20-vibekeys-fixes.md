# Plan: vibekeys fixes — pad MIC, doubled dictation, Shift latch, drift port, AHK scoping

Date: 2026-11-20 · Repo: whisper-writer-gpu-wake · Base: main (358e85f) · Policy: review-routing-2026-10-07-v1
Rev 2 — folds in every plan-board-1 finding (run 2026-11-21T02-42-08-329Z-21400).

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

### c1 `port-drift`
Make the repo tree match the running install (and carry the newer repo code in the same branch):
- Port verbatim from live: `src/input_simulation.py`, the `is_hallucination` block of `src/transcription.py`,
  the `src/result_thread.py` one-liner, `src/config_schema.yaml`, `tests/test_hallucination_guard.py`,
  `tools/pad-upload/{ch57x_upload.py,final.json,markers.json}`.
- `src/config.yaml`: port `vad_filter: true`, `condition_on_previous_text: false`, and the `wake_options`
  section **with `enabled: false`** as the repo default (fresh clones without .onnx models must not warn at
  boot). `sound_device` stays `null` in the repo template (machine-specific; live keeps 15).
- `src/ui/settings_window.py`: render null numerics as empty, not `'None'` —
  `create_line_edit('' if current_value is None else str(current_value))` — required because
  `sound_device` becomes `type: int` with a `null` default; the current `str(current_value)` renders `'None'`
  and crashes Save on a fresh clone.
- Verify: repo test suite (pytest — 82 existing + 6 ported = 88 green expected) + `py_compile` on changed
  files. The ported `test_hallucination_guard.py` covers `is_hallucination`; the ported `test_typewrite.py`
  covers the pynput/paste paths c2/c3 build on.

### c2 `silence-guard`
Ordering is load-bearing (plan-board-1 P1):
1. `post_process_transcription` runs `is_hallucination` on the ORIGINAL stripped text and returns `''` before
   any collapse — a 3× loop of a short phrase must still be dropped, never collapsed-and-typed
   (`'I am here. ' * 3` → `''`).
2. Collapse applies only to survivors: when the whole transcript is an exact k× repeat (k≥2) of a unit of ≥3
   words, keep the FIRST copy. Keys and slice offsets come from the SAME whitespace-delimited `\S+` tokens
   (punctuation/case-folded for matching only); the kept text is the original string up to the end of copy 1's
   last token span, so copy 1's punctuation and capitalization are preserved verbatim (a contraction's apostrophe
   is inside its token span, so no mis-slicing).
3. Run `is_hallucination` on the collapsed result too: a 2× `SILENCE_PHRASES` phrase or a doubled
   `initial_prompt` sentence collapses to 1× and must then be dropped, not typed.
4. Every collapse is logged (`ConfigManager.console_print`, and it is visible in the transcript log) so
   truncation is auditable.
- `main.py` `on_transcription_complete`: gate ONLY the `typewrite` call and the `noise_on_completion` beep on
  `result.strip()`; the re-arm tail (`start_result_thread()` in continuous mode / `key_listener.start()`)
  ALWAYS runs — an early return there would leave the pad MIC dead after a silence artifact. History logging
  unchanged.
- Verify: unit tests — 2× clean repeat collapses; 2× repeat with punctuation drift collapses and keeps copy 1's
  punctuation; 3× short-unit loop still fully dropped (unit ≥3 words, asserted against
  `post_process_transcription('I am here. I am here. I am here.') == ''`); 2× silence phrase dropped; 2×
  initial-prompt sentence dropped; non-repeat untouched; whitespace-only result types nothing but re-arms the
  listener (`source='key'` asserted). Full suite.

### c3 `shift-latch`
- `input_simulation.py`: defensive release covers `Key.shift_l`, `Key.shift_r`, and `Key.shift` (all three;
  releasing an unpressed key is a no-op) at the start of `_typewrite_pynput`, AND before the Ctrl+V in
  `_typewrite_paste` (a latched Shift would turn the paste into Ctrl+Shift+V). Wrap the `pyperclip.copy(text)`
  calls inside the retry loop in try/except so a locked clipboard cannot raise.
- Verify: unit tests asserting all three releases are attempted on both paths; full suite. The physical
  verification (a latched right Shift no longer selection-extends) is part of the live deploy check.

### c4 `vibekeys-terminal-only` (outside the repo — flagged)
- `C:\Users\neill\bin\vibekeys.ahk` (not version-controlled): gate Retry and YOLO handlers with
  `WinActive("ahk_exe WindowsTerminal.exe")` — NOT a bare `WinGetProcessName("A")`, which throws an uncaught
  modal `TargetError`/`OSError` when no window is active or the foreground window is elevated. Outside
  Windows Terminal the two handlers do nothing. ~10-line diff, verified live by Neill. Rides the plan review,
  not a repo board.

## Deploy (separate authorization, after chunk acceptance)

1. Snapshot: copy live `src` to `C:\Tools\whisper-writer-wake\src.bak-<date>` (rollback path).
2. Copy CODE files only: `input_simulation.py`, `transcription.py`, `result_thread.py`, `config_schema.yaml`,
   `wake_setup.py`, `ui/settings_window.py`. **Never copy `config.yaml`** — live keeps `sound_device: 15` and
   `wake_options.enabled: true`.
3. Restart the app (one clean chain). Rollback = restore the snapshot and restart.
4. Neill re-tests: pad MIC hold = dictation; no lone spaces; no doubled lines; no delete-after-dictation;
   Retry/YOLO inert outside Windows Terminal.

## Out of scope

AirPods/Bluetooth device-hang class (deferred by Neill's 2026-10-18 ruling), Chunk 2 (AirPods toggle),
Chunk 3 (Hey Claude), pad remap changes (bindings verified alive today).
