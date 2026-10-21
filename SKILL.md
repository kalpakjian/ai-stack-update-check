---
name: ai-stack-update-check
description: "檢查本機 AI 堆疊是否有新版：Python 預設版本、PyTorch/CUDA wheel、torchvision、torchaudio、torchcodec、transformers、datasets、accelerate、numpy、pandas、Ollama 與 GPU 驅動；可用 --runtime 實測 CUDA。WHEN: 檢查更新, 睇下有冇新版, 版本檢查, Python 版本, 最新版本, PyTorch 有冇更新, CUDA wheel 版本, torch 版本, transformers 版本, Ollama 更新, 套件過期, outdated, update check, version check, upgrade check, 依家裝咗咩版本, 個 stack 更唔更新. DO NOT USE FOR: 直接執行升級安裝（除非用戶明確要求，照輸出指令做）、一般 pip list／pip freeze 查詢、非 AI 套件的版本管理、切換 Python 直譯器（用 py launcher 或 uv）。"
license: MIT
metadata:
  author: local
  version: "2.0.0"
  root: "C:\\Users\\princ"
---

# ai-stack-update-check — 本機 AI 堆疊版本檢查

一次列出「已安裝 vs 官方最新」，並判斷**升級是否安全**。
最關鍵的一條規則：**Python 有新版不代表可以升**——PyTorch 沒出對應 `cpXY` wheel 時，
升級會讓 `import torch` 直接失效。

## 何時用

| 用戶意圖 | 動作 |
|---|---|
| 「檢查下有冇更新」「個 stack 係咪最新」 | 跑預設模式（表格） |
| 「Python 係咩版本？有冇新過？」 | 看 `Python` 那一行＋Notes |
| 「PyTorch / CUDA 版本啱唔啱？」 | 看 `torch` 行＋`CUDA build` note |
| 「Ollama 要唔要更新？」 | 看 `Ollama` 行 |
| 想把結果餵給其他程式 | `--json` |
| 想確認 CUDA 真的能用 | `--runtime`（會匯入 torch，較慢） |
| 沒網路／離線 | `--no-network`（只列已安裝版本） |
| **想自動升級（含回滾）** | `run_daily.ps1 -AutoUpdate` |
| 只想預覽升級指令 | `run_daily.ps1 -AutoUpdate -DryRun` |
| 手動升級 | 先跑檢查，再用 `Suggested commands`；不要自行猜版本 |

## 快速用法

```powershell
# 用「預設 python」檢查，才會反映你日常使用的環境
python C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\check_stack.py

# JSON（給腳本／CI 用）
python C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\check_stack.py --json

# 額外驗證 CUDA（會 import torch，約多 3-5 秒）
python C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\check_stack.py --runtime

# 離線：只列已安裝版本
python C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\check_stack.py --no-network
```

> ⚠️ 用 `py -3.15 ...` 跑，檢查的就是 3.15 那個解譯器，不是你的預設環境。
> 要檢查「日常那套」請用 `python`。

## 每日排程（23:00 自動檢查）

已建立 Windows 工作排程，無需開著終端機：

| 項目 | 值 |
|---|---|
| 工作名稱 | `AI Stack Update Check` |
| 觸發時間 | **每天 23:00**（當地時間） |
| 執行內容 | `scripts/run_daily.ps1 -AutoUpdate`（檢查 → 自動升級 → 失敗回滾；結果 toast） |
| 日誌 | `C:\Users\princ\ml-demos\logs\stack-check-YYYY-MM-DD.log` |
| 最新摘要 | `C:\Users\princ\ml-demos\logs\stack-check-latest.log` |
| 狀態檔 | `...\stack-check-status.txt`（`OK` / `AUTO_UPDATED: ...` / `ROLLED_BACK: ...` / `ROLLBACK_FAILED`） |
| 保留期限 | 90 天（自動刪除舊日誌） |

```powershell
# 查看排程與下次執行時間
Get-ScheduledTask -TaskName 'AI Stack Update Check'
Get-ScheduledTaskInfo -TaskName 'AI Stack Update Check'

# 立即測試一次（不用等到 23:00）
Start-ScheduledTask -TaskName 'AI Stack Update Check'
Get-Content C:\Users\princ\ml-demos\logs\stack-check-latest.log

# 停用 / 刪除
Disable-ScheduledTask -TaskName 'AI Stack Update Check'
Unregister-ScheduledTask -TaskName 'AI Stack Update Check' -Confirm:$false
```

> ⚠️ 工作以「目前使用者、僅在登入時執行」註冊。
> 電腦關機或睡眠時不會自動喚醒（未設 `WakeToRun`）；若錯過排程，
> `StartWhenAvailable` 會在下次開機／喚醒後補跑。
> 有更新時：check-only 模式寫 `UPDATES_AVAILABLE`；`-AutoUpdate` 模式會寫
> `AUTO_UPDATED` 或 `ROLLED_BACK`，並在日誌留下完整過程。

### 更新通知（Windows toast）

有套件可更新時，排程會發一個 Windows 通知：

```
AI Stack: 2 update(s) available
Updates: torch, transformers
Details: stack-check-latest.log
```

| 項目 | 值 |
|---|---|
| 實作 | `scripts/notify.ps1`（WinRT，免安裝 BurntToast） |
| AppUserModelID | `AIStack.UpdateCheck`（已註冊於 HKCU） |
| 測試 | `powershell -File scripts\run_daily.ps1 -ForceNotify` |
| 失敗紀錄 | 日誌最後一行 `TOAST: ...` |

> 若沒看到通知：檢查「設定 → 系統 → 通知」是否啟用、專注模式／勿擾是否開啟，
> 以及通知清單中是否出現 **AI Stack Check**。




## 自動更新與回滾（方案 C）

`run_daily.ps1 -AutoUpdate` 會**真的執行升級**，不是只通知。

### 流程

```
pip freeze 快照
  → 依 check_stack.py 的報告升級
  → 煙霧測試（import torch + GPU matmul + 所有關鍵套件）
  → pip check
  → 全部通過：保留升級、發成功通知
  → 任一失敗：把本次變動的套件還原到快照版本
       → 還原後再跑一次煙霧測試
       → 成功：通知「已回滾」；失敗：通知「需人工處理」
```

### 自動升級政策

| 類別 | 套件 | 行為 |
|---|---|---|
| 一般套件 | transformers、datasets、accelerate、numpy、pandas | ✅ 自動升級 |
| 音訊 | torchaudio、torchcodec | ✅ 自動升級（PyPI） |
| PyTorch | torch、torchvision | ✅ 自動升級，但**必須**有對應 Python 的 Windows wheel；用 `newest_cuda` 索引 |
| Python | CPython | ❌ 永不自動（要重新安裝，且 torch 未必支援） |
| Ollama | `ollama` | ❌ 永不自動（需重啟服務） |
| CUDA-only 更新 | `torch-cuda` | ❌ 不自動（同版本換 CUDA 建置，1.9 GB，手動決定） |

> torch 升級可能下載 ~2 GB；不想等就把排程的 `-AutoUpdate` 拿掉。

### 指令

```powershell
# 檢查 + 自動升級（失敗回滾）
powershell -File C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\run_daily.ps1 -AutoUpdate

# 只看會執行什麼，不動環境
powershell -File C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\run_daily.ps1 -AutoUpdate -DryRun

# 只檢查（把排程的 -AutoUpdate 拿掉就是這個模式）
powershell -File C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\run_daily.ps1
```

### 檔案與狀態

| 檔案 | 內容 |
|---|---|
| `logs\stack-report.json` | check_stack.py 的完整報告（升級決策來源） |
| `logs\auto-update-summary.json` | 升級前後版本、changed、smoke、rollback、mismatches |
| `logs\stack-check-status.txt` | `OK` / `AUTO_UPDATED: ...` / `ROLLED_BACK: ...` / `ROLLBACK_FAILED` |
| `logs\stack-check-YYYY-MM-DD.log` | 完整過程（含 pip 輸出、`TOAST:` 行） |

### 回滾細節

- 只還原**本次有變動**的套件（`changed`），不是整個環境重裝。
- 使用 `pip install --force-reinstall --no-deps <name>==<old>`。
- torch/torchvision 用**升級前的 CUDA 索引**（例如 `cu132`）還原，避免裝到 CPU 版。
- 本次新裝的套件會 `pip uninstall -y` 移除。
- 還原後比對 `pip freeze`；有任何版本不符 → `ROLLBACK_FAILED`。
- `auto_update.py` 退出碼：`0` 成功／無事可做、`1` 已回滾、`2` 回滾失敗。

### 已驗證案例（2026-10-21）

| 測試 | 結果 |
|---|---|
| 升級：accelerate 1.14.0 → 1.15.0 | ✅ smoke pass、pip check pass |
| 強制失敗後回滾 | ✅ 還原 1.14.0、post-rollback smoke pass、`result=rolled-back` |
| 排程端到端 | ✅ `AUTO_UPDATED: accelerate`、toast 已送出 |

### 取消自動升級

把排程動作中的 `-AutoUpdate` 拿掉即可（回到只檢查＋通知）：

```powershell
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$sc = "C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\run_daily.ps1"
$arg = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $sc + '"'
Set-ScheduledTask -TaskName 'AI Stack Update Check' `
  -Action (New-ScheduledTaskAction -Execute $ps -Argument $arg)
```


## 輸出範例（2026-10-21 實測）

```
======================================================================================
AI Stack Update Check   2026-10-21T10:33:43+08:00
Interpreter: C:\Users\princ\AppData\Local\Programs\Python\Python314\python.exe
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

## 判讀規則

| Status | 意思 | 行動 |
|---|---|---|
| `up to date` | 已是最新 | 不用動 |
| `update available` | 有新版 | 用 `Suggested commands` 升級 |
| `newer exists (torch max X.Y)` | 有新 Python，但 torch 未支援 | **不要升 Python**；等 torch wheel |
| `not installed` | 該套件不在這個環境 | 需要才裝 |
| `not checked` | 離線模式 | 需要時重跑（不加 `--no-network`） |
| `check failed` | 查不到（網路／套件不存在） | 檢查網路或套件名稱 |

### 兩個關鍵判斷邏輯

1. **Python 能不能升，看 torch 的 wheel tag**
   腳本會抓 PyPI 上 torch 最新版的 wheel 檔名，取 `cp3XX` 的最大值。
   例：torch 2.14.1 提供 `cp310`–`cp314` → 最高支援 Python 3.14。
   即使 python.org 已出 3.15，也會標成 `newer exists (torch max 3.14)`。

2. **CUDA 變體是否最新，看「真的有 Windows wheel」**
   `download.pytorch.org/whl/` 目錄裡有 `cu129`、`cu134`，但那些只有 Linux wheel。
   腳本會對每個 `cuNNN` 實際 HEAD 一個 `torch-<latest>+cuNNN-cpXXX-cpXXX-win_amd64.whl`，
   取第一個存在的，所以不會誤報「有新 CUDA」。

## 升級指令（照輸出用，不要猜）

```powershell
# 一般套件
python -m pip install --upgrade transformers datasets accelerate

# PyTorch：一定要指定 CUDA 索引，否則會裝到 CPU 版
python -m pip install --upgrade torch torchvision `
  --index-url https://download.pytorch.org/whl/cu132

# torchaudio / torchcodec 走 PyPI（cu132 索引沒有新版 torchaudio）
python -m pip install --upgrade torchaudio torchcodec

# Ollama
winget upgrade Ollama.Ollama
```

## 設計與資料來源

| 項目 | 來源 |
|---|---|
| 已安裝版本 | `importlib.metadata.version()`（不匯入套件，快） |
| Python 最新版／EOL | `https://endoflife.date/api/python.json` |
| 套件最新版 | `https://pypi.org/pypi/<pkg>/json` |
| torch 最高支援 Python | PyPI 上 torch wheel 的 `cp3XX` tag |
| 最新可用 CUDA 索引 | `https://download.pytorch.org/whl/` ＋ 實際 HEAD wheel |
| Ollama 本機版本 | `http://localhost:11434/api/version` |
| Ollama 最新版 | GitHub Releases API |
| GPU／驅動 | `nvidia-smi`（解析 `CUDA UMD Version` 或 `CUDA Version`） |

## 硬規則

1. **只檢查，不自動升級**：腳本只印指令；實際安裝由用戶決定。
2. **退出碼**：`0` = 沒有可用更新（含「Python 被 torch 阻擋」）；`1` = 有套件可更新；
   `2` = 參數錯誤。可直接用於 CI。
3. **只用標準函式庫**：不需要 `pip install` 任何東西。
4. **輸出全英文**：避免 Windows 主控台在重導向時用 GBK 解碼造成亂碼；中文說明看本文件。
5. **用預設 `python` 執行**：否則檢查到的是另一個解譯器。
6. **需要網路**才能判斷新版本；離線請加 `--no-network`（只列已安裝）。
7. **Windows 專用**：CUDA wheel 探測寫死 `win_amd64`；Ollama 與 `nvidia-smi` 也是本機路徑。

## 故障排除

| 症狀 | 原因 | 處理 |
|---|---|---|
| 全部 `check failed` | 網路／代理／防火牆 | 先測 `Invoke-WebRequest https://pypi.org/pypi/torch/json` |
| Python 行顯示 3.15 而非 3.14 | 你用 `py -3.15` 執行 | 改用 `python` 執行 |
| `CUDA build ... newest: none` | 最新 torch 沒有你目前 Python 的 Windows wheel | 正常（例如 Python 3.15）；不是錯誤 |
| `Ollama: not running` | Ollama 服務沒開 | 啟動 Ollama，或忽略該行 |
| 沒有 GPU 行 | 找不到 `nvidia-smi` | 非 NVIDIA 機器／未安裝驅動 |
| 想看的套件不在表內 | 只涵蓋固定清單 | 改 `PACKAGES` 常數，或直接用 `pip index versions <pkg>` |

## 相關檔案

| 檔案 | 用途 |
|---|---|
| [scripts/check_stack.py](scripts/check_stack.py) | 主程式（STDLIB only） |
| [scripts/run_daily.ps1](scripts/run_daily.ps1) | 每日 23:00 排程包裝：檢查、自動升級、回滾、日誌、toast |
| [scripts/auto_update.py](scripts/auto_update.py) | 升級執行器：快照 → pip 升級 → 煙霧測試 → 失敗回滾 |
| [scripts/notify.ps1](scripts/notify.ps1) | Windows toast 通知（WinRT，免外部模組） |
| `C:\\Users\\princ\\.agents\\skills\\authoring-agent-skills` | skill 撰寫／驗證規範 |

## 變更記錄

| 版本 | 日期 | 變更 |
|---|---|---|
| 2.0.0 | 2026-10-21 | 方案 C：自動升級 + 快照 + 煙霧測試 + 失敗回滾（`auto_update.py`）；排程改為 `-AutoUpdate` |
| 1.2.0 | 2026-10-21 | 有更新時發 Windows toast（`notify.ps1`、AppID `AIStack.UpdateCheck`） |
| 1.1.0 | 2026-10-21 | 新增每日 23:00 Windows 排程（`run_daily.ps1`）、日誌與狀態檔、90 天保留 |
| 1.0.0 | 2026-10-21 | 初版：Python/torch/CUDA/transformers/Ollama 版本比對、`--json`、`--runtime`、`--no-network` |

