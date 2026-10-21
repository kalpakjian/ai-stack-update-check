---
name: ai-stack-update-check
description: "檢查本機 AI 堆疊是否有新版：Python 預設版本、PyTorch/CUDA wheel、cuDNN（裝好未／版本／有沒有完整複製進 CUDA 目錄／可自動補檔）、torchvision、torchaudio、torchcodec、transformers、datasets、accelerate、numpy、pandas、Ollama 與 GPU 驅動；可用 --runtime 實測 CUDA 與 cuDNN、--bench 跑 GPU 效能基準、--fix-cudnn 觸發提權工作補檔。WHEN: 檢查更新, 睇下有冇新版, 版本檢查, Python 版本, 最新版本, PyTorch 有冇更新, CUDA wheel 版本, cuDNN 版本, cuDNN 有冇裝好, cudnn 裝好未, cuDNN 複製, cuDNN 抄過去未, include 漏咗, cuDNN 補檔, GPU 效能, 跑分, benchmark, TFLOPS, cuDNN 優化, 有冇變慢, torch 版本, transformers 版本, Ollama 更新, 套件過期, outdated, update check, version check, upgrade check, 依家裝咗咩版本, 個 stack 更唔更新. DO NOT USE FOR: 直接執行升級安裝（除非用戶明確要求，照輸出指令做）、一般 pip list／pip freeze 查詢、非 AI 套件的版本管理、切換 Python 直譯器（用 py launcher 或 uv）。"
license: MIT
metadata:
  author: local
  version: "2.4.0"
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
| 「cuDNN 有冇裝好？係咩版本？」 | 看 `cuDNN` 行（`--runtime` 才有版本）＋`Runtime` 行 |
| 「cuDNN 抄進 CUDA 目錄未？include 有冇漏？」 | 看 `Warnings`／`Notes` 的 `cuDNN vX -> CUDA vY` 行（預設模式就有） |
| 「GPU 效能點？cuDNN 有冇發揮？升級後有冇變慢？」 | 跑 `bench_gpu.py`（或 `check_stack.py --bench`、`auto_update.py --bench`） |
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

# 額外驗證 CUDA 與 cuDNN（會 import torch，約多 3-5 秒）
python C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\check_stack.py --runtime

# 離線：只列已安裝版本
python C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\check_stack.py --no-network

# 另跑 GPU 效能基準（會操 GPU，約 1 分鐘；--bench-quick 約 10 秒、不含模型）
python C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\check_stack.py --bench

# 單獨跑基準（含 TF32／fp16／channels_last／cudnn.benchmark 對照）
python C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\bench_gpu.py

# cuDNN 檔缺了要自動補（需先一次性註冊提權排程工作，見「cuDNN 自動補檔」）
python C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\check_stack.py --fix-cudnn
```

> ⚠️ 用 `py -3.15 ...` 跑，檢查的就是 3.15 那個解譯器，不是你的預設環境。
> 要檢查「日常那套」請用 `python`。

## 每日排程（23:00 自動檢查）

已建立 Windows 工作排程，無需開著終端機：

| 項目 | 值 |
|---|---|
| 工作名稱 | `AI Stack Update Check` |
| 觸發時間 | **每天 23:00**（當地時間） |
| 執行內容 | `scripts/run_daily.ps1 -AutoUpdate`（檢查 → 自動升級 → 失敗回滾；結果 toast）；檢查固定加 `--runtime`（含 cuDNN） |
| 日誌 | `C:\Users\princ\ml-demos\logs\stack-check-YYYY-MM-DD.log` |
| 最新摘要 | `C:\Users\princ\ml-demos\logs\stack-check-latest.log` |
| 狀態檔 | `...\stack-check-status.txt`（`OK` / `AUTO_UPDATED: ...` / `ROLLED_BACK: ...` / `ROLLBACK_FAILED` / `CUDNN_PROBLEM: ...`） |
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
  → 煙霧測試（import torch + GPU matmul + cuDNN 卷積 + 所有關鍵套件）
  → pip check
  → （`-Bench` 時）升級前後各跑一次 `bench_gpu.py`，把差異寫進日誌與 summary
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
| `logs\auto-update-summary.json` | 升級前後版本、changed、smoke、rollback、mismatches（`-Bench` 另有 `bench_before`／`bench_after`／`bench_delta`） |
| `logs\stack-check-status.txt` | `OK` / `AUTO_UPDATED: ...` / `ROLLED_BACK: ...` / `ROLLBACK_FAILED` / `CUDNN_PROBLEM: ...` |
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
| cuDNN 實測（2026-10-22） | ✅ `--runtime`：`cuDNN 9.24.0 (cudnn64_9.dll)`、`conv (8, 4, 32, 32) cuda:0`、`cudnn.status=ok` |
| 煙霧測試含 cuDNN（2026-10-22） | ✅ `cudnn_available True`、`cudnn_version 92400`、`conv (4, 4, 32, 32) cuda:0`、rc 0 |
| cuDNN 異常分支（2026-10-22） | ✅ 假報告測 7 種組合：`not available`／`not found`／`check failed` → `CUDNN_PROBLEM`；`ROLLED_BACK` 不被覆蓋 |
| CUDA 目錄齊全度（2026-10-22） | ✅ 實測抓到 `cuDNN v9.22 -> CUDA v13.2` 的 `include 0/8`（8 個標頭全缺）→ 進 `Warnings`＋給複製指令；`v9.26 -> v13.4` 判為 `complete` |
| 修復後複驗（2026-10-22） | ✅ 以管理員跑 `copy_cudnn_13.2.ps1`：`copied=8 skipped=39 errors=0`、逐檔 `mismatches=0`；重跑檢查 → `cuDNN v9.22 -> CUDA v13.2: bin 10/10, include 8/8, lib 29/29 (complete)`、`warnings` 0 |

### 取消自動升級

把排程動作中的 `-AutoUpdate` 拿掉即可（回到只檢查＋通知）：

```powershell
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$sc = "C:\Users\princ\.agents\skills\ai-stack-update-check\scripts\run_daily.ps1"
$arg = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $sc + '"'
Set-ScheduledTask -TaskName 'AI Stack Update Check' `
  -Action (New-ScheduledTaskAction -Execute $ps -Argument $arg)
```


## 輸出範例（2026-10-21 實測，`--runtime` 模式）

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
cuDNN         9.24.0              -                 ok
Ollama        0.40.6              0.40.6            up to date
--------------------------------------------------------------------------------------
GPU: NVIDIA GeForce RTX 4090 | driver 617.42 | CUDA 13.4
Runtime: 2.14.1+cu132 | CUDA available: True | NVIDIA GeForce RTX 4090 | cuDNN 9.24.0 (cudnn64_9.dll)
Ollama models: 21

Warnings:
  ! cuDNN v9.22 -> CUDA v13.2: 8 file(s) missing (bin 10/10, include 0/8, lib 29/29) - the cuDNN
    files are not fully copied into the CUDA toolkit: include\cudnn.h, include\cudnn_ops.h, ...

Notes:
  - Python 3.15.0 is out, but the newest torch wheel only supports up to 3.14 (cp314);
    upgrading now would break PyTorch.
  - CUDA build: cu132 (newest available for Windows: cu132)
  - cuDNN 9.24.0 available (ships inside the torch wheel; loaded from ...\torch\lib\cudnn64_9.dll).
  - cuDNN v9.26 -> CUDA v13.4: bin 10/10, include 9/9, lib 29/29 (complete).

Suggested commands:
  # Python 3.15.0: do not upgrade yet; wait for a torch cp315 wheel
  # cuDNN v9.22 -> CUDA v13.2: copy the missing files (elevated shell), e.g. Copy-Item
    'C:\Program Files\NVIDIA\CUDNN\v9.22\include\13.2\*' '...\CUDA\v13.2\include\' -Force

Result: no safe Python upgrade yet (blocked by torch wheel support).
Note: 1 warning(s) above need attention.
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
| `ok`（cuDNN） | `torch.backends.cudnn.is_available()` 為 True 且有版本號 | 不用動 |
| `present (not verified)`（cuDNN） | 檔案在 `torch\lib`，但這次沒跑 `--runtime` | 想看版本就加 `--runtime` |
| `not available`（cuDNN） | torch 在、CUDA 可用，但 cuDNN 不可用 | **要處理**：照 Warnings 重裝對應 CUDA 索引的 torch wheel |
| `not found`（cuDNN） | torch 在，但找不到 cuDNN 函式庫檔 | 先跑 `--runtime` 確認，再照 Warnings 處理 |
| `not installed`（cuDNN） | 這個環境沒有 torch | 不用管（換環境再查） |

### 三個關鍵判斷邏輯

1. **Python 能不能升，看 torch 的 wheel tag**
   腳本會抓 PyPI 上 torch 最新版的 wheel 檔名，取 `cp3XX` 的最大值。
   例：torch 2.14.1 提供 `cp310`–`cp314` → 最高支援 Python 3.14。
   即使 python.org 已出 3.15，也會標成 `newer exists (torch max 3.14)`。

2. **CUDA 變體是否最新，看「真的有 Windows wheel」**
   `download.pytorch.org/whl/` 目錄裡有 `cu129`、`cu134`，但那些只有 Linux wheel。
   腳本會對每個 CUDA 索引（`cu126`、`cu128`、`cu132`、`cu134`…）實際 HEAD 一個對應的
   wheel（例：`torch-2.14.1+cu132-cp314-cp314-win_amd64.whl`），取第一個存在的，
   所以不會誤報「有新 CUDA」。

3. **cuDNN 是「裝好未」，不是「有冇新版」**
   cuDNN 隨 torch wheel 一起安裝（Windows 在 `torch\lib\cudnn*.dll`），
   **不能獨立 pip 升級**，所以它永遠不會出現在 `updates`／`Suggested commands` 的升級清單。
   預設模式只掃檔案（10 libs、542 MB）；加 `--runtime` 才用官方 API
   `torch.backends.cudnn.is_available()` / `version()` 實測，並回報
   **實際載入的 cudnn DLL 路徑**與一次真的走 cuDNN 的 GPU 卷積。
   > ⚠️ 不要自己用 ctypes 載入 `cudnn64_9.dll` 去問 `cudnnGetVersion()`：實測會被 PATH 上
   > 另一套系統版 cuDNN 的相依 DLL 混進來（本機回 9.26.0，torch 實際用 9.24.0）。
   > torch 是用 `LoadLibraryExW(..., 0x1100)` 強制從自己的 `torch\lib` 載入。

4. **cuDNN 有沒有「完整複製」進 CUDA 目錄**
   cuDNN 的 zip 與 installer 都只是把檔案放到一個地方，還要**手動複製** `bin`＋`include`＋
   `lib\x64` 三份到 `CUDA\vXX.X\`。腳本會比對 `CUDNN\v*` 與 `CUDA\v*`（檔名＋大小，
   不讀 GB 級 static lib 的內容）：
   - 齊全 → `Notes` 一行 `cuDNN v9.26 -> CUDA v13.4: bin 10/10, include 9/9, lib 29/29 (complete).`
   - 缺檔 → `Warnings` 一行列出 `bin 10/10, include 0/8, ...` 與缺的檔名，並給複製指令。
   > installer 的來源路徑多一層 CUDA 版本（`bin\13.2\x64`、`include\13.2`）；少寫這一層
   > 會撈到 0 個檔而**靜默跳過**，這正是 include 最常漏掉的原因。
   > DLL 放 `bin\` 或 `bin\x64\` 都算到位（兩個都在 PATH 上）。

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

## cuDNN 自動補檔（一次性設定）

`Program Files` 需要管理員權限，所以「把 cuDNN 檔抄進 CUDA 目錄」不能由非提權的每日排程直接做。
做法是註冊一個**最高權限**的專用排程工作，之後非提權的行程用 `schtasks /run` 觸發它
（自己啟動自己的工作不會跳 UAC），補檔就能自動化。

```powershell
# ① 一次性（系統管理員）：註冊提權工作
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_cudnn_sync_task.ps1

# ② 之後任何時候（不需要管理員）
python scripts\check_stack.py --fix-cudnn     # 偵測到缺檔 → 觸發工作 → 等它跑完 → 重新掃描
powershell -File scripts\run_daily.ps1        # 偵測到工作存在就自動帶 --fix-cudnn

# ③ 手動測試／看日誌／移除
schtasks /run /tn "AI Stack cuDNN Sync"
Get-Content "$env:USERPROFILE\ai-stack-logs\cudnn-sync-YYYY-MM-DD.log"
powershell -File scripts\register_cudnn_sync_task.ps1 -Unregister
```

| 檔案 | 角色 |
|---|---|
| `scripts\sync_cudnn.ps1` | 提權工作本體：自動探索所有 `CUDNN\v*` ↔ `CUDA\v*` 配對、只補缺的、幂等、可 `-WhatIf` |
| `scripts\register_cudnn_sync_task.ps1` | 一次性註冊／移除（`-Unregister`）／預覽（`-DryRun`，非管理員會直接拒絕） |

行為細節：

- `sync_cudnn.ps1` 退出碼：`0` 沒有要補的、`1` 有補檔、`2` 錯誤（含非提權造成的 access denied）。
- `bin` 的目的地會優先選「已經有 cudnn DLL」的那個（`bin\` 或 `bin\x64\`），沒有才依佈局建立。
- `run_daily.ps1` 的規則是「**工作存在就自動開**」：`-FixCudnn` 強制開、`-NoFixCudnn` 關掉。
- 沒註冊工作時，檢查只會警告＋給手動指令（不會壞掉），`Notes` 會提示去跑註冊腳本。

## GPU 效能基準（bench_gpu.py）

`bench_gpu.py` 量五件事，全部實際操 GPU（輸出全英文）：

| 區塊 | 內容 |
|---|---|
| `[cuBLAS]` | matmul 4096³：fp32（TF32 off／on）、fp16、bf16 → ms 與 TFLOPS |
| `[cuDNN]` | conv2d：ResNet stem（224）與 mid（56）在 fp32／TF32／fp16，contiguous vs channels_last |
| `[cuDNN] benchmark` | `torch.backends.cudnn.benchmark` 開關前後（含 autotune 時間） |
| `[attention]` | SDPA fp16（flash attention）延遲與 TFLOPS |
| `[HF baselines]` | 快取模型（離線）：DistilBERT SST-2 texts/s、ViT-base 224 ms/img |

```powershell
python scripts\bench_gpu.py                                  # 全部（約 1 分鐘）
python scripts\bench_gpu.py --skip-hf                        # 只微基準
python scripts\bench_gpu.py --only-hf --db-batch 64 --vit-batch 1
python scripts\bench_gpu.py --quick --json-out bench.json    # 快速（升級前後對比用）
```

### 本機基線（2026-10-22 實測，RTX 4090 / torch 2.14.1+cu132 / cuDNN 9.24.0）

| 項目 | 數字 | 備註 |
|---|---|---|
| matmul fp32（TF32 off → on） | 59.8 → **82.5 TFLOPS** | TF32 +38% |
| matmul fp16／bf16 | **185.4**／159.5 TFLOPS | |
| conv mid 56 fp32 → fp16 → fp16+channels_last | 1.771 → 1.020 → **0.790 ms** | 2.24×；VRAM 656 → 107 MB |
| `cudnn.benchmark` 開關 | 1.836 → 1.836 ms（1.00×） | 固定形狀沒差；autotune 只花 0.03 s |
| SDPA fp16（flash） | 0.074 ms／116.3 TFLOPS | |
| DistilBERT SST-2（batch 64） | **3,130 texts/s** | 文章基線 3,243 texts/s（±4%） |
| ViT-base 224（batch 1／32） | 5.51／1.00 ms/img | 文章基線 5.7 ms/img |

> 用途：升級前後對比與「升級後有沒有變慢」的判準。數字是閒置 GPU 單次量測，逐次誤差約數 %。
> 三個可調開關：`torch.backends.cuda.matmul.allow_tf32`（fp32 matmul +38%）、
> AMP＋`channels_last`（conv 最多 2.24×）、`cudnn.benchmark`（輸入形狀多變才需要）。

## 設計與資料來源

| 項目 | 來源 |
|---|---|
| 已安裝版本 | `importlib.metadata.version()`（不匯入套件，快） |
| Python 最新版／EOL | `https://endoflife.date/api/python.json` |
| 套件最新版 | `https://pypi.org/pypi/<pkg>/json` |
| torch 最高支援 Python | PyPI 上 torch wheel 的 `cp3XX` tag |
| 最新可用 CUDA 索引 | `https://download.pytorch.org/whl/` ＋ 實際 HEAD wheel |
| cuDNN 檔案（預設） | 掃 `torch\lib\cudnn*.dll`（Linux：`nvidia/cudnn/lib`）— 只讀檔，不匯入 torch |
| cuDNN 可用性／版本（`--runtime`） | `torch.backends.cudnn.is_available()` / `version()`、`GetModuleHandleW` 取得實際載入路徑 |
| cuDNN 是否完整複製進 CUDA | 比對 `CUDNN\v*` 與 `CUDA\v*` 的 `bin`／`include`／`lib\x64`（檔名＋大小；`bin` 接受 `bin\` 或 `bin\x64\`） |
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
8. **寫 `Program Files` 一律走提權排程工作**：非提權行程不直接寫 `Program Files`；
   需要補 cuDNN 檔時觸發「AI Stack cuDNN Sync」（見「cuDNN 自動補檔」）。

## 故障排除

| 症狀 | 原因 | 處理 |
|---|---|---|
| 全部 `check failed` | 網路／代理／防火牆 | 先測 `Invoke-WebRequest https://pypi.org/pypi/torch/json` |
| Python 行顯示 3.15 而非 3.14 | 你用 `py -3.15` 執行 | 改用 `python` 執行 |
| `CUDA build ... newest: none` | 最新 torch 沒有你目前 Python 的 Windows wheel | 正常（例如 Python 3.15）；不是錯誤 |
| `Ollama: not running` | Ollama 服務沒開 | 啟動 Ollama，或忽略該行 |
| 沒有 GPU 行 | 找不到 `nvidia-smi` | 非 NVIDIA 機器／未安裝驅動 |
| 想看的套件不在表內 | 只涵蓋固定清單 | 改 `PACKAGES` 常數，或直接用 `pip index versions <pkg>` |
| `cuDNN: not available` | torch 載不到 cuDNN（版本不匹配／DLL 載入失敗） | 重裝對應索引的 torch（`--index-url https://download.pytorch.org/whl/cu132`）；**不要**手動複製 cuDNN |
| `cuDNN: present (not verified)` | 這次沒加 `--runtime` | 加 `--runtime` 就會有版本並變成 `ok` |
| `cuDNN: check failed` | `--runtime` 連 torch 都匯入失敗 | 先修 torch（看同一行的錯誤訊息） |
| `cuDNN auto-fix: scheduled task ... is not registered` | 還沒做一次性註冊 | 以管理員跑 `scripts\register_cudnn_sync_task.ps1` |
| 補檔後仍有 `access denied` | 工作不是最高權限執行 | 重新註冊（`-Force` 已內建）並確認 `RunLevel=Highest` |
| 工作跑了但檔案還是缺 | 來源路徑或版本層不對 | 看 `%USERPROFILE%\ai-stack-logs\cudnn-sync-*.log` 的 `SOURCE MISSING`／`FAIL` |

## 相關檔案

| 檔案 | 用途 |
|---|---|
| [scripts/check_stack.py](scripts/check_stack.py) | 主程式（STDLIB only） |
| [scripts/run_daily.ps1](scripts/run_daily.ps1) | 每日 23:00 排程包裝：檢查、自動升級、回滾、日誌、toast |
| [scripts/auto_update.py](scripts/auto_update.py) | 升級執行器：快照 → pip 升級 → 煙霧測試 → 失敗回滾（`--bench` 可加升級前後效能對比） |
| [scripts/bench_gpu.py](scripts/bench_gpu.py) | GPU 效能基準：cuBLAS／cuDNN／cudnn.benchmark／SDPA／HF 基線（`--quick`、`--json-out`） |
| [scripts/sync_cudnn.ps1](scripts/sync_cudnn.ps1) | 提權工作本體：把缺的 cuDNN 檔補進 `CUDA\v*`（幂等、`-WhatIf`、寫 `cudnn-sync-*.log`） |
| [scripts/register_cudnn_sync_task.ps1](scripts/register_cudnn_sync_task.ps1) | 一次性註冊／移除提權排程工作「AI Stack cuDNN Sync」（需管理員） |
| [scripts/notify.ps1](scripts/notify.ps1) | Windows toast 通知（WinRT，免外部模組） |
| `C:\\Users\\princ\\.agents\\skills\\authoring-agent-skills` | skill 撰寫／驗證規範 |

## 變更記錄

| 版本 | 日期 | 變更 |
|---|---|---|
| 2.4.0 | 2026-10-22 | 新增 **cuDNN 自動補檔**：`sync_cudnn.ps1`（提權工作本體）＋`register_cudnn_sync_task.ps1`（一次性註冊「AI Stack cuDNN Sync」）＋`check_stack.py --fix-cudnn`／`run_daily.ps1`（工作存在就自動觸發），解決「非提權行程不能寫 Program Files」的斷點 |
| 2.3.0 | 2026-10-22 | 新增 **GPU 效能基準** `bench_gpu.py`（cuBLAS／cuDNN／cudnn.benchmark／SDPA／HF 基線＋本機基線表）；`check_stack.py --bench` 把基準放進報告；`auto_update.py --bench` 升級前後各跑一次並輸出 `bench_delta`；`run_daily.ps1 -Bench` 串起兩者 |
| 2.2.0 | 2026-10-22 | 新增 **CUDA 目錄 cuDNN 齊全度**檢查：比對 `CUDNN\v*` 與 `CUDA\v*` 的 `bin`／`include`／`lib\x64`（檔名＋大小），缺檔進 `warnings` 並給複製指令；新增 `cuda_cudnn` JSON 欄位 |
| 2.1.0 | 2026-10-22 | 新增 **cuDNN 檢查**：預設掃 `torch\lib` 檔案、`--runtime` 用官方 API 實測（含實際載入的 cudnn DLL 與 GPU 卷積）；新增 `cuDNN` 表格列、`cudnn`／`runtime_detail`／`warnings` JSON 區塊；煙霧測試加 cuDNN 卷積（CUDA 可用但 cuDNN 不可用即回滾）；排程改用 `--runtime` 並在狀態檔寫 `CUDNN_PROBLEM` |
| 2.0.2 | 2026-10-21 | 新增 GitHub Actions CI、README 徽章、tag 觸發 Release 自動化 |
| 2.0.1 | 2026-10-21 | 腳本可攜化：`$PSScriptRoot`、`-Python`／`-LogDir` 參數，移除寫死路徑 |
| 2.0.0 | 2026-10-21 | 方案 C：自動升級 + 快照 + 煙霧測試 + 失敗回滾（`auto_update.py`）；排程改為 `-AutoUpdate` |
| 1.2.0 | 2026-10-21 | 有更新時發 Windows toast（`notify.ps1`、AppID `AIStack.UpdateCheck`） |
| 1.1.0 | 2026-10-21 | 新增每日 23:00 Windows 排程（`run_daily.ps1`）、日誌與狀態檔、90 天保留 |
| 1.0.0 | 2026-10-21 | 初版：Python/torch/CUDA/transformers/Ollama 版本比對、`--json`、`--runtime`、`--no-network` |

