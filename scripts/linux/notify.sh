#!/usr/bin/env bash
# notify.sh — Linux 桌面通知（notify-send，不需第三方相依）
#
# 用法：notify.sh <urgency> <title> <body>
#   urgency: normal | critical
#
# 沒有桌面 session（純 SSH、headless、systemd 還沒連上 D-Bus）時不會失敗，
# 會退回寫 stderr（journald 會收進去），讓排程任務不會因為通知失敗而報錯。
set -uo pipefail

URGENCY="${1:-normal}"
TITLE="${2:-AI Stack Update Check}"
BODY="${3:-}"

if command -v notify-send >/dev/null 2>&1; then
  if notify-send --app-name="AI Stack Update Check" --urgency="$URGENCY" \
                  --icon=ai-stack-update-check "$TITLE" "$BODY" 2>/dev/null; then
    exit 0
  fi
fi

echo "[notify] $TITLE: $BODY" >&2
exit 0
