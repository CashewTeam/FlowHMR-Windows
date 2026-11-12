param([int]$ExpectedPid = 0)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$stateFile = Join-Path $projectRoot 'logs\web-process.json'
if (-not (Test-Path -LiteralPath $stateFile)) { Write-Host 'No recorded server.'; exit 0 }
$state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
$rootId = [int]$state.pid
$port = [int]$state.port
if ($ExpectedPid -and $rootId -ne $ExpectedPid) {
    throw 'The recorded FlowHMR server changed. No process was stopped.'
}
if (-not (Get-Process -Id $rootId -ErrorAction SilentlyContinue)) {
    Write-Host 'The recorded server has already stopped.'
    exit 0
}
$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $rootId"
if ($null -eq $processInfo) { Write-Host 'The recorded server has already stopped.'; exit 0 }
. (Join-Path $PSScriptRoot 'desktop-runtime.ps1')
$expectedExe = Get-FlowHMRConsoleRuntime $projectRoot
if ($state.root -ne $projectRoot -or $processInfo.ExecutablePath -ne $expectedExe -or
    $processInfo.CommandLine -notmatch "\bapp\.py\b.*--host\s+127\.0\.0\.1\s+--port\s+$port\b") {
    throw 'The recorded process does not match this FlowHMR server. No process was stopped.'
}
function Stop-ServerProcessTree($parent) {
    $parentId = [int]$parent.ProcessId
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $parentId"
    foreach ($child in $children) {
        if ($child.CreationDate -ge $parent.CreationDate) { Stop-ServerProcessTree $child }
    }
    $current = Get-CimInstance Win32_Process -Filter "ProcessId = $parentId"
    if ($current -and $current.CreationDate -eq $parent.CreationDate) {
        try { Stop-Process -Id $parentId }
        catch { if (Get-Process -Id $parentId -ErrorAction SilentlyContinue) { throw } }
    }
}
Stop-ServerProcessTree $processInfo
Write-Host 'FlowHMR stopped.'
