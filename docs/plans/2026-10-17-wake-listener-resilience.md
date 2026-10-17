# Fix plan: wake listener must survive a transient mic hiccup (not die silently)

## Decisions (Neill, 2026-10-17)
- **Fix scope: harden it fully.** Supervised listener that auto-recovers from any
  transient mic failure, reopens on a stalled stream, logs every transition. Full
  four-way board + a live re-test. (Not the minimal/fire-drill path.)
- **Failure UX: tray notification.** When wake genuinely can't recover after
  repeated failures, pop a tray warning and also write it to the log.

## Root cause (diagnosed live, 2026-10-17)
The integrated wake path never fired for Neill. Live probes on his laptop mic
(device 15, "Microphone Array (2- Realtek Audio)") established:

- The detector + mic + stream config are fine: standalone, "Hey Jarvis" scores
  0.94–0.999, foreground is `WindowsTerminal.exe` (already allowed → gate passes).
- The exact `WakeListener` config (callback + `WasapiSettings(auto_convert=True)`)
  opens reliably **15/15** solo and delivers frames.
- But a probe running the real `WakeListener` once hit a transient PortAudio host
  error on open: `Unanticipated host error [-9999] ... WDM-KS`. When that happens
  the thread emits `errorSignal` and **exits**; `main.on_wake_error` then `wait()`s
  and drops the listener. The error surfaces only as a tray toast — never the log.

So a single momentary device hiccup (contention, USB/Bluetooth re-enumeration,
power management) permanently and invisibly disables wake for the whole session.
That is the "never worked yet" symptom.

## The fix (durable) — `src/wake_listener.py`
Turn `WakeListener.run()` from "open once, die on any error" into a supervised
loop that stays listening for as long as wake is enabled:

- **Outer retry loop** `while self._running:` — open the stream, run the detect
  loop, and on any exception during open or run, log it (console_print, so it is
  in the log and not just a tray toast), back off `RETRY_BACKOFF_S`, and retry.
  The thread only exits when `stop()` sets `_running = False`.
- **Stall recovery:** if no frame arrives for `STALL_TIMEOUT_S` while the stream
  still claims `active`, treat it as dead and reopen (a silent stall is as fatal
  as a raised error for an always-on stream).
- **Tray warning only on persistent failure:** emit `errorSignal` once after
  `WARN_AFTER_FAILURES` consecutive failures (not on the first), so a 1-second
  hiccup that self-heals never nags; log when it recovers.
- Keep the existing drop-oldest backlog and the abort-on-hung-close in `stop()`.
- Backoff sleep stays responsive to `stop()` (checks `_running` every ~100 ms).

## `src/main.py`
`errorSignal` no longer means "the thread is exiting." Change `on_wake_error` to
**only** show the tray warning; it must NOT `wait()` or drop `self.wake_listener`
(the thread is still alive and retrying). The listener is torn down only by
`cleanup()` / `stop()` as today.

## Tests — `tests/test_wake_listener.py` (revise; contract changed)
- A transient open failure (fail once, then succeed) is retried and the detector
  then receives frames / emits — the thread stays running across the failure.
- A persistent open failure emits `errorSignal` after `WARN_AFTER_FAILURES` and
  the thread keeps retrying (does not exit).
- Stall: stream reports `active` but delivers no frames → the loop reopens.
- `stop()` still returns promptly and ends the thread, including mid-backoff and
  during a hung close (keep the existing abort-on-hung-close coverage).

## Review
Concurrency = sensitive list → Opus authors; full four-way board on the chunk,
then update PR #3, then a live re-test with Neill (restart app, say "Hey Jarvis",
confirm it types). policy `review-routing-2026-10-07-v1`.
