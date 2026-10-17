# Review ledger

Policy: `review-routing-2026-10-07-v1` (CodeRabbit is the only specialist seat; Bugbot out since 2026-10-09).
One line per board: tag · headSha · real or seat-failure · open P0/P1 · SUMMARY.

## wake-word (plan: docs/plans/2026-10-16-wake-word.md)

- [wake-word plan b1/3] 3c367fc · seat-failure (Cursor failed → Antigravity failover) · 12 findings, all FIXED · `4way-runs\2026-10-16T16-51-32-773Z-49700`
- [wake-word plan b2/3] 88d6a97 · seat-failure (Cursor failed → Antigravity failover) · 15 findings, all FIXED · `4way-runs\2026-10-16T17-04-03-517Z-49236`
- [wake-word plan b3/3] 604abd2 · seat-failure (Cursor failed → Antigravity failover) · 3 open P1 at cap → Neill; he approved fixing in plan without board 4; 9 findings FIXED in cc2b766 · `4way-runs\2026-10-16T17-20-47-202Z-30664`
- Chunk 0 (2026-10-16): machine half PASS (stack, shared mic, synthetic clips); Neill's live laptop-mic run PASS (12/12 wakes he made, 0 false wakes reported, Pause dictation worked alongside). AirPods run still owed.
- [wake-word c1 b1/3] 6e8045b · seat-failure (Cursor failed → Antigravity failover) · author=opus · 9 findings (4 P1), all FIXED in 6b74ba1 · `4way-runs\2026-10-16T19-36-04-318Z-60820`
- [wake-word c1 b2/3] 4cec14c · seat-failure (Cursor failed → Antigravity failover) · Codex+Antigravity APPROVE_WITH_NITS, Grok BLOCK · 7 findings (1 P1), all FIXED in 1232ffd · `4way-runs\2026-10-16T20-02-52-204Z-53628`
- [wake-word c1 b3/3] 6724f78 · seat-failure (Cursor failed → Antigravity failover) · Codex+Antigravity APPROVE_WITH_NITS, Grok BLOCK · OPEN P1: 3e56549440ed (dead mic stream never aborted), d95dc97b7df6 (ResultThread.stop waits forever on a hung close) · AT CAP → Neill · `4way-runs\2026-10-16T20-25-49-272Z-68676`
- [wake-word c1 b3/3 repair] b0a0315 · 2 open P1 at cap → Neill (he said proceed) → fixed WITHOUT board 4 (bounded repair): ResultThread.stop abort-on-hung-close, WakeListener thread-abort + drop-oldest backlog, mic-resolve inside guard, console_print; 6 findings FIXED · 65 tests green
- PR #3 opened, pushed b0a0315; @coderabbitai full review triggered at that SHA (CodeRabbit = only specialist seat; Bugbot out). Default branch held; merge is Neill's.
