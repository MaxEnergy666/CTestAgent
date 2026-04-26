param(
    [string]$PythonExe = "",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"

$WorkspaceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $WorkspaceRoot "CTestAgent"
$FrontendDir = Join-Path $WorkspaceRoot "ctestagent-ui"

function Resolve-PythonExe {
    param(
        [string]$UserPython,
        [string]$WorkspaceRootPath
    )

    $candidates = @(
        $UserPython,
        $env:CTESTAGENT_PYTHON,
        "D:\Anaconda3\envs\rl_learning\python.exe",
        "D:\Anaconda3\python.exe"
    )

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd -and $pythonCmd.Source) {
        return $pythonCmd.Source
    }

    throw "未找到可用 Python，请设置 CTESTAGENT_PYTHON 或传入 -PythonExe 参数。"
}

if (-not (Test-Path $BackendDir)) {
    throw "后端目录不存在: $BackendDir"
}

if (-not (Test-Path $FrontendDir)) {
    throw "前端目录不存在: $FrontendDir"
}

$ResolvedPython = Resolve-PythonExe -UserPython $PythonExe -WorkspaceRootPath $WorkspaceRoot

$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) {
    throw "未找到 npm，请先安装 Node.js 并确保 npm 在 PATH 中。"
}

$nodeModulesDir = Join-Path $FrontendDir "node_modules"
if (-not (Test-Path $nodeModulesDir)) {
    Write-Host "[Init] 前端依赖未安装，正在执行 npm install..." -ForegroundColor Yellow
    Push-Location $FrontendDir
    try {
        & $npmCmd.Source install
    }
    finally {
        Pop-Location
    }
}

$backendCommand = "Set-Location '$BackendDir'; & '$ResolvedPython' -m uvicorn server:app --host 0.0.0.0 --port $BackendPort --reload"
$frontendCommand = "Set-Location '$FrontendDir'; npm run dev -- --host 0.0.0.0 --port $FrontendPort"

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-Command", $backendCommand
)

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy", "Bypass",
    "-Command", $frontendCommand
)

Start-Process "http://localhost:$FrontendPort"

Write-Host "[OK] 已启动后端与前端。" -ForegroundColor Green
Write-Host "后端: http://localhost:$BackendPort/api/status"
Write-Host "前端: http://localhost:$FrontendPort"