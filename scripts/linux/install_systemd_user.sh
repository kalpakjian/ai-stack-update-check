#!/usr/bin/env bash
# install_systemd_user.sh — 安裝／移除 systemd **user** 排程（Linux）
#
# 用法：
#   ./install_systemd_user.sh              # 安裝並啟用
#   ./install_systemd_user.sh --uninstall  # 停用並移除
#   ./install_systemd_user.sh --no-enable  # 只安裝 unit，先不要啟用
#
# 用 user scope（不是 system scope）的好處：
#   * 不需要 root 權限
#   * 桌面通知一定送得到（跟著你的登入 session）
#   * 日誌留在 ~/.local/state/ai-stack-update-check/
#
# 想讓「登出狀態」也能跑（開機自動執行、不需要登入）：
#   sudo loginctl enable-linger "$USER"
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_NAME="ai-stack-check.service"
TIMER_NAME="ai-stack-check.timer"

MODE="install"
case "${1:-}" in
  --uninstall) MODE="uninstall" ;;
  --no-enable) MODE="no-enable" ;;
  "") ;;
  *) echo "未知參數：$1"; exit 2 ;;
esac

if [[ "${MODE}" == "uninstall" ]]; then
  systemctl --user disable --now "${TIMER_NAME}" 2>/dev/null || true
  rm -f "${UNIT_DIR}/${SERVICE_NAME}" "${UNIT_DIR}/${TIMER_NAME}"
  systemctl --user daemon-reload
  echo "[OK] 已移除 ${SERVICE_NAME} 與 ${TIMER_NAME}"
  exit 0
fi

mkdir -p "${UNIT_DIR}"
sed "s|__REPO_DIR__|${REPO_DIR}|g" "${SCRIPT_DIR}/ai-stack-check.service.in" \
    > "${UNIT_DIR}/${SERVICE_NAME}"
cp "${SCRIPT_DIR}/ai-stack-check.timer" "${UNIT_DIR}/${TIMER_NAME}"
chmod +x "${SCRIPT_DIR}"/*.sh 2>/dev/null || true

systemctl --user daemon-reload
echo "[OK] 已安裝 unit 到 ${UNIT_DIR}"

if [[ "${MODE}" == "no-enable" ]]; then
  echo "（依參數未啟用 timer；執行 systemctl --user enable --now ${TIMER_NAME} 啟用）"
  exit 0
fi

systemctl --user enable --now "${TIMER_NAME}"
echo "[OK] 已啟用 ${TIMER_NAME}"
echo
systemctl --user list-timers "${TIMER_NAME}" --no-pager || true
echo
echo "立刻手動跑一次："
echo "  systemctl --user start ${SERVICE_NAME}"
echo "看結果："
echo "  journalctl --user -u ${SERVICE_NAME} -n 50 --no-pager"
echo "  cat \${XDG_STATE_HOME:-\$HOME/.local/state}/ai-stack-update-check/stack-check-status.txt"
