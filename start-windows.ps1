$rootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "===================================="
Write-Host " Starting LLMOps Platform"
Write-Host "===================================="
Write-Host ""

$backendDir = Join-Path $rootDir "backend"
$frontendDir = Join-Path $rootDir "frontend"
$pythonExe = Join-Path $backendDir ".venv\Scripts\python.exe"

# Step 1: Backend venv
Write-Host "[1/4] Setting up backend..."
if (-not (Test-Path $pythonExe)) {
    Write-Host "[INFO] Creating virtual environment..."
    Push-Location $backendDir
    py -3 -m venv .venv
    if (-not (Test-Path $pythonExe)) {
        python -m venv .venv
    }
    Pop-Location
}
Push-Location $backendDir
& $pythonExe -m pip install -e . -q
Pop-Location
Write-Host "[OK] Backend ready"
Write-Host ""

# Step 2: Frontend deps
Write-Host "[2/4] Setting up frontend..."
if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
    Push-Location $frontendDir
    npm install
    Pop-Location
}
Write-Host "[OK] Frontend ready"
Write-Host ""

# Step 3: Start backend
Write-Host "[3/4] Starting backend..."
Start-Process -WindowStyle Normal -WorkingDirectory $backendDir -FilePath $pythonExe -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
Write-Host "[OK] Backend starting"
Write-Host ""

# Step 4: Start frontend (cmd /c avoids npm.ps1 shim issue)
Write-Host "[4/4] Starting frontend..."
Start-Process -WindowStyle Normal -WorkingDirectory $frontendDir -FilePath "cmd.exe" -ArgumentList "/c npm run dev"
Write-Host "[OK] Frontend starting"
Write-Host ""

Write-Host "===================================="
Write-Host " Done"
Write-Host " Frontend: http://127.0.0.1:5173"
Write-Host " API:      http://127.0.0.1:8000/docs"
Write-Host "===================================="
