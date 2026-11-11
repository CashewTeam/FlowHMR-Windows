param(
    [ValidateRange(1, 65535)][int]$Port = 60550,
    [Alias('NoBrowser')][switch]$ServerOnly,
    [ValidatePattern('^Local\\FlowHMR-backend-[0-9a-f]{32}$')][string]$DesktopJobName
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $projectRoot
. (Join-Path $PSScriptRoot 'desktop-runtime.ps1')
if ($DesktopJobName) {
    if (-not $ServerOnly) { throw 'A desktop job can only start the server.' }
    Join-FlowHMRDesktopJob $DesktopJobName
}
$pythonExe = Get-FlowHMRConsoleRuntime $projectRoot
if (-not $ServerOnly) {
    $launcher = Join-Path $projectRoot 'FlowHMR.exe'
    if (Test-Path -LiteralPath $launcher -PathType Leaf) {
        Start-Process -FilePath $launcher -ArgumentList @('--port',"$Port") -WorkingDirectory $projectRoot -WindowStyle Normal
        exit 0
    }
    $desktopExe = Get-FlowHMRDesktopRuntime $projectRoot
    Start-Process -FilePath $desktopExe -ArgumentList @('-I','tools\windows\desktop_launcher.pyw','--port',"$Port") -WorkingDirectory $projectRoot -WindowStyle Normal
    exit 0
}
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:HF_HOME = Join-Path $projectRoot '.cache\huggingface'
$env:TORCH_HOME = Join-Path $projectRoot '.cache\torch'
$env:FLOWHMR_SAM_COMPILE = '0'
$env:FLOWHMR_ATTENTION_BACKEND = 'query_batched'
$env:FLOWHMR_ATTENTION_QUERY_CHUNK_SIZE = '256'
$url = "http://127.0.0.1:$Port"
$logDir = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stateFile = Join-Path $logDir 'web-process.json'
$serverProcess = $null
if (Test-Path -LiteralPath $stateFile) {
    $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    if ($state.root -eq $projectRoot) {
        $recordedId = [int]$state.pid
        $recordedPort = [int]$state.port
        $recorded = Get-CimInstance Win32_Process -Filter "ProcessId = $recordedId"
        if ($recorded.ExecutablePath -eq $pythonExe -and $recorded.CommandLine -match "\bapp\.py\b.*--host\s+127\.0\.0\.1\s+--port\s+$recordedPort\b") {
            if ($recordedPort -ne $Port) { throw "FlowHMR is already running on port $recordedPort. Stop it before changing the port." }
            $serverProcess = Get-Process -Id $recordedId -ErrorAction SilentlyContinue
        }
    }
}
if ($null -eq $serverProcess) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try { $listener.Start() } catch { throw "Port $Port is occupied. Choose another port with -Port or stop the existing server." } finally { $listener.Stop() }
    $serverProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-u','app.py','--host','127.0.0.1','--port',"$Port",'--device','cuda','--sam-chunk-default','24') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'web.stdout.log') -RedirectStandardError (Join-Path $logDir 'web.stderr.log') -PassThru
    @{pid=$serverProcess.Id;port=$Port;root=$projectRoot} | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
    Write-Host "Starting FlowHMR. Logs: $logDir"
}
$deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $deadline) {
    if ($serverProcess.HasExited) {
        Get-Content -LiteralPath (Join-Path $logDir 'web.stderr.log') -Tail 40
        throw 'FlowHMR exited during startup.'
    }
    try {
        $response = Invoke-WebRequest -Uri "$url/api/info" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Host "FlowHMR ready: $url"
            exit 0
        }
    } catch {}
    Start-Sleep -Milliseconds 500
}
throw "Startup is still running. Inspect $logDir or run stop-web.cmd."
