param(
    [ValidateSet('demo', 'design', 'test', 'app', 'experiments')]
    [string]$Mode = 'demo',
    [string]$Scenario = 'clean',
    [switch]$NoVideo
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$MasonPython = Join-Path $PSScriptRoot '.venv311\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $MasonPython)) {
    $MasonPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $MasonPython)) {
    throw 'Create the virtual environment and install dependencies as described in README.md.'
}
switch ($Mode) {
    'test' { & $MasonPython -m pytest -q }
    'design' { & $MasonPython -m masonmind --milestone 5 }
    'app' {
        if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.local-llm\ready.json')) {
            & (Join-Path $PSScriptRoot 'scripts\start_local_llm.ps1')
        }
        $env:MPLCONFIGDIR = Join-Path $PSScriptRoot 'runs\web\matplotlib'
        & $MasonPython scripts/app.py
    }
    'experiments' { & $MasonPython experiments/run_all.py --quick }
    'demo' {
        $MasonArguments = @('-m', 'masonmind', '--scenario', $Scenario)
        if ($NoVideo) { $MasonArguments += '--no-video' }
        & $MasonPython @MasonArguments
    }
}
exit $LASTEXITCODE
