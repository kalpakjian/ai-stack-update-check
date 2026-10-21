# notify.ps1 — 發送 Windows 11 toast 通知（不需外部模組）
# 用法： powershell -File notify.ps1 -Title "..." -Body "line1`nline2"
param(
    [Parameter(Mandatory = $true)][string]$Title,
    [string]$Body = "",
    [string]$AppId = "AIStack.UpdateCheck"
)

$ErrorActionPreference = "Stop"

function Esc([string]$s) {
    if ([string]::IsNullOrEmpty($s)) { return "" }
    return $s.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;').Replace('"', '&quot;')
}

try {
    # 1) 註冊 AppUserModelID（只需一次；重複執行無害）
    $key = "HKCU:\Software\Classes\AppUserModelId\$AppId"
    if (-not (Test-Path $key)) {
        New-Item -Path $key -Force | Out-Null
        New-ItemProperty -Path $key -Name "DisplayName" -Value "AI Stack Check" `
            -PropertyType String -Force | Out-Null
        New-ItemProperty -Path $key -Name "ShowInSettings" -Value 1 `
            -PropertyType DWord -Force | Out-Null
        $icon = $null
        $found = Get-Command python -ErrorAction SilentlyContinue
        if ($found -and $found.Source -and (Test-Path $found.Source)) {
            $icon = $found.Source
        }
        if ($icon) {
            New-ItemProperty -Path $key -Name "IconUri" -Value $icon `
                -PropertyType String -Force | Out-Null
        }
    }

    # 2) 載入 WinRT 型別
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

    # 3) 組 XML（Title + 多行 Body）
    $texts = "<text>$(Esc $Title)</text>"
    if ($Body) {
        foreach ($line in ($Body -split "`n")) {
            if ($line.Trim()) { $texts += "<text>$(Esc $line.Trim())</text>" }
        }
    }
    $xml = @"
<toast activationType="foreground" scenario="reminder">
  <visual>
    <binding template="ToastGeneric">
      $texts
    </binding>
  </visual>
  <audio src="ms-winsoundevent:Notification.Reminder" />
</toast>
"@

    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml($xml)
    $toast = New-Object Windows.UI.Notifications.ToastNotification $doc
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show($toast)

    Write-Output "toast sent: $Title"
    exit 0
}
catch {
    Write-Output "toast failed: $($_.Exception.Message)"
    exit 1
}
