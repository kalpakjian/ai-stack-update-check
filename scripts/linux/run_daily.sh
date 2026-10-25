#!/usr/bin/env bash
# run_daily.sh — Linux 每日 AI 堆疊檢查（由 systemd user timer 呼叫）
#
# 流程：
#   1. check_stack.py --runtime --json  → 套件／CUDA／cuDNN／驅動／系統更新
#   2. audit_venv.py（每個 venv 各一次） → 22 項堆疊完整性檢查
#   3. 寫日誌（保留 90 天）＋狀態檔
#   4. 桌面通知（notify.sh）
#
# 環境變數：
#   PYTHON   跑檢查用的直譯器（預設 $COMFY/.venv/bin/python，要能 import torch）
#   COMFY    ComfyUI 根目錄（預設 $HOME/Projects/ComfyUI）
#   LOG_DIR  日誌目錄（預設 ${XDG_STATE_HOME:-$HOME/.local/state}/ai-stack-update-check）
#   VENVS    要稽核的 venv（空白分隔；預設 "$COMFY/.venv $COMFY/.venv-nightly"）
#
# 注意：這裡 **只檢查不升級**。Linux 上自動升級的風險比 Windows 高：
#   * pacman 是 rolling release，partial upgrade 會直接把系統弄壞
#   * NVIDIA 驅動是 DKMS 套件，升級後 **一定要重開機** 才會換到新驅動
#   * PyTorch 的 cu12/cu13 wheel 會安裝到重疊路徑，pip 升級可能蓋掉錯的 .so
# 所以自動升級留在人工確認之後做，這支腳本負責「把狀態看清楚並通知你」。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

COMFY="${COMFY:-$HOME/Projects/ComfyUI}"
PYTHON="${PYTHON:-${COMFY}/.venv/bin/python}"
LOG_DIR="${LOG_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/ai-stack-update-check}"
VENVS="${VENVS:-${COMFY}/.venv ${COMFY}/.venv-nightly}"
RETENTION_DAYS="${RETENTION_DAYS:-90}"

STAMP="$(date '+%Y-%m-%d')"
LOG_FILE="${LOG_DIR}/stack-check-${STAMP}.log"
STATUS_FILE="${LOG_DIR}/stack-check-status.txt"
REPORT_FILE="${LOG_DIR}/stack-check-report-${STAMP}.json"

mkdir -p "${LOG_DIR}"

log() { printf '%s\n' "$*" | tee -a "${LOG_FILE}"; }

log "============================================================"
log "AI Stack daily check   $(date '+%Y-%m-%d %H:%M:%S %Z')"
log "============================================================"
log "python   : ${PYTHON}"
log "comfy    : ${COMFY}"
log "venvs    : ${VENVS}"

# ---------------------------------------------------------------- 1. 堆疊檢查
STATUS="OK"
SUMMARY=""
UPDATES_COUNT=0
WARNING_COUNT=0

if [[ ! -x "${PYTHON}" ]]; then
  log "[FAIL] 找不到 python：${PYTHON}"
  STATUS="PROBLEM: python not found"
else
  log ""
  log "[1] stack check (check_stack.py --runtime --json)"
  "${PYTHON}" "${REPO_DIR}/scripts/check_stack.py" --runtime \
       --json-out "${REPORT_FILE}" 2>&1 | tee -a "${LOG_FILE}"
  CHECK_RC=${PIPESTATUS[0]}
  # exit code 0 = 沒有更新，1 = 有更新，2 = 錯誤
  case "${CHECK_RC}" in
    0) UPDATES_COUNT=0 ;;
    1) UPDATES_COUNT=1 ;;
    *) STATUS="PROBLEM: check_stack.py failed (rc=${CHECK_RC})" ;;
  esac

  if [[ -f "${REPORT_FILE}" ]]; then
    read -r UPDATES_COUNT WARNING_COUNT SUMMARY < <("${PYTHON}" - "${REPORT_FILE}" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("0 0 (報告無法解析)")
    raise SystemExit(0)
updates = d.get("updates") or []
warnings = d.get("warnings") or []
parts = []
if updates:
    parts.append("有更新：" + ", ".join(updates[:6]) + ("…" if len(updates) > 6 else ""))
if warnings:
    parts.append("注意：" + warnings[0][:160])
cudnn = d.get("cudnn") or {}
if cudnn.get("status") not in (None, "ok"):
    parts.append("cuDNN 狀態異常：" + str(cudnn.get("status")))
print(len(updates), len(warnings), "；".join(parts) if parts else "全部都是最新的")
PY
)
    log ""
    log "[1] 摘要：${SUMMARY}"
  fi
fi

# ---------------------------------------------------------------- 2. venv 稽核
log ""
log "[2] venv audit"
AUDIT_FAILED=0
for venv in ${VENVS}; do
  [[ -x "${venv}/bin/python" ]] || { log "  [${venv}] 不存在，略過"; continue; }
  log "  --- ${venv} ---"
  if "${PYTHON}" "${SCRIPT_DIR}/audit_venv.py" "${venv}" --label "$(basename "${venv}")" \
       2>&1 | tee -a "${LOG_FILE}"; then
    log "  ${venv}: PASS"
  else
    log "  ${venv}: FAIL"
    AUDIT_FAILED=1
    STATUS="PROBLEM: audit failed for $(basename "${venv}")"
  fi
done

# ---------------------------------------------------------------- 3. 收尾
log ""
if [[ "${AUDIT_FAILED}" -eq 0 && "${STATUS}" == "OK" ]]; then
  if [[ "${UPDATES_COUNT}" -gt 0 ]]; then
    STATUS="UPDATES: ${SUMMARY}"
  else
    STATUS="OK"
  fi
fi
printf '%s\n' "${STATUS}" > "${STATUS_FILE}"
log "狀態：${STATUS}"

# 90 天前的日誌刪掉
find "${LOG_DIR}" -maxdepth 1 -name 'stack-check-*.log' -mtime "+${RETENTION_DAYS}" \
     -delete 2>/dev/null || true
find "${LOG_DIR}" -maxdepth 1 -name 'stack-check-report-*.json' -mtime "+${RETENTION_DAYS}" \
     -delete 2>/dev/null || true

# ---------------------------------------------------------------- 4. 通知
if [[ "${STATUS}" == "OK" ]]; then
  "${SCRIPT_DIR}/notify.sh" normal "AI Stack 檢查完成" "${SUMMARY}"
elif [[ "${STATUS}" == PROBLEM* ]]; then
  "${SCRIPT_DIR}/notify.sh" critical "AI Stack 檢查異常" "${STATUS}"
else
  "${SCRIPT_DIR}/notify.sh" normal "AI Stack 有更新" "${SUMMARY}"
fi

log ""
log "日誌：${LOG_FILE}"
exit 0
