<#
  sync_cudnn.ps1 - 把 cuDNN 的檔案補進 CUDA toolkit 目錄（提權工作腳本）

  對每一個 CUDNN\v*\include\<cudaver> 配對，把缺少或不同的檔案複製到
  CUDA\v<cudaver>\ 的 bin（或 bin\x64）、include、lib\x64。

  為什麼要提權：目的在 C:\Program Files。這支腳本由排程工作
  「AI Stack cuDNN Sync」（RunLevel Highest）執行，所以不需要每次跳 UAC；
  check_stack.py 的 --fix-cudnn 與 run_daily.ps1 會按需觸發它。

  幂等：大小相同就 SKIP，所以可以重複執行。

  退出碼：0 = 沒有要補的（已齊全）、1 = 有補檔、2 = 發生錯誤。
#>
param(
    [string]$CudnnRoot = (Join-Path $env:ProgramFiles 'NVIDIA\CUDNN'),
    [string]$CudaRoot  = (Join-Path $env:ProgramFiles 'NVIDIA GPU Computing Toolkit\CUDA'),
    [string]$LogDir    = (Join-Path $env:USERPROFILE 'ai-stack-logs'),
    [string]$Only      = '',
    [switch]$WhatIf
)
$ErrorActionPreference = 'Continue'
$HashLimitMB = 64

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("cudnn-sync-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd'))

function Write-Line([string]$text) {
    $text | Out-File -FilePath $LogFile -Append -Encoding utf8
    Write-Output $text
}

function Get-Sha256([string]$path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($path)
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $sha.Dispose() }
}

Write-Line ("`n===== sync_cudnn {0:yyyy-MM-dd HH:mm:ss} (elevated={1}, whatif={2}) =====" -f `
    (Get-Date), $isAdmin, [bool]$WhatIf)
Write-Line ("CUDNN root: " + $CudnnRoot)
Write-Line ("CUDA  root: " + $CudaRoot)
if (-not $isAdmin) {
    Write-Line 'NOTE: not elevated - copying into Program Files will fail; run as admin or use the scheduled task'
}

if (-not (Test-Path $CudnnRoot)) {
    Write-Line ("RESULT: SKIP - CUDNN root not found")
    exit 0
}

$copied = 0
$skipped = 0
$errors = @()
$pairs = @()

foreach ($cudnnDir in (Get-ChildItem $CudnnRoot -Directory -Filter 'v*' -ErrorAction SilentlyContinue)) {
    if ($Only -and ($cudnnDir.Name -ne $Only)) { continue }
    foreach ($incDir in (Get-ChildItem (Join-Path $cudnnDir.FullName 'include') -Directory -ErrorAction SilentlyContinue)) {
        $cudaVer = $incDir.Name
        $cudaDir = Join-Path $CudaRoot ("v" + $cudaVer)
        if (-not (Test-Path $cudaDir)) {
            Write-Line ("[cuDNN {0} -> CUDA v{1}] SKIP - toolkit not installed" -f $cudnnDir.Name, $cudaVer)
            continue
        }
        $pairs += @{ Cudnn = $cudnnDir.Name; CudaVer = $cudaVer; CudnnDir = $cudnnDir.FullName; CudaDir = $cudaDir }
    }
}

if (-not $pairs) { Write-Line 'RESULT: SKIP - no CUDNN\v*\include\<ver> pairs found'; exit 0 }

foreach ($pair in $pairs) {
    Write-Line ("[cuDNN {0} -> CUDA v{1}]" -f $pair.Cudnn, $pair.CudaVer)

    # bin：優先選「已經有 cudnn DLL」的目的資料夾，其次 bin\x64（CUDA 13.x 佈局），最後 bin
    $binSrc = Join-Path $pair.CudnnDir ("bin\{0}\x64" -f $pair.CudaVer)
    $binDsts = @()
    foreach ($rel in 'bin\x64', 'bin') {
        $abs = Join-Path $pair.CudaDir $rel
        if ((Test-Path $abs) -and
            @(Get-ChildItem $abs -Filter 'cudnn*.dll' -File -ErrorAction SilentlyContinue).Count -gt 0) {
            $binDsts += $rel
        }
    }
    if (-not $binDsts) { if (Test-Path (Join-Path $pair.CudaDir 'bin\x64')) { $binDsts = @('bin\x64') } else { $binDsts = @('bin') } }

    $jobs = @(
        @{ Name = 'bin';     Src = $binSrc;                                            Dst = $binDsts;     Pat = '*.dll' },
        @{ Name = 'include'; Src = (Join-Path $pair.CudnnDir ("include\{0}" -f $pair.CudaVer)); Dst = @('include'); Pat = '*.h' },
        @{ Name = 'lib';     Src = (Join-Path $pair.CudnnDir ("lib\{0}\x64" -f $pair.CudaVer)); Dst = @('lib\x64'); Pat = '*.lib' }
    )

    foreach ($job in $jobs) {
        $destRel = $job.Dst[0]
        $destAbs = Join-Path $pair.CudaDir $destRel
        $srcFiles = @(Get-ChildItem $job.Src -Filter $job.Pat -File -ErrorAction SilentlyContinue)
        if (-not $srcFiles) { Write-Line ("  {0,-8} SOURCE MISSING: {1}" -f $job.Name, $job.Src); $errors += $job.Src; continue }
        if (-not (Test-Path $destAbs)) {
            if ($WhatIf) { Write-Line ("  {0,-8} would create {1}" -f $job.Name, $destAbs) }
            else { New-Item -ItemType Directory -Force -Path $destAbs | Out-Null }
        }
        $localCopied = 0
        foreach ($f in $srcFiles) {
            $target = Join-Path $destAbs $f.Name
            $same = (Test-Path $target) -and ((Get-Item $target).Length -eq $f.Length)
            if ($same) { $skipped++; continue }
            if ($WhatIf) { Write-Line ("  {0,-8} would copy {1}" -f $job.Name, $f.Name); $localCopied++; continue }
            try {
                Copy-Item $f.FullName $target -Force -ErrorAction Stop
                $localCopied++
            } catch {
                Write-Line ("  {0,-8} FAIL {1} :: {2}" -f $job.Name, $f.Name, $_.Exception.Message)
                $errors += $f.Name
            }
        }
        $copied += $localCopied
        $present = @($srcFiles | Where-Object {
            $t = Join-Path $destAbs $_.Name
            (Test-Path $t) -and ((Get-Item $t).Length -eq $_.Length)
        }).Count
        Write-Line ("  {0,-8} -> {1}  matched {2}/{3}, copied {4}" -f `
            $job.Name, $destAbs, $present, $srcFiles.Count, $localCopied)
    }
}

# ---- 驗收：逐檔比對（<= 64 MB 用 SHA256，大檔比大小）----
Write-Line ''
Write-Line 'VERIFY (source vs CUDA toolkit):'
$mismatch = 0
$byHash = 0
$bySize = 0
foreach ($pair in $pairs) {
    $checks = @(
        @{ Src = (Join-Path $pair.CudnnDir ("bin\{0}\x64" -f $pair.CudaVer)); Pat = '*.dll'; Dsts = @('bin\x64', 'bin') },
        @{ Src = (Join-Path $pair.CudnnDir ("include\{0}" -f $pair.CudaVer)); Pat = '*.h';   Dsts = @('include') },
        @{ Src = (Join-Path $pair.CudnnDir ("lib\{0}\x64" -f $pair.CudaVer)); Pat = '*.lib'; Dsts = @('lib\x64') }
    )
    foreach ($check in $checks) {
        foreach ($f in @(Get-ChildItem $check.Src -Filter $check.Pat -File -ErrorAction SilentlyContinue)) {
            $found = $false
            foreach ($rel in $check.Dsts) {
                $target = Join-Path (Join-Path $pair.CudaDir $rel) $f.Name
                if (-not (Test-Path $target)) { continue }
                if ((Get-Item $target).Length -ne $f.Length) { continue }
                if ($f.Length -le ($HashLimitMB * 1MB)) {
                    if ((Get-Sha256 $target) -ne (Get-Sha256 $f.FullName)) { continue }
                    $byHash++
                } else { $bySize++ }
                $found = $true
                break
            }
            if (-not $found) { $mismatch++; Write-Line ('  MISSING/DIFF: ' + $f.Name) }
        }
    }
}
Write-Line ("  hash-verified={0} size-only={1} mismatches={2}" -f $byHash, $bySize, $mismatch)
Write-Line ''
Write-Line ("SUMMARY copied={0} skipped={1} errors={2}" -f $copied, $skipped, $errors.Count)

if ($errors.Count -gt 0) { Write-Line 'RESULT: ERROR - see FAIL lines above'; exit 2 }
if ($mismatch -gt 0) { Write-Line 'RESULT: INCOMPLETE - some files are still missing or different'; exit 2 }
if ($WhatIf) { Write-Line 'RESULT: DRY-RUN - nothing was written'; exit 0 }
if ($copied -gt 0) { Write-Line 'RESULT: COPIED - cuDNN files are now complete'; exit 1 }
Write-Line 'RESULT: OK - already complete (nothing to do)'
exit 0
