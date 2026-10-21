<#
  register_cudnn_sync_task.ps1 - 註冊提權排程工作「AI Stack cuDNN Sync」（只需執行一次）

  為什麼要這個工作：cuDNN 的檔案要複製到 C:\Program Files\...\CUDA\vX.Y\（需要管理員），
  但每日的 AI Stack 排程是「非提權」執行。做法是註冊一個「最高權限」的專用工作，
  之後非提權的 check_stack.py / run_daily.ps1 只要 Start-ScheduledTask 就能觸發它
  （自己啟動自己的工作不會跳 UAC），於是補檔也能自動化。

  用法（以「系統管理員」開啟 PowerShell）：
      powershell -NoProfile -ExecutionPolicy Bypass -File register_cudnn_sync_task.ps1
      powershell -NoProfile -ExecutionPolicy Bypass -File register_cudnn_sync_task.ps1 -Unregister
      powershell -NoProfile -ExecutionPolicy Bypass -File register_cudnn_sync_task.ps1 -DryRun

  註冊後可驗證：
      schtasks /query /tn "AI Stack cuDNN Sync" /v /fo list
      schtasks /run  /tn "AI Stack cuDNN Sync"      # 手動觸發一次（會提權執行）
      Get-Content "$env:USERPROFILE\ai-stack-logs\cudnn-sync-<date>.log"
#>
param(
    [string]$TaskName = 'AI Stack cuDNN Sync',
    [string]$LogDir = (Join-Path $env:USERPROFILE 'ai-stack-logs'),
    [switch]$Unregister,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Output 'ERROR: this script must run as Administrator (right-click PowerShell -> Run as administrator).'
    exit 2
}

if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) { Write-Output ("Task not found: " + $TaskName); exit 0 }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output ("Unregistered: " + $TaskName)
    exit 0
}

$SyncScript = Join-Path $PSScriptRoot 'sync_cudnn.ps1'
if (-not (Test-Path $SyncScript)) {
    Write-Output ('ERROR: sync_cudnn.ps1 not found next to this script: ' + $SyncScript)
    exit 2
}

$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
             $SyncScript + '" -LogDir "' + $LogDir + '"'

Write-Output ('Task name : ' + $TaskName)
Write-Output ('Execute   : ' + $ps)
Write-Output ('Arguments : ' + $arguments)
Write-Output ('Principal : ' + $env:USERNAME + ' (Interactive, RunLevel Highest)')
Write-Output ('Trigger   : none (on demand; check_stack.py --fix-cudnn / run_daily.ps1 trigger it)')

if ($DryRun) { Write-Output 'DRY-RUN: nothing was registered.'; exit 0 }

$action = New-ScheduledTaskAction -Execute $ps -Argument $arguments
$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Principal $principal `
    -Settings $settings -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
Write-Output ('Registered: {0} | State={1} | RunLevel={2}' -f $task.TaskName, $task.State, $task.Principal.RunLevel)
Write-Output ''
Write-Output 'Next steps:'
Write-Output ('  1) test it once:  schtasks /run /tn "' + $TaskName + '"')
Write-Output ('  2) check the log: Get-Content "' + (Join-Path $LogDir 'cudnn-sync-<today>.log') + '"')
Write-Output '  3) then the daily job can self-heal: run_daily.ps1 detects the task and passes --fix-cudnn'
exit 0
