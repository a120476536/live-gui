# kill_8765.ps1
# 杀掉本机 8765 端口上所有的进程（默认只杀 LISTENING 的服务端；加 -All 连客户端也杀）
#
# 用法（PowerShell 或 Git Bash 都行）：
#   powershell -ExecutionPolicy Bypass -File kill_8765.ps1                # 默认 8765，只杀服务端
#   powershell -ExecutionPolicy Bypass -File kill_8765.ps1 -All           # 8765 端口所有相关进程（含 chrome 等客户端）
#   powershell -ExecutionPolicy Bypass -File kill_8765.ps1 -Port 8080     # 指定其他端口
#   powershell -ExecutionPolicy Bypass -File kill_8765.ps1 -Port 8080 -All -Yes   # 跳过确认
#
# Git Bash 里调用：
#   powershell -ExecutionPolicy Bypass -File ./kill_8765.ps1
#   powershell -ExecutionPolicy Bypass -File ./kill_8765.ps1 -All -Yes

[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$All = $false,
    [switch]$Yes = $false   # 跳过确认提示
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== 8765 端口清理脚本 (port=$Port, all=$All) ===" -ForegroundColor Cyan
Write-Host ""

# 1) 查所有相关连接（LISTENING / ESTABLISHED / SYN_SENT / CLOSE_WAIT 等）
$conns = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Host "[OK] 端口 $Port 上没有任何活跃连接" -ForegroundColor Green
    exit 0
}

# 2) 按角色分类展示
$listeners = $conns | Where-Object { $_.State -eq "Listen" }
$clients   = $conns | Where-Object { $_.State -ne "Listen" }

Write-Host "[LISTENING] 服务端进程：" -ForegroundColor Cyan
if ($listeners) {
    foreach ($c in $listeners) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        $name = if ($p) { $p.ProcessName } else { "<已退出>" }
        $path = if ($p) { $p.Path }       else { "<n/a>" }
        Write-Host ("  PID={0,-6}  进程={1,-20}  路径={2}" -f $c.OwningProcess, $name, $path) -ForegroundColor Green
    }
} else {
    Write-Host "  (无)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "[CONNECTED] 客户端连接（" $clients.Count "条）：" -ForegroundColor Cyan
if ($clients) {
    foreach ($c in $clients) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        $name = if ($p) { $p.ProcessName } else { "<已退出>" }
        Write-Host ("  PID={0,-6}  进程={1,-20}  远端={2}:{3,-5}  状态={4}" -f $c.OwningProcess, $name, $c.RemoteAddress, $c.RemotePort, $c.State) -ForegroundColor Yellow
    }
}

# 3) 决定要杀的进程集合
$toKill = @()
$toKill += $listeners | ForEach-Object { $_.OwningProcess }
if ($All) {
    $toKill += $clients | ForEach-Object { $_.OwningProcess }
}
$toKill = $toKill | Sort-Object -Unique | Where-Object { $_ -gt 0 }

if ($toKill.Count -eq 0) {
    Write-Host ""
    Write-Host "[SKIP] 没有需要杀的进程（可能只剩 TIME_WAIT 残留）" -ForegroundColor Yellow
    exit 0
}

# 4) 确认
Write-Host ""
Write-Host "=== 准备杀掉 PID: $($toKill -join ', ') ===" -ForegroundColor Magenta
if (-not $Yes) {
    $ans = Read-Host "确认？(y/N)"
    if ($ans -ne 'y' -and $ans -ne 'Y') {
        Write-Host "已取消，未杀任何进程" -ForegroundColor Yellow
        exit 0
    }
}

# 5) 执行
Write-Host ""
Write-Host "=== 开始杀进程 ===" -ForegroundColor Magenta
foreach ($pid_ in $toKill) {
    try {
        $p = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
        $name = if ($p) { $p.ProcessName } else { "<已退出>" }
        Stop-Process -Id $pid_ -Force -ErrorAction Stop
        Write-Host "  [OK] 杀掉 PID=$pid_ ($name)" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] PID=$pid_ 杀失败: $_" -ForegroundColor Red
    }
}

# 6) 验证
Start-Sleep -Seconds 1
Write-Host ""
Write-Host "=== 杀完后端口状态 ===" -ForegroundColor Cyan
$conns2 = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
$left = if ($conns2) { $conns2.Count } else { 0 }
if ($left -eq 0) {
    Write-Host "[OK] 端口 $Port 已彻底清空" -ForegroundColor Green
} else {
    Write-Host "[WARN] 端口 $Port 还有 $left 个连接残留（多半是 TIME_WAIT，会在几十秒内自动消失）：" -ForegroundColor Yellow
    $conns2 | Select-Object State, OwningProcess, LocalAddress, LocalPort, RemoteAddress, RemotePort |
              Format-Table -AutoSize | Out-String | Write-Host
}

Write-Host ""
