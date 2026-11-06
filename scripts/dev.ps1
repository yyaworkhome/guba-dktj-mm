$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { python -m venv .venv }
& '.venv/Scripts/python.exe' -m pip install -r backend/requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
if (-not (Test-Path -LiteralPath 'node_modules')) { npm ci }
if ($LASTEXITCODE -ne 0) { throw 'Node dependency installation failed' }
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
$taskApi = Start-Process -FilePath $taskPython -ArgumentList '-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru
try { npm run dev } finally { if (-not $taskApi.HasExited) { Stop-Process -Id $taskApi.Id } }
