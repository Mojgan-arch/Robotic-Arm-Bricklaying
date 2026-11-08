$ErrorActionPreference = 'Stop'
$MasonRoot = Split-Path -Parent $PSScriptRoot
$MasonOllama = Join-Path $MasonRoot '.local-llm\runtime\ollama.exe'
if (-not (Test-Path -LiteralPath $MasonOllama)) { throw 'Local Ollama runtime is not installed.' }
try {
    Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null
    return
} catch { }
$env:OLLAMA_MODELS = Join-Path $MasonRoot '.local-llm\models'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_NUM_PARALLEL = '1'
Start-Process -FilePath $MasonOllama -ArgumentList 'serve' -WindowStyle Hidden -WorkingDirectory $MasonRoot -RedirectStandardOutput (Join-Path $MasonRoot '.local-llm\server.log') -RedirectStandardError (Join-Path $MasonRoot '.local-llm\server-error.log') | Out-Null
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    try { Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null; return } catch { }
}
throw 'Ollama did not become ready. Inspect .local-llm/server-error.log.'
