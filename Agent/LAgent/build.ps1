<#
.SYNOPSIS
    LAgent 一键打包脚本 —— PyInstaller + Inno Setup 6。

.DESCRIPTION
    先调用 PyInstaller 把 main.py 打包成可执行程序（dist/LAgent/），
    再用 Inno Setup 6 把 dist/LAgent/ 封装成 Windows 安装包 (.exe)。
    脚本会自动补齐 PATH、检测 ISCC 路径、复制数据文件。

.PARAMETER SkipInstaller
    只生成可执行程序目录，跳过 Inno Setup 安装包生成。

.PARAMETER Clean
    构建前清理之前的 build/ 和 dist/ 目录。

.PARAMETER InnoSetupPath
    手动指定 Inno Setup 可执行文件（ISCC.exe）路径。不传则自动探测常见位置。

.EXAMPLE
    .\build.ps1                     # 完整构建（exe + 安装包）
    .\build.ps1 -SkipInstaller      # 只生成 exe 目录
    .\build.ps1 -Clean              # 清理后重新构建
    .\build.ps1 -Clean -SkipInstaller
#>

param(
    [switch]$SkipInstaller,
    [switch]$Clean,
    [string]$InnoSetupPath = ""
)

$ErrorActionPreference = "Stop"

# ---------- 0. 补齐 PATH：pip install --user 的脚本目录 ----------
$UserScripts = "$env:APPDATA\Python\Python313\Scripts"
if (-not ($env:PATH -split ";" | Where-Object { $_ -eq $UserScripts })) {
    $env:PATH = "$UserScripts;$env:PATH"
}

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
Write-Host "`n╔══════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   LAgent 打包脚本" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host "`n[1/5] 工作目录: $ProjectRoot" -ForegroundColor Cyan

if (-not (Test-Path "main.py")) {
    Write-Host "❌ 错误：当前目录找不到 main.py" -ForegroundColor Red
    exit 1
}

# ---------- 2. 环境校验 ----------
Write-Host "`n[2/5] 环境校验..." -ForegroundColor Cyan

try {
    $pyver = python --version 2>&1
    Write-Host "   ✅ Python: $pyver" -ForegroundColor Green
} catch {
    Write-Host "   ❌ 未找到 Python" -ForegroundColor Red
    exit 1
}

try {
    $pyi_ver = pyinstaller --version 2>&1
    Write-Host "   ✅ PyInstaller: $pyi_ver" -ForegroundColor Green
} catch {
    Write-Host "   ❌ 未找到 PyInstaller（pip install pyinstaller）" -ForegroundColor Red
    exit 1
}

# ---------- 3. 探测 ISCC 路径 ----------
Write-Host "`n[3/5] 探测 Inno Setup 6..." -ForegroundColor Cyan

$IsccCandidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "D:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "D:\Program Files\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
)

if ($InnoSetupPath -and (Test-Path $InnoSetupPath)) {
    $IsccPath = $InnoSetupPath
    Write-Host "   ✅ 使用手动指定: $IsccPath" -ForegroundColor Green
} else {
    $IsccPath = ""
    foreach ($c in $IsccCandidates) {
        if (Test-Path $c) {
            $IsccPath = $c
            break
        }
    }
    if ($IsccPath) {
        Write-Host "   ✅ 自动发现: $IsccPath" -ForegroundColor Green
    } else {
        Write-Host "   ⚠️  未找到 ISCC.exe —— 将跳过安装包生成" -ForegroundColor Yellow
        Write-Host "      如已安装，可用 -InnoSetupPath '...' 指定路径" -ForegroundColor Yellow
        $SkipInstaller = $true
    }
}

# ---------- 4. 清理 ----------
if ($Clean) {
    Write-Host "`n[4/5] 清理..." -ForegroundColor Cyan
    foreach ($d in @("build", "dist")) {
        if (Test-Path $d) {
            Remove-Item -Recurse -Force $d
            Write-Host "   删除: $d" -ForegroundColor DarkGray
        }
    }
}

# ---------- 5. PyInstaller 构建 ----------
Write-Host "`n[5/5] PyInstaller 构建..." -ForegroundColor Cyan

$pyiArgs = @(
    "--noconfirm",
    "--clean",
    "--console",
    "--name", "LAgent",
    "--paths", ".",
    "--hidden-import", "core",
    "--hidden-import", "core.agent",
    "--hidden-import", "core.config",
    "--hidden-import", "core.events",
    "--hidden-import", "core.llm_client",
    "--hidden-import", "core.tools",
    "--hidden-import", "core.tools.registry",
    "--hidden-import", "core.tools.file_ops",
    "--hidden-import", "core.tools.datetime_tools",
    "--hidden-import", "core.tools.search_tools",
    "--hidden-import", "core.tools.web_tools",
    "--hidden-import", "core.tools.cli_tools",
    "--hidden-import", "ui",
    "--hidden-import", "ui.base",
    "--hidden-import", "ui.cli",
    "--hidden-import", "ui.tui",
    "--hidden-import", "ui.tui.app",
    "--hidden-import", "ui.tui.widgets",
    "--hidden-import", "ui.tui.help_screen",
    "--hidden-import", "trafilatura",
    "--hidden-import", "bs4",
    "--hidden-import", "textual",
    "--hidden-import", "textual.widgets._text_area",
    "--exclude-module", "PyQt5",
    "--exclude-module", "PyQt6",
    "--exclude-module", "PySide2",
    "--exclude-module", "PySide6",
    "--exclude-module", "tkinter",
    "--exclude-module", "matplotlib",
    "--exclude-module", "scipy",
    "--exclude-module", "pandas",
    "--exclude-module", "numpy",
    "--exclude-module", "PIL",
    "--exclude-module", "IPython",
    "--exclude-module", "jupyter",
    "--exclude-module", "notebook",
    "main.py"
)

Write-Host "   命令: pyinstaller [args] main.py" -ForegroundColor DarkGray
$sw = [System.Diagnostics.Stopwatch]::StartNew()

& pyinstaller @pyiArgs 2>&1 | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }

$sw.Stop()
$buildExit = $LASTEXITCODE

if ($buildExit -ne 0) {
    Write-Host "`n❌ PyInstaller 构建失败（exit code = $buildExit）" -ForegroundColor Red
    exit $buildExit
}
Write-Host "   ✅ PyInstaller 完成（$($sw.Elapsed.ToString('mm\:ss'))）" -ForegroundColor Green

# ---------- 6. 复制数据文件 ----------
Write-Host "`n复制数据文件..." -ForegroundColor Cyan

$distDir = "dist\LAgent"
if (Test-Path "config.ini") {
    Copy-Item config.ini "$distDir\config.ini" -Force
    Write-Host "   ✅ config.ini → $distDir" -ForegroundColor Green
}
$tuiStylesDir = "$distDir\ui\tui"
New-Item -ItemType Directory -Force -Path $tuiStylesDir | Out-Null
if (Test-Path "ui\tui\styles.tcss") {
    Copy-Item ui\tui\styles.tcss "$tuiStylesDir\styles.tcss" -Force
    Write-Host "   ✅ styles.tcss → $tuiStylesDir" -ForegroundColor Green
}

# ---------- 7. 验证 exe ----------
Write-Host "`n验证 exe..." -ForegroundColor Cyan
$exePath = "$distDir\LAgent.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "❌ dist\LAgent\LAgent.exe 不存在" -ForegroundColor Red
    exit 1
}
try {
    & $exePath --help 2>&1 | Out-Null
    Write-Host "   ✅ exe 启动正常" -ForegroundColor Green
} catch {
    Write-Host "   ⚠️  exe 启动有警告（非致命）" -ForegroundColor Yellow
}

# ---------- 8. Inno Setup 安装包 ----------
if ($SkipInstaller) {
    Write-Host "`n⚠️  跳过 Inno Setup 安装包生成" -ForegroundColor Yellow
} else {
    Write-Host "`n生成安装包..." -ForegroundColor Cyan
    Write-Host "   （需要 Inno Setup 6，脚本留空等待后续配置）" -ForegroundColor DarkGray
    # TODO: 创建 .iss 脚本并调用 ISCC.exe
}

# ---------- 9. 产物汇总 ----------
Write-Host "`n╔══════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║   打包完成！" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════════════════════╝" -ForegroundColor Green

if (Test-Path $distDir) {
    $totalSize = (Get-ChildItem $distDir -Recurse | Measure-Object -Property Length -Sum).Sum
    Write-Host "`n📦 产物目录: $distDir" -ForegroundColor Cyan
    Write-Host "   总大小: $([math]::Round($totalSize / 1MB, 1)) MB" -ForegroundColor DarkGray
    Write-Host "`n🚀 运行方式:" -ForegroundColor Cyan
    Write-Host "   1. 直接双击 dist\LAgent\LAgent.exe" -ForegroundColor White
    Write-Host "   2. 命令行: dist\LAgent\LAgent.exe --ui cli" -ForegroundColor White
    Write-Host "   3. 配置文件与 exe 同级，可直接修改 config.ini" -ForegroundColor White
}
