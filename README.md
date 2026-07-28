# whisper-writer-gpu

**Push-to-talk speech-to-text that runs entirely on your own machine. Hold a key, talk, let go — the text types itself wherever your cursor is. No API. No subscription. No audio leaves your computer. $0 forever.**

This is a hardened Windows + NVIDIA fork of [savbell/whisper-writer](https://github.com/savbell/whisper-writer). Upstream is great software. It also does not start on a lot of Windows machines, and the error it gives you is a lie.

This fork fixes that.

> Free from **[Neill's Vibe](https://github.com/neillgroom/neills-vibe)** — adventures in AI coding.

---

## If you're here from a search engine

You probably typed something like:

```
Could not load library cudnn_ops64_9.dll
faster-whisper cudnn dll not found windows
ctranslate2 cuda windows Library cublas64_12.dll is not found
whisper-writer not working gpu
```

**The fix is in [`src/main.py` lines 4-11](src/main.py) and it is nine lines long.** Read [SETUP.md](SETUP.md) for the full explanation. Short version: the CUDA DLLs *are* installed — they ship inside your venv as pip wheels — but Windows can't find them, because PyQt5 and pynput mutate the DLL search path when they import. You have to register the wheel directories **before** those imports run.

```python
if sys.platform == 'win32':
    for _pkg in ('cublas', 'cudnn', 'cuda_nvrtc'):
        _dll_dir = os.path.join(sys.prefix, 'Lib', 'site-packages', 'nvidia', _pkg, 'bin')
        if os.path.isdir(_dll_dir):
            os.add_dll_directory(_dll_dir)

import ctranslate2      # initialize CUDA before PyQt/pynput poison the loader
import faster_whisper
```

Import order is load-bearing. That's the whole bug. You're welcome — go have your evening back.

---

## Install

**Requires Python 3.11** (not 3.12+, not 3.13 — the pinned wheels don't exist for them).

### NVIDIA GPU (Windows) — the fast path

```powershell
git clone https://github.com/neillgroom/whisper-writer-gpu
cd whisper-writer-gpu
py -3.11 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

First run downloads the model (~1.5 GB) automatically. Then hold **Pause/Break**, speak, release.

### No NVIDIA GPU — the CPU path

```powershell
git clone https://github.com/neillgroom/whisper-writer-gpu
cd whisper-writer-gpu
py -3.11 -m venv venv
venv\Scripts\activate
pip install -r requirements-cpu.txt
copy src\config.cpu.yaml src\config.yaml
python run.py
```

`requirements-cpu.txt` is the same lock minus ~1 GB of CUDA wheels you'd never load. The CPU preset uses `base.en` at `int8` — roughly 8× faster than real time on a modern laptop, accurate enough to dictate at an AI. It is not as good as the GPU path. It is genuinely usable.

If the first-run model download errors on symlink creation (Windows, not in Developer Mode):

```powershell
setx HF_HUB_DISABLE_SYMLINKS 1
```

Then open a new terminal and retry.

### Launch it at login

Make a shortcut in your Startup folder (`Win+R` → `shell:startup`):

| Field | Value |
|---|---|
| Target | `<repo>\venv\Scripts\pythonw.exe` |
| Arguments | `run.py` |
| Start in | `<repo>` |

`pythonw.exe` (not `python.exe`) means no console window. It starts to the tray with the hotkey already armed.

---

## What this fork changes

Four things, all of them scars.

### 1. The CUDA DLL loader fix

Described above. `src/main.py`. Nine lines that turn "it doesn't work and the internet doesn't know why" into "it works."

### 2. The GPU model runs out-of-process

`src/transcription_worker.py` + `RemoteModel` in `src/transcription.py`.

Running a CUDA model in the same process as a Qt event loop deadlocks on Windows. So the model lives in a subprocess that **never imports PyQt5**, and the app talks to it over stdin/stdout with length-prefixed frames:

```
Request:  [4-byte BE length][JSON header][4-byte BE length][raw float32 audio]
Response: [4-byte BE length][JSON body]
```

Framing only — no pickle, no untrusted deserialization. The model loads **once**, at startup, and stays hot. Every subsequent transcription is inference-only, which is why it feels instant.

**Side effect that looks alarming and isn't:** one running instance shows up as **~6 `pythonw.exe` processes** in Task Manager. That's correct. The venv uses redirector-stub launchers, so each of the 3 logical processes (`run.py`, `main.py`, worker) appears as a stub + real interpreter pair. **Do not "clean up" the ones that look like duplicates** — killing them tears down your live instance. Check `worker.log` for a single `model ready` line to confirm the model loaded exactly once.

### 3. Transcription history

`history.log` in the repo root, one tab-separated line per transcription.

Because it launches via `pythonw.exe`, console output goes nowhere. Before this, if a paste landed in the wrong window or an app ate it, the text was gone forever. Now it isn't. Best-effort — a failure to write history never blocks the paste.

**It's gitignored, and you want it to stay that way.** It is a verbatim log of everything you have ever dictated.

### 4. Tray-only launch + cleaner output

Starts minimized with the hotkey armed instead of popping the main window at you. Post-processing strips filler words (`uh`, `um`) and collapses stutters (`but but but` → `but`) before typing.

---

## Configuration

Three config files ship here:

| File | What it is |
|---|---|
| `src/config.yaml` | **Active config.** Ships as the GPU preset. |
| `src/config.gpu.yaml` | GPU preset backup — `large-v3-turbo`, `cuda`, `float16` |
| `src/config.cpu.yaml` | CPU preset — `base.en`, `cpu`, `int8`, heavily commented |

Copy the preset you want over `src/config.yaml`. The knobs that matter:

```yaml
model_options:
  use_api: false          # false = local, $0. Leave it false.
  local:
    model: large-v3-turbo # GPU. On CPU use base.en / small.en / tiny.en
    device: cuda          # or cpu
    compute_type: float16 # GPU only. Use int8 on CPU.
recording_options:
  activation_key: pause   # any key name pynput understands
  recording_mode: hold_to_record
```

**`use_api: false` is the whole value proposition.** It never calls OpenAI, so there is no key to buy and no per-minute charge. There is a paid product literally named "WhisperKey" — unrelated, don't confuse them.

---

## Why `large-v3-turbo` is fast

It's `large-v3` with the decoder pruned from 32 layers to 4 — about 8× faster decode, near-identical accuracy — running `float16` on the GPU through CTranslate2.

And there is **no PyTorch here.** faster-whisper runs on CTranslate2 directly: 80 packages, no multi-gigabyte torch install. That's a big part of why it starts fast and stays small.

---

## When it breaks

| Symptom | Cause | Fix |
|---|---|---|
| `Could not load library cudnn...` | cuDNN wheel ↔ ctranslate2 version mismatch | Reinstall from `requirements.txt`. Never `pip install -U ctranslate2` on its own. |
| Silently slow / fell back to CPU | GPU not visible, or wrong `compute_type` | Confirm `nvidia-smi` works; keep `device: cuda` + `compute_type: float16` |
| `numpy` / `numba` import error | numpy got upgraded to 2.x | `pip install "numpy==1.24.3"` — it must stay below 2 |
| Pause key does nothing | Another app grabbed the key, or the pynput hook failed | Check the tray status window; restart `pythonw run.py` |
| ~6 python processes | Normal. See above. | Do nothing. |

**The version pairing is load-bearing.** `ctranslate2==4.7.1` requires cuDNN 9.x. The older `4.2.x` requires cuDNN 8.x. Mixing them produces exactly the DLL error this fork exists to fix — which is why upstream's `requirements.txt` (pinning `ctranslate2==4.2.1` against cuDNN 9 wheels) is a trap, and why `requirements.txt` **in this repo is a verified working freeze** of all 80 packages.

Full reproduction recipe and the reference machine's exact versions: **[SETUP.md](SETUP.md)**.

---

## Credit

All the actual speech-to-text application is [**savbell/whisper-writer**](https://github.com/savbell/whisper-writer), MIT licensed. Forked at upstream commit `370333b`. Upstream's original README is preserved here as [README-upstream.md](README-upstream.md) and its changelog as [CHANGELOG-upstream.md](CHANGELOG-upstream.md) — note that upstream's README references a `requirements.txt` layout this fork has deliberately changed.

This fork adds the Windows/CUDA loader fix, the out-of-process GPU worker, transcription history, and verified dependency locks. Everything here stays MIT — see [LICENSE](LICENSE).

If the maintainers want any of this upstream, take it. No permission needed.

---

## License

MIT. Same as upstream. Do whatever you want with it.
