param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeRoot,
    [string]$LogRoot = "",
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
$runtime = (Resolve-Path -LiteralPath $RuntimeRoot).Path
if ([string]::IsNullOrWhiteSpace($LogRoot)) {
    $LogRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "build\three-entry-smoke"
}
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
$LogRoot = (Resolve-Path -LiteralPath $LogRoot).Path

$required = @(
    "TuiGuangTool.exe",
    "_internal",
    "app.py",
    "core"
)
$missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $runtime $_)) })
if ($missing.Count -gt 0) {
    throw "Runtime is incomplete: $($missing -join ', ')"
}
$batchFiles = @(Get-ChildItem -LiteralPath $runtime -Filter "*.bat" -File)
if ($batchFiles.Count -lt 2) {
    throw "Runtime must contain two batch entry points."
}
$primaryBatch = $batchFiles | Where-Object {
    (Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8) -match "TuiGuangTool\.exe"
} | Select-Object -First 1
if ($null -eq $primaryBatch) {
    throw "Runtime does not contain a batch entry point for TuiGuangTool.exe."
}
$compatibilityBatch = $batchFiles | Where-Object { $_.FullName -ne $primaryBatch.FullName } |
    Select-Object -First 1
if ($null -eq $compatibilityBatch) {
    throw "Runtime must contain distinct primary and compatibility batch entry points."
}
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $PythonExecutable = (Get-Command "python.exe" -ErrorAction Stop).Source
}

$createdPids = [System.Collections.Generic.List[int]]::new()
$results = [System.Collections.Generic.List[object]]::new()
$oldHeadless = $env:METOX_HEADLESS
$oldAppTest = $env:METOX_APP_TEST
$env:METOX_HEADLESS = "1"

function Wait-MetoXUrl {
    param([string]$StdoutPath, [System.Diagnostics.Process]$Process)
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if (Test-Path -LiteralPath $StdoutPath) {
            $content = Get-Content -LiteralPath $StdoutPath -Raw -ErrorAction SilentlyContinue
            if ($content -match "METOX_URL=http://127\.0\.0\.1:(\d+)") {
                return [int]$Matches[1]
            }
        }
        if ($Process.HasExited) {
            throw "Entry exited before emitting METOX_URL (exit=$($Process.ExitCode))."
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Timed out waiting for METOX_URL in $StdoutPath"
}

function Wait-MetoXHealth {
    param([int]$Port, [System.Diagnostics.Process]$Process, [string]$StderrPath)
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($Process.HasExited) {
            $details = Get-Content -LiteralPath $StderrPath -Raw -ErrorAction SilentlyContinue
            throw "Entry exited before health check (exit=$($Process.ExitCode)): $details"
        }
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/_stcore/health" -UseBasicParsing -TimeoutSec 2
            if ([int]$response.StatusCode -eq 200) {
                return $response
            }
        } catch {
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Health check timed out on port $Port"
}

function Test-MetoXEntry {
    param([string]$Name, [string]$Path, [bool]$IsBatch)
    $stdout = Join-Path $LogRoot "$Name.stdout.log"
    $stderr = Join-Path $LogRoot "$Name.stderr.log"
    if ($IsBatch) {
        $entry = Start-Process -FilePath $env:ComSpec `
            -ArgumentList @("/d", "/c", ('"' + $Path + '"')) `
            -WorkingDirectory $runtime `
            -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr `
            -PassThru -WindowStyle Hidden
    } else {
        $entry = Start-Process -FilePath $Path `
            -WorkingDirectory $runtime `
            -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr `
            -PassThru -WindowStyle Hidden
    }
    $createdPids.Add($entry.Id)
    $listenerPid = $null
    try {
        $port = Wait-MetoXUrl -StdoutPath $stdout -Process $entry
        if ($port -eq 8765) {
            throw "$Name reused occupied port 8765"
        }
        $health = Wait-MetoXHealth -Port $port -Process $entry -StderrPath $stderr
        $rootResponse = Invoke-WebRequest -Uri "http://127.0.0.1:$port" -UseBasicParsing -TimeoutSec 5
        $listenerPid = [int](
            Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $port -State Listen |
                Select-Object -First 1
        ).OwningProcess
        if (-not $createdPids.Contains($listenerPid)) {
            $createdPids.Add($listenerPid)
        }
        $results.Add([pscustomobject]@{
            Entry = $Name
            SelectedPort = $port
            Health = [int]$health.StatusCode
            Root = [int]$rootResponse.StatusCode
            ListenerPid = $listenerPid
        })
    } finally {
        if ($null -ne $listenerPid) {
            Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $entry.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
}

function Test-MetoXAppExecution {
    $stdout = Join-Path $LogRoot "app_execution.stdout.log"
    $stderr = Join-Path $LogRoot "app_execution.stderr.log"
    $env:METOX_APP_TEST = "1"
    try {
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = Join-Path $runtime "TuiGuangTool.exe"
        $startInfo.WorkingDirectory = $runtime
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "Packaged app execution process did not start."
        }
        $createdPids.Add($process.Id)
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(90000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "Packaged app execution test timed out."
        }
        $process.WaitForExit()
        $exitCode = $process.ExitCode
        $output = $stdoutTask.Result
        $errorOutput = $stderrTask.Result
        Set-Content -LiteralPath $stdout -Value $output -Encoding UTF8
        Set-Content -LiteralPath $stderr -Value $errorOutput -Encoding UTF8
        if ($exitCode -ne 0 -or $output -notmatch "METOX_APP_TEST=PASS") {
            throw "Packaged app execution failed (exit=$exitCode): $output $errorOutput"
        }
        return [pscustomobject]@{
            Status = "PASS"
            ExitCode = $exitCode
            Marker = "METOX_APP_TEST=PASS"
        }
    } finally {
        $env:METOX_APP_TEST = $oldAppTest
    }
}

$blocker = $null
try {
    $appExecution = Test-MetoXAppExecution
    $existing8765 = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if (-not $existing8765) {
        $blocker = Start-Process -FilePath $PythonExecutable `
            -ArgumentList @("-m", "http.server", "8765", "--bind", "127.0.0.1") `
            -WorkingDirectory $runtime `
            -RedirectStandardOutput (Join-Path $LogRoot "blocker.stdout.log") `
            -RedirectStandardError (Join-Path $LogRoot "blocker.stderr.log") `
            -PassThru -WindowStyle Hidden
        $createdPids.Add($blocker.Id)
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {
                break
            }
            if ($blocker.HasExited) {
                throw "Port blocker exited unexpectedly (exit=$($blocker.ExitCode))."
            }
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)) {
        throw "Could not establish the occupied-port test condition on 8765."
    }

    Test-MetoXEntry -Name "direct_exe" -Path (Join-Path $runtime "TuiGuangTool.exe") -IsBatch $false
    Test-MetoXEntry -Name "primary_bat" -Path $primaryBatch.FullName -IsBatch $true
    Test-MetoXEntry -Name "compatibility_bat" -Path $compatibilityBatch.FullName -IsBatch $true
    [pscustomobject]@{
        AppExecution = $appExecution
        EntrySmoke = $results
    } | ConvertTo-Json -Depth 4
} finally {
    foreach ($createdPid in ($createdPids | Sort-Object -Unique -Descending)) {
        Stop-Process -Id $createdPid -Force -ErrorAction SilentlyContinue
    }
    $env:METOX_HEADLESS = $oldHeadless
    $env:METOX_APP_TEST = $oldAppTest
}
