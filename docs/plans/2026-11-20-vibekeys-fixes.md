# Plan: vibekeys fixes — pad MIC, doubled dictation, Shift latch, drift port, AHK scoping

Date: 2026-11-20 · Repo: whisper-writer-gpu-wake · Base: main (358e85f) · Policy: review-routing-2026-10-07-v1

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
   character selection-extends and REPLACES text.
4. **Single instance confirmed** — the venv `python/pythonw` are launcher stubs fronting base Python311
   (stub+real process pairs = one app chain; ledger 2026-10-18 documented the same). NOT doubling.
5. **Live drift** — repo unchanged since 555d081; live install differs: `input_simulation.py` (+75: real
   Shift+VK events, `paste` input method, `should_continue`), `transcription.py` (+47: `is_hallucination`
   filter), `result_thread.py` (1 line: `extra_settings` when device is not None), `config.yaml`
   (`vad_filter: true`, `condition_on_previous_text: false`, `sound_device: 15`, `wake_options` section).
6. **vibekeys.ahk** — Retry (`^!+r` = Up+Enter) and YOLO (`^!+e` = Tab+Enter) fire globally; Neill approved
   terminal-only scoping (2026-11-20 session).

## Chunks (each = one behavior, one board)

### c1 `port-drift`
Copy the four live-only changes into the repo so the reviewed tree matches the running install:
- `src/input_simulation.py`, `src/transcription.py` (is_hallucination block), `src/result_thread.py`
  (one-liner) — verbatim from live.
- `src/config.yaml`: port `vad_filter: true`, `condition_on_previous_text: false`, and the `wake_options`
  section as defaults. `sound_device` stays `null` in the repo template (machine-specific; live keeps 15).
- Verify: repo test suite (pytest, 82 green expected) + `py_compile` on changed files.

### c2 `silence-guard`
- `transcription.py`: collapse an exact whole-transcript repetition (word-level; the transcript is a k× repeat,
  k≥2, of a unit ≥3 words) to the FIRST copy, preserving its original text verbatim (token spans keep the first
  copy's punctuation/capitalization). Full-drop behavior of `is_hallucination` unchanged.
- `main.py` `on_transcription_complete`: skip typing (and the completion beep) when `result.strip()` is empty —
  silence artifacts stop typing lone spaces. History logging unchanged.
- Verify: unit tests for collapse (2× with punctuation drift, 2× clean, 3× small loop still dropped, non-repeat
  untouched) + whitespace-skip test; full suite.

### c3 `shift-latch`
- `input_simulation.py` `_typewrite_pynput`: defensive release covers `Key.shift_l`, `Key.shift_r`, and
  `Key.shift` (all three; releasing an unpressed key is a no-op), so a latched right Shift can never
  selection-extend the next transcript.
- Verify: unit test asserting all three releases are attempted; full suite.

### c4 `vibekeys-terminal-only` (outside the repo — flagged)
- `C:\Users\neill\bin\vibekeys.ahk` (not version-controlled): gate Retry and YOLO handlers on the foreground
  process being `WindowsTerminal.exe` (`WinGetProcessName("A")`); elsewhere they do nothing. ~10-line diff,
  verified live by Neill. Rides the plan review, not a repo board.

## Deploy (separate authorization, after chunk acceptance)

Copy the reviewed files into `C:\Tools\whisper-writer-wake\src` and restart the app (one clean chain). Neill
re-tests: pad MIC hold = dictation; no lone spaces; no doubled lines; no delete-after-dictation.

## Out of scope

AirPods/Bluetooth device-hang class (deferred by Neill's 2026-10-18 ruling), Chunk 2 (AirPods toggle),
Chunk 3 (Hey Claude), pad remap changes (bindings verified alive today).
