#!/usr/bin/env bash
# verify_stack.sh — 驗證 NVIDIA 驅動 / CUDA / PyTorch 堆疊狀態
#
# 用法：
#   ./verify_stack.sh              # 系統 + 兩個 venv + 功能測試
#   ./verify_stack.sh --full       # 再加 ComfyUI 實機煙霧測試 + 基準對照
#
# 環境變數：
#   COMFY     ComfyUI 根目錄（預設 $HOME/Projects/ComfyUI）
#   BENCH_DIR 基準 JSON 所在目錄（預設：本腳本所在的 scripts/linux/ 目錄）
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMFY="${COMFY:-$HOME/Projects/ComfyUI}"
BENCH_DIR="${BENCH_DIR:-$SCRIPT_DIR}"
FULL=0
for arg in "$@"; do
  case "${arg}" in
    --full) FULL=1 ;;
    -h|--help)
      echo "用法：$(basename "$0") [--full] [ComfyUI 路徑]"
      echo "環境變數：COMFY（ComfyUI 根目錄）、BENCH_DIR（基準 JSON 目錄）"
      exit 0 ;;
    *) COMFY="${arg}" ;;
  esac
done

hr() { printf '%s\n' "------------------------------------------------------------"; }

echo "============================================================"
echo "AI Stack Verification   $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "============================================================"
hr

echo "[1] 系統"
echo "  kernel      : $(uname -r)"
echo "  os          : $(grep PRETTY_NAME /etc/os-release | cut -d'"' -f2)"
echo "  driver pkg  : $(pacman -Q nvidia-open-dkms 2>/dev/null || echo 'n/a')"
echo "  running drv : $(cat /proc/driver/nvidia/version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"

hr
echo "[2] nvidia-smi"
nvidia-smi --query-gpu=name,driver_version,memory.total,temperature.gpu --format=csv,noheader 2>&1
CUDA_UMD=$(nvidia-smi 2>/dev/null | grep -oE 'CUDA UMD Version: *[0-9.]+' | grep -oE '[0-9.]+$' | head -1)
echo "  CUDA UMD    : ${CUDA_UMD:-<unknown>}"

# CUDA UMD >= 13.4 -> cu134 nightly 可用
if [[ -n "${CUDA_UMD:-}" ]]; then
  major="${CUDA_UMD%%.*}"; minor="${CUDA_UMD##*.}"
  if (( major > 13 || (major == 13 && minor >= 4) )); then
    echo "  => CUDA >= 13.4 : cu134 nightly torch 可安裝"
  else
    echo "  => CUDA <  13.4 : cu134 nightly 暫不可用，nightly 請用 cu132"
  fi
fi

hr
echo "[3] PyTorch 環境"
for venv in .venv .venv-nightly; do
  py="${COMFY}/${venv}/bin/python"
  [[ -x "$py" ]] || { echo "  [${venv}] 不存在"; continue; }
  echo "  --- ${venv} ---"
  "$py" - <<'PY' 2>&1
import torch, sys
print(f"    python      : {sys.version.split()[0]}")
print(f"    torch       : {torch.__version__}")
print(f"    cuda avail  : {torch.cuda.is_available()}")
print(f"    torch CUDA  : {torch.version.cuda}")
print(f"    cuDNN       : {torch.backends.cudnn.version()} (available={torch.backends.cudnn.is_available()})")
if torch.cuda.is_available():
    print(f"    device      : {torch.cuda.get_device_name(0)}  sm_{''.join(map(str, torch.cuda.get_device_capability(0)))}")
    print(f"    arch list   : {torch.cuda.get_arch_list()}")
# torchaudio 必須能被 import，且其編譯 CUDA 版本要與 torch 一致，
# 否則 ComfyUI 會在 comfy/sd.py -> audio_vae.py 的硬 import 直接崩潰。
try:
    import torchaudio
    print(f"    torchaudio   : {torchaudio.__version__}")
    ta = torch.ops._torchaudio.cuda_version()
    if ta is not None:
        ta_s = f"{str(ta)[:-3]}.{str(ta)[-2]}"
        tc_s = ".".join(torch.version.cuda.split(".")[:2])
        flag = "OK" if ta_s == tc_s else "MISMATCH  <-- ComfyUI 會啟動失敗"
        print(f"    ta/torch CUDA: {ta_s} vs {tc_s}  -> {flag}")
except Exception as e:
    print(f"    torchaudio   : ERROR {type(e).__name__}: {str(e)[:140]}")
PY
done

hr
echo "[4] 功能測試 (stable venv)"
"${COMFY}/.venv/bin/python" - <<'PY' 2>&1
import time, torch
def bench(fn, iters=20, warmup=5):
    for _ in range(warmup): fn()
    torch.cuda.synchronize(); t = time.time()
    for _ in range(iters): fn()
    torch.cuda.synchronize(); return (time.time() - t) / iters * 1000

x = torch.randn(8192, 8192, device='cuda', dtype=torch.float16)
ms = bench(lambda: x @ x)
flops = 2 * 8192 ** 3          # 每次 matmul 的 FLOPs
print(f"    cuBLAS fp16 8192 matmul : {ms:.3f} ms  ({flops / (ms / 1000) / 1e12:.1f} TFLOPS)")

conv = torch.nn.Conv2d(64, 64, 3, padding=1).cuda().half().to(memory_format=torch.channels_last)
i = torch.randn(32, 64, 224, 224, device='cuda', dtype=torch.float16).to(memory_format=torch.channels_last)
torch.backends.cudnn.benchmark = True
ms = bench(lambda: conv(i))
print(f"    cuDNN conv fp16+CL      : {ms:.3f} ms")
q = torch.randn(4096, 4096, device='cuda', dtype=torch.float16)
ms = bench(lambda: torch.nn.functional.scaled_dot_product_attention(q, q, q))
print(f"    SDPA fp16               : {ms:.3f} ms")
print(f"    VRAM peak               : {torch.cuda.max_memory_allocated()/1e6:.0f} MB")
PY

hr
echo "[5] 依升級前基準比對"
BEFORE="${BENCH_DIR}/bench_before_driver_update.json"
if [[ -f "$BEFORE" ]]; then
  echo "  基準檔: $BEFORE"
  python3 - "$BEFORE" <<'PY' 2>&1
import json, sys
b = json.load(open(sys.argv[1]))
mm = b.get("matmul") or {}
conv = b.get("conv") or {}
att = (b.get("attention") or {}).get("sdpa_fp16") or {}
print(f"    env              : {(b.get('env') or {}).get('torch')} / cuda {(b.get('env') or {}).get('cuda')} / cudnn {(b.get('env') or {}).get('cudnn')}")
print(f"    fp16 matmul      : {(mm.get('fp16') or {}).get('tflops', '?')} TFLOPS")
print(f"    fp32 tf32off/on  : {(mm.get('fp32 (tf32 off)') or {}).get('tflops', '?')} / {(mm.get('fp32 (tf32 on)') or {}).get('tflops', '?')} TFLOPS")
print(f"    conv mid56 fp16 CL: {(conv.get('mid 56 fp16 CL') or {}).get('ms', '?')} ms  VRAM {(conv.get('mid 56 fp16 CL') or {}).get('peak_mb', '?')} MB")
print(f"    SDPA fp16        : {att.get('ms', '?')} ms  {att.get('tflops', '?')} TFLOPS")
PY
else
  echo "  (找不到基準檔)"
fi

hr
if [[ "${FULL}" -eq 1 ]]; then
  echo "[6] ComfyUI 實機煙霧測試（每個 venv 各啟一次）"
  for venv in .venv .venv-nightly; do
    py="${COMFY}/${venv}/bin/python"
    [[ -x "$py" ]] || continue
    log="/tmp/comfy_smoke_$(basename "$venv").log"
    ( cd "$COMFY" && timeout 300 "$py" main.py --quick-test-for-ci --disable-auto-launch > "$log" 2>&1 )
    rc=$?
    fails=$(grep -icE 'import failed|failed to import' "$log")
    echo "  ${venv}: exit=${rc}  custom node 匯入失敗=${fails}"
    [[ $rc -ne 0 ]] && grep -E 'Error|Traceback' "$log" | head -5
  done
  hr
  echo "[7] A/B/C 基準對照"
  python3 "${SCRIPT_DIR}/compare.py" --dir "${BENCH_DIR}" 2>&1
  hr
fi

echo "完成。"
echo
echo "下一步（視需要）："
echo "  重新安裝 ComfyUI 的套件（上游改版後）:"
echo "    cd ${COMFY} && git pull && uv pip install --python .venv/bin/python -r requirements.txt"
echo "  裁掉用不到的 TensorRT 架構資源檔（RTX 4090 約可回收 3.2 GiB）:"
echo "    ${SCRIPT_DIR}/trim_tensorrt_resources.py --venv ${COMFY}/.venv-nightly"
echo "  開源後的每日排程（systemd user timer + 桌面通知）:"
echo "    ${SCRIPT_DIR}/install_systemd_user.sh"
