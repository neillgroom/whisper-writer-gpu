# WhisperWriter — the verified working setup

> **Status: WORKING.** This documents the *verified* state that produces blazing-fast,
> local, GPU-accelerated speech-to-text bound to the **Pause/Break** key.
> Captured 2026-05-31 by reading a live, running install — not from memory.
>
> The original troubleshooting history is lost. But "what makes it work" ultimately
> **is** the exact combination of versions below. Reproduce these and it works.

---

## What it is

[WhisperWriter](https://github.com/savbell/whisper-writer) — open-source, free (MIT).
Hold a hotkey, speak, release → it transcribes locally and types the text wherever
your cursor is. **No API, no per-use cost, no audio leaves the machine.**

This repo is that, plus the Windows/CUDA fixes described in [README.md](README.md).

- Launches at login via a Startup shortcut → `venv\Scripts\pythonw.exe run.py`
  (start-in: the repo root)

## The reference machine

| Item | Value |
|------|-------|
| GPU | NVIDIA GeForce RTX 4080 Laptop GPU, 12 GB VRAM |
| NVIDIA driver | 560.94 |
| OS | Windows 11 |

You do not need this exact hardware. Any CUDA-capable NVIDIA GPU with a reasonably
current driver works. This is recorded so the versions below have a known-good context.

## The key architectural facts (this is the hard-won part)

1. **No PyTorch.** This install does **not** use torch. faster-whisper runs on
   **CTranslate2** directly — a much leaner, faster inference engine. (80 packages total,
   no multi-GB torch.)

2. **CUDA/cuDNN come from pip wheels INSIDE the venv — not a system CUDA install.**
   You do **not** need to install the CUDA Toolkit. The relevant wheels:
   - `nvidia-cudnn-cu12==9.21.1.3`
   - `nvidia-cublas-cu12==12.9.2.10`
   - `nvidia-cuda-nvrtc-cu12==12.9.86`

3. **Windows still can't find those DLLs without help.** This is the infamous
   "Could not load library cudnn / DLL not found" failure. The wheels are on disk;
   the loader doesn't look there, and PyQt5/pynput make it worse by mutating the DLL
   search path on import. The fix — registering the wheel dirs with
   `os.add_dll_directory()` **before** those imports, then force-preloading every CUDA
   DLL in the worker — lives in `src/main.py` and `src/transcription_worker.py`.
   **This is the single most valuable thing in this repo.**

4. **Version pairing is load-bearing.** `ctranslate2==4.7.1` needs **cuDNN 9.x**.
   Older `ctranslate2==4.2.x` needs cuDNN 8.x. Mixing them = the DLL error.
   → Upstream's `requirements.txt` pins the OLD `ctranslate2 4.2.1` / `faster-whisper 1.0.2`,
     which does **not** match the cuDNN 9 wheels. **`requirements.txt` in this repo is
     the verified working freeze instead.** Install from it and nothing else.

5. **NumPy must stay < 2.** Pinned at `numpy==1.24.3`. NumPy 2.x breaks numba/onnxruntime here.

6. **Python 3.11.** Verified on **3.11.9**. Newer Pythons are too new for these pinned wheels.

## Verified working versions (the ones that matter)

```
python                  3.11.9
faster-whisper          1.2.1
ctranslate2             4.7.1
nvidia-cudnn-cu12       9.21.1.3
nvidia-cublas-cu12      12.9.2.10
nvidia-cuda-nvrtc-cu12  12.9.86
numpy                   1.24.3
pynput                  1.7.6      # keyboard hook (Pause key) + types output
sounddevice             0.4.6      # mic capture
PyQt5                   5.15.10    # status window
```

Full authoritative snapshot: **`requirements.txt`** (80 pinned packages).
CPU-only variant with the CUDA wheels removed: **`requirements-cpu.txt`** (77 packages).

## The model

- Config requests: `large-v3-turbo`
- Resolves to HF repo: **`mobiuslabsgmbh/faster-whisper-large-v3-turbo`** (~1.5 GB)
- Cached at: `%USERPROFILE%\.cache\huggingface\hub\`
- Downloads automatically on first run.

**Why it's fast:** `large-v3-turbo` is `large-v3` with the decoder pruned from 32 → 4 layers
(~8× faster decode, near-identical accuracy), running float16 on the GPU via CTranslate2.

## Active config (`src/config.yaml`)

```yaml
model_options:
  use_api: false              # LOCAL — no OpenAI API, $0 cost
  local:
    model: large-v3-turbo
    device: cuda              # GPU. Change to "cpu" on machines without NVIDIA.
    compute_type: float16     # GPU-only. Use "int8" on CPU.
recording_options:
  activation_key: pause       # Pause/Break key
  recording_mode: hold_to_record
post_processing:
  input_method: pynput        # types text at cursor
```

---

## Reproduce from scratch

```powershell
git clone https://github.com/neillgroom/whisper-writer-gpu
cd whisper-writer-gpu
py -3.11 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt    # NOT upstream's — this one is the verified lock
python run.py                      # first run downloads the turbo model
```

Then create the Startup shortcut: target `<repo>\venv\Scripts\pythonw.exe`,
arguments `run.py`, start-in `<repo>`.

## On a machine with no NVIDIA GPU

`device: cuda` + `compute_type: float16` fails to start. Use the CPU preset:

```powershell
pip install -r requirements-cpu.txt
copy src\config.cpu.yaml src\config.yaml
```

That gives you `base.en` at `int8` — verified 2026-07-01 transcribing ~8× faster than
real time with clean-speech accuracy near 100%. Want more accuracy at the cost of speed?
`small.en`. Very slow machine? `tiny.en`.

On CPU it goes from "blazing" to "usable." Set expectations accordingly — it is still
completely adequate for dictating at an AI, which is most of what this gets used for.

**First-run gotcha (Windows):** if you're not in Developer Mode, the Hugging Face
download errors on symlink creation. Set `setx HF_HUB_DISABLE_SYMLINKS 1`, open a new
terminal, retry.

## Normal process picture — DON'T panic at "6 python processes"

A single running instance shows up as **~6 `pythonw.exe` processes**, and that is
**correct/healthy**, not a duplicate or a bug.

Reason: this venv uses **redirector-stub launchers**. `venv\Scripts\pythonw.exe` is a small
stub (pyvenv.cfg `home`/`executable` → the base Python 3.11 install). The stub stays alive as
the **parent** and runs the real work in a base-Python311 **child**. So each logical process
appears as a *stub + real interpreter* pair:

```
run.py     : venv-stub  +  base-Python311
main.py    : venv-stub  +  base-Python311
worker     : venv-stub  +  base-Python311   ← loads the model ONCE
```

→ 3 logical processes × 2 = ~6 OS processes. **The model loads exactly once** (`worker.log`
shows a single `model ready`). Seeing base-Python311 processes alongside venv ones is the
launcher delegating — NOT a second instance. Do **not** "clean up" the base-Python ones;
killing them tears down the live instance (they're the real interpreters).

Quick health check (PowerShell):

```powershell
Get-Content worker.log -Tail 3   # want: "model ready"
```

## If it ever breaks (the likely failure modes)

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Could not load library cudnn..." | cuDNN wheel ↔ ctranslate2 version mismatch | reinstall from `requirements.txt`; don't let pip upgrade ctranslate2 alone |
| Falls back to CPU silently / slow | GPU not visible / wrong compute_type | confirm `nvidia-smi` works; keep `device: cuda`, `compute_type: float16` |
| numpy / numba import error | numpy got upgraded to 2.x | `pip install "numpy==1.24.3"` |
| Pause key does nothing | another app grabbed the key, or the pynput hook failed | check the status window; restart `pythonw run.py` |
| Worker won't start | anything | read `worker.log` — the worker logs its exact failure there |

## Where your transcriptions go

Every transcription is appended to `history.log` in the repo root, tab-separated,
timestamped. This exists because `pythonw.exe` discards console output, so a paste that
lands in the wrong window used to be gone forever.

`history.log` is **gitignored and should stay that way** — it is a verbatim record of
everything you have ever dictated.
