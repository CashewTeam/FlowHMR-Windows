param([string]$Destination = '')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if (-not $Destination) { $Destination = Join-Path $root 'FlowHMR.exe' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw 'The Windows .NET Framework C# compiler is missing.' }
$source = Join-Path $PSScriptRoot 'FlowHMRLauncher.cs'
$updater = Join-Path $PSScriptRoot 'DesktopUpdate.cs'
$icon = Join-Path $root 'assets\flowhmr.ico'
& $compiler /nologo /codepage:65001 /target:winexe /platform:x64 /optimize+ `
    /reference:System.Windows.Forms.dll "/win32icon:$icon" "/out:$Destination" $source $updater
if ($LASTEXITCODE -ne 0) { throw 'Could not build FlowHMR.exe.' }
Write-Host "Built $Destination"
