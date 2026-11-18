# Starts the API locally and exposes it through a free Cloudflare quick tunnel (no account, no card).
# The public URL works only while this window stays open and the PC is awake.
Set-Location $PSScriptRoot
$ErrorActionPreference = "Continue"
$port = 4020
$py = ".\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force tools, docs | Out-Null

# 1) cloudflared (official release from github.com/cloudflare/cloudflared), downloaded once
if (-not (Test-Path tools\cloudflared.exe)) {
    Write-Host "Downloading cloudflared.exe from the official Cloudflare GitHub release ..."
    Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile tools\cloudflared.exe
}

# 2) API server
Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
$api = Start-Process -FilePath $py -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","$port" -PassThru -WindowStyle Hidden -RedirectStandardError docs\demo-server.log
Start-Sleep -Seconds 5
try { Write-Host ("Local health check: " + (Invoke-RestMethod "http://127.0.0.1:$port/health").status) } catch { Write-Host "Local server did not start: $($_.Exception.Message)" }

# 3) Tunnel
Remove-Item docs\demo-tunnel.log, docs\demo-url.txt -ErrorAction SilentlyContinue
$tun = Start-Process -FilePath .\tools\cloudflared.exe -ArgumentList "tunnel","--url","http://127.0.0.1:$port","--no-autoupdate" -PassThru -WindowStyle Hidden -RedirectStandardError docs\demo-tunnel.log
$url = $null
for ($i = 0; $i -lt 40 -and -not $url; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path docs\demo-tunnel.log) {
        $m = Select-String -Path docs\demo-tunnel.log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value }
    }
}
if ($url) {
    $url | Set-Content docs\demo-url.txt -Encoding ascii
    Write-Host ""
    Write-Host "PUBLIC DEMO URL:  $url"
    Write-Host "API docs:         $url/docs"
    Write-Host "Endpoint:         $url/link-analysis"
    Write-Host ""
    Write-Host "Keep this window OPEN while the demo should be reachable. Close it to stop."
} else { Write-Host "Tunnel URL not found; see docs\demo-tunnel.log" }
while ($true) { Start-Sleep -Seconds 60; if ($api.HasExited -or $tun.HasExited) { Write-Host "A process exited; closing."; break } }
