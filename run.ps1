# Start (or restart) the RINDA console on http://localhost:8740
param([int]$Port = 8740)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$old = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).OwningProcess
if ($old) { Stop-Process -Id $old -Force; Start-Sleep 1 }
New-Item -ItemType Directory -Force (Join-Path $here "var") | Out-Null
Start-Process -FilePath python -WorkingDirectory $here `
  -ArgumentList "-m", "uvicorn", "--app-dir", "server", "main:app", "--host", "127.0.0.1", "--port", "$Port" `
  -RedirectStandardOutput (Join-Path $here "var\server.log") -RedirectStandardError (Join-Path $here "var\server.err") -WindowStyle Hidden
Write-Host "RINDA starting on http://localhost:$Port  (first start fits the models: about one minute)"
