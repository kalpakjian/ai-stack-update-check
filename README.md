# ai-stack-update-check

[![CI](https://github.com/kalpakjian/ai-stack-update-check/actions/workflows/ci.yml/badge.svg)](https://github.com/kalpakjian/ai-stack-update-check/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/kalpakjian/ai-stack-update-check?display_name=tag)](https://github.com/kalpakjian/ai-stack-update-check/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D4)]()

Check, notify, and **safely auto-update** a local AI stack on Windows:
Python, PyTorch (CUDA wheels), torchvision, torchaudio, torchcodec,
transformers, datasets, accelerate, numpy, pandas, and Ollama.

The core rule: **a newer Python is not always a safe Python.** PyTorch ships
wheels only up to a specific `cpXY` tag, so this tool derives compatibility from
real wheel filenames instead of trusting version numbers.

## Why this exists

On 2026-10-21 the machine had Python **3.15.0** available, but torch 2.14.1
shipped wheels only up to **cp314**. Upgrading Python would have broken
`import torch`. At the same time, the PyTorch index listed `cu129` and `cu134`
directories that contained **no Windows wheels at all** — so "newest CUDA index"
was not the same as "newest usable CUDA build".

This tool answers three questions in one pass:

| Question | How it is answered |
|---|---|
| Is there a newer version? | PyPI JSON + endoflife.date |
| Can I actually install it? | Real `HEAD` request for the platform wheel |
| Is it safe to upgrade Python? | Max `cpXY` tag across torch's wheels |

## Features

- **One-shot report** — current vs latest for the whole stack, with a clear status.
- **Wheel-aware** — checks actual `win_amd64` wheels on the PyTorch CUDA index.
- **Python guard** — reports "newer exists (torch max 3.14)" instead of telling you
  to break your GPU environment.
- **JSON output** — `--json` / `--json-out` for scripts and CI.
- **Daily schedule** — Task Scheduler wrapper with rotated logs (90 days).
- **Desktop toast** — WinRT notification, no third-party module required.
- **Auto-update with rollback** — snapshot, upgrade, GPU smoke test, `pip check`,
  and automatic restore on failure.

## Requirements

- Windows 10/11
- Python 3.10+ (tested on 3.14.8)
- No third-party Python packages — the checker uses only the standard library
- Optional: NVIDIA GPU for `--runtime` and the full smoke test
- Optional: Ollama running on `http://localhost:11434`

## Quick start

```powershell
git clone https://github.com/kalpakjian/ai-stack-update-check.git
cd ai-stack-update-check

python scripts\check_stack.py
python scripts\check_stack.py --json
python scripts\check_stack.py --runtime
python scripts\check_stack.py --no-network
```

### Auto-update (opt-in)

```powershell
# Preview what would be installed
powershell -File scripts\run_daily.ps1 -AutoUpdate -DryRun

# Check + upgrade + smoke test + rollback on failure
powershell -File scripts\run_daily.ps1 -AutoUpdate

# Test the toast without waiting for an update
powershell -File scripts\run_daily.ps1 -ForceNotify
```

### Options

| Flag | Meaning |
|---|---|
| `--json` | Print the full report as JSON |
| `--json-out PATH` | Also write the JSON report to a file |
| `--runtime` | Import torch and verify CUDA availability (slower) |
| `--no-network` | Skip online checks; only list installed versions |

Exit codes: `0` = no actionable updates (a Python upgrade blocked by torch still
counts as 0), `1` = at least one package can be updated, `2` = bad arguments.

## Sample output

```
======================================================================================
AI Stack Update Check   2026-10-21T10:33:43+08:00
Interpreter: C:\Python314\python.exe
======================================================================================
Component     Installed           Latest            Status
--------------------------------------------------------------------------------------
Python        3.14.8              3.15.0            newer exists (torch max 3.14)
torch         2.14.1+cu132        2.14.1            up to date
torchvision   0.29.1+cu132        0.29.1            up to date
torchaudio    2.11.0              2.11.0            up to date
torchcodec    0.17.0              0.17.0            up to date
transformers  5.20.2              5.20.2            up to date
datasets      5.1.0               5.1.0             up to date
accelerate    1.15.0              1.15.0            up to date
numpy         2.5.4               2.5.4             up to date
pandas        3.0.6               3.0.6             up to date
Ollama        0.40.6              0.40.6            up to date
--------------------------------------------------------------------------------------
GPU: NVIDIA GeForce RTX 4090 | driver 617.42 | CUDA 13.4
Ollama models: 21

Notes:
  - Python 3.15.0 is out, but the newest torch wheel only supports up to 3.14 (cp314);
    upgrading now would break PyTorch.
  - CUDA build: cu132 (newest available for Windows: cu132)

Suggested commands:
  # Python 3.15.0: do not upgrade yet; wait for a torch cp315 wheel

Result: no safe Python upgrade yet (blocked by torch wheel support).
```

## Daily schedule (Windows Task Scheduler)

```powershell
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$script = "$PWD\scripts\run_daily.ps1"
$arg = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $script + '" -AutoUpdate'
Register-ScheduledTask -TaskName 'AI Stack Update Check' `
  -Action (New-ScheduledTaskAction -Execute $ps -Argument $arg) `
  -Trigger (New-ScheduledTaskTrigger -Daily -At 23:00) `
  -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable) -Force
```

Artifacts (default log directory: `%USERPROFILE%\ai-stack-logs`):

| File | Contents |
|---|---|
| `stack-check-YYYY-MM-DD.log` | full run log |
| `stack-check-latest.log` | last 60 lines |
| `stack-check-status.txt` | `OK` / `AUTO_UPDATED: ...` / `ROLLED_BACK: ...` |
| `stack-report.json` | checker report |
| `auto-update-summary.json` | before/after, changed, smoke, rollback |

The task runs only while the user is logged on. If the machine is off or asleep
at the scheduled time, `StartWhenAvailable` runs it on the next wake.

## Auto-update policy

| Category | Packages | Behavior |
|---|---|---|
| General | transformers, datasets, accelerate, numpy, pandas | auto-upgrade |
| Audio | torchaudio, torchcodec | auto-upgrade from PyPI |
| PyTorch | torch, torchvision | auto-upgrade **only** when a matching Windows wheel exists for the running Python; uses the newest usable CUDA index |
| Python | CPython | never automatic |
| Ollama | ollama | never automatic (service restart) |
| CUDA-only | same torch version, newer CUDA build | never automatic (often a ~2 GB download) |

## How rollback works

1. `pip freeze` snapshot before touching anything.
2. Upgrade via `pip install --upgrade ...`.
3. Smoke test: import every key package and run a GPU matmul.
4. `pip check` for broken requirements.
5. On any failure: reinstall only the packages that changed, pinned to the
   snapshot version (`--force-reinstall --no-deps`), using the **original CUDA
   index** for torch builds. Newly added packages are uninstalled.
6. Re-run the smoke test and compare `pip freeze` with the snapshot; any mismatch
   marks the rollback as failed.

Exit codes for `auto_update.py`: `0` success/no-op, `1` rolled back, `2` rollback failed.

## Data sources

| Data | Source |
|---|---|
| Installed versions | `importlib.metadata` (no imports) |
| Latest Python / EOL | `https://endoflife.date/api/python.json` |
| Latest packages | `https://pypi.org/pypi/<pkg>/json` |
| Max Python for torch | `cp3XY` tags in torch wheel filenames |
| Newest usable CUDA | `https://download.pytorch.org/whl/` + real wheel `HEAD` |
| Ollama local / latest | `http://localhost:11434/api/version`, GitHub Releases |
| GPU / driver | `nvidia-smi` (`CUDA UMD Version` or `CUDA Version`) |

## Tested environment

| Component | Version |
|---|---|
| OS | Windows 11 Pro (26220) |
| Python | 3.14.8 |
| torch / torchvision | 2.14.1+cu132 / 0.29.1+cu132 |
| torchaudio / torchcodec | 2.11.0 / 0.17.0 |
| transformers / datasets / accelerate | 5.20.2 / 5.1.0 / 1.15.0 |
| GPU | NVIDIA GeForce RTX 4090, driver 617.42 |
| Ollama | 0.40.6 |

## Using it as an agent skill

Copy the folder to your local skills directory:

```powershell
Copy-Item -Recurse . "$env:USERPROFILE\.agents\skills\ai-stack-update-check"
```

`SKILL.md` contains the trigger keywords and the operating rules. The skill list
is a snapshot taken when the agent session starts, so start a new session after
installing it.

## Notes and gotchas

- **Output is English on purpose.** Windows consoles often decode redirected
  output with the legacy code page (GBK/cp936), which mangles Chinese text.
- **torchaudio on PyPI is a CPU build** (`+cpu`); DSP ops still run on CUDA
  tensors, only file decoding is CPU-bound.
- **`torch-cuda` is not a package.** It is a synthetic status meaning "same torch
  version, newer CUDA build".
- **Python is never auto-upgraded** — it cannot be done with pip, and torch may
  not support the new interpreter yet.
- **PowerShell + git:** `git push` writes progress to stderr; PowerShell may show
  it as an error even when the push succeeded. Check `git ls-remote` instead.

## License

MIT — see [LICENSE](LICENSE).

## 中文簡介

這個工具會檢查本機 AI 堆疊（Python、PyTorch/CUDA、transformers、Ollama）是否有
更新，並且**只在你真的能安裝時**才建議升級：它會讀取 PyTorch wheel 的 `cpXY`
標籤，判斷最新 Python 是否受支援；也會對 CUDA 索引發出真實的 wheel 請求，避免
把只有 Linux 版本的 `cu129`/`cu134` 誤認為可用。

可選的自動更新模式會在升級前快照、升級後跑 GPU 煙霧測試與 `pip check`，失敗時
自動回滾到原本版本。搭配 Windows 工作排程器可每天固定時間執行，並用 toast 通知
結果。

