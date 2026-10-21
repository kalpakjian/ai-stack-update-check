# run_daily.ps1 — 每日排程：檢查版本；可選自動升級（失敗自動回滾）；有變更/更新時 toast。
param(
    [string]$Python = "",
    [string]$LogDir = (Join-Path $env:USERPROFILE "ai-stack-logs"),
    [switch]$AutoUpdate,
    [switch]$Bench,
    [switch]$ForceNotify,
    [switch]$DryRun
)
$ErrorActionPreference = "Continue"

$ScriptDir = $PSScriptRoot
$Script = Join-Path $ScriptDir "check_stack.py"
$AutoScript = Join-Path $ScriptDir "auto_update.py"
$Notify = Join-Path $ScriptDir "notify.ps1"
$KeepDays = 90

if (-not $Python) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) { $Python = $found.Source } else { $Python = "python" }
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$today = Get-Date -Format "yyyy-MM-dd"
$log = Join-Path $LogDir ("stack-check-{0}.log" -f $today)
$latest = Join-Path $LogDir "stack-check-latest.log"
$status = Join-Path $LogDir "stack-check-status.txt"
$reportPath = Join-Path $LogDir "stack-report.json"
$autoSummaryPath = Join-Path $LogDir "auto-update-summary.json"

function Write-Log([string]$text) {
    $text | Out-File -FilePath $log -Append -Encoding utf8
}

Remove-Item $reportPath, $autoSummaryPath -Force -ErrorAction SilentlyContinue

Write-Log ("`n===== {0:yyyy-MM-dd HH:mm:ss} =====" -f (Get-Date))
Write-Log ("Mode: AutoUpdate={0} DryRun={1} Bench={2}" -f $AutoUpdate, $DryRun, $Bench)

# 1) 版本檢查（一律先做；--runtime 會匯入 torch 實測 CUDA 與 cuDNN）
$checkArgs = @("-u", $Script, "--json-out", $reportPath, "--runtime")
if ($Bench -and -not $AutoUpdate) { $checkArgs += "--bench" }
$output = & $Python @checkArgs 2>&1
$code = $LASTEXITCODE
$output | ForEach-Object { Write-Log ([string]$_) }

$updates = @()
foreach ($line in $output) {
    if ([string]$line -match '^(\S+)\s+.*\supdate available') {
        $updates += $Matches[1]
    }
}

# 1b) cuDNN 狀態（報告的 cudnn 區塊）；不可用時要在狀態檔與通知看得到
$cudnnProblem = $null
if (Test-Path $reportPath) {
    try {
        $rpt = Get-Content $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $cudnnStatus = [string]$rpt.cudnn.status
        Write-Log ("CUDNN: status={0} installed={1} libs={2} dll={3}" -f `
            $cudnnStatus, $rpt.cudnn.installed, $rpt.cudnn.libs, $rpt.cudnn.dll)
        if ($cudnnStatus -in @("not available", "not found", "check failed")) {
            $cudnnProblem = "CUDNN_PROBLEM: $cudnnStatus"
        }
    } catch {
        Write-Log ("CUDNN: report parse failed: " + $_.Exception.Message)
    }
}

# 2) 自動更新（可選）
$toastTitle = $null
$toastBody = $null
$statusText = "OK"

if ($AutoUpdate) {
    Write-Log "`n===== AUTO UPDATE ====="
    $autoArgs = @("-u", $AutoScript, "--report", $reportPath, "--json-out", $autoSummaryPath)
    if ($DryRun) { $autoArgs += "--dry-run" }
    if ($Bench) { $autoArgs += "--bench" }
    $autoOut = & $Python @autoArgs 2>&1
    $autoCode = $LASTEXITCODE
    $autoOut | ForEach-Object { Write-Log ([string]$_) }
    Write-Log ("AUTO EXIT={0}" -f $autoCode)

    if (Test-Path $autoSummaryPath) {
        $s = Get-Content $autoSummaryPath -Raw | ConvertFrom-Json
        $names = @($s.upgraded.PSObject.Properties.Name)
        $result = [string]$s.result
        switch ($result) {
            "success" {
                if ($names.Count -gt 0) {
                    $statusText = "AUTO_UPDATED: " + ($names -join ", ")
                    $toastTitle = "AI Stack: $($names.Count) package(s) auto-updated"
                    $toastBody = ($names -join ", ") + "`nSmoke: $($s.smoke) | pip check: $($s.pip_check)"
                } else {
                    $statusText = "OK"
                }
            }
            "no-op" { $statusText = "OK" }
            "dry-run" { $statusText = "DRY_RUN" }
            "rolled-back" {
                $statusText = "ROLLED_BACK: " + ($names -join ", ")
                $toastTitle = "AI Stack: update failed - rolled back"
                $toastBody = "Environment restored. Details: stack-check-latest.log"
            }
            "rollback-failed" {
                $statusText = "ROLLBACK_FAILED"
                $toastTitle = "AI Stack: UPDATE FAILED - manual fix needed"
                $toastBody = "Rollback did not fully restore. Check today's log."
            }
            default { $statusText = "AUTO_" + $result.ToUpper() }
        }
    }
} else {
    if ($code -eq 1) {
        $statusText = "UPDATES_AVAILABLE: " + ($updates -join ", ")
        $toastTitle = "AI Stack: $($updates.Count) update(s) available"
        $toastBody = "Updates: " + ($updates -join ", ") + "`nRun with -AutoUpdate to install"
    }
}

# 2b) cuDNN 異常優先於 OK／UPDATES_AVAILABLE（回滾類訊息優先權最高，不覆蓋）
if ($cudnnProblem -and $statusText -notlike "ROLLBACK_FAILED*" -and $statusText -notlike "ROLLED_BACK*") {
    $statusText = $cudnnProblem
    if (-not $toastTitle) {
        $toastTitle = "AI Stack: cuDNN not usable"
        $toastBody = "torch.backends.cudnn is not usable.`nSee stack-check-latest.log"
    }
}

$statusText | Out-File -FilePath $status -Encoding utf8
"See $log" | Out-File -FilePath $status -Append -Encoding utf8

# 3) 通知
if (-not $toastTitle -and $ForceNotify) {
    $toastTitle = "AI Stack: test notification"
    $toastBody = "AutoUpdate=$AutoUpdate DryRun=$DryRun Status=$statusText"
}
if ($toastTitle -and (Test-Path $Notify)) {
    $toastResult = & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden `
        -File $Notify -Title $toastTitle -Body $toastBody 2>&1
    Write-Log ("TOAST: {0}" -f $toastResult)
}

# 4) 摘要與清理
Get-Content $log -Tail 60 | Out-File -FilePath $latest -Encoding utf8

Get-ChildItem $LogDir -Filter "stack-check-*.log" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

# 對排程器而言，0 = 執行成功（有無更新都算成功）
exit 0
