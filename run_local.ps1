# One-click local run: setup -> tests -> start server -> analyze real sites -> save outputs -> git commit.
# Usage (PowerShell, in this folder):  powershell -ExecutionPolicy Bypass -File .\run_local.ps1
$ErrorActionPreference = "Stop"
Start-Transcript -Path "$PSScriptRoot\docs\run-log.txt" -Force | Out-Null
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt

if (-not (Test-Path .env)) {
    $sec = Read-Host "Paste your NVIDIA API key (nvapi-...)" -AsSecureString
    $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    @"
LLM_PROVIDER=nvidia
LLM_MODEL=nvidia/nemotron-3-ultra-550b-a55b
LLM_FALLBACK_PROVIDER=
LLM_FALLBACK_MODEL=
NVIDIA_API_KEY=$key
"@ | Set-Content .env -Encoding ascii
}

& .\.venv\Scripts\python.exe -m pytest -q
New-Item -ItemType Directory -Force docs | Out-Null

Get-NetTCPConnection -LocalPort 4000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Remove-Item docs\live-*,docs\benchmark-results.md -ErrorAction SilentlyContinue
$server = Start-Process -FilePath .\.venv\Scripts\python.exe -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","4000" -PassThru -WindowStyle Hidden -RedirectStandardError docs\server.log
Start-Sleep -Seconds 5
try {
    foreach ($site in @("https://www.python.org", "https://fastapi.tiangolo.com", "https://www.wikipedia.org")) {
        $name = ([uri]$site).Host -replace '[^a-z0-9]', '_'
        Write-Host "Analyzing $site ..."
        try {
            $r = Invoke-RestMethod -Method Post -Uri http://localhost:4000/link-analysis -ContentType "application/json" -Body (@{url=$site} | ConvertTo-Json) -TimeoutSec 400
            $r | ConvertTo-Json -Depth 20 | Set-Content "docs\live-$name.json" -Encoding utf8
            Write-Host ("  OK  tokens {0}/{1}  llm {2} ms  total {3} ms" -f $r.meta.input_tokens, $r.meta.output_tokens, $r.meta.llm_latency_ms, $r.meta.total_ms)
        } catch { Write-Host "  FAILED: $($_.Exception.Message)"; if ($_.ErrorDetails) { Write-Host $_.ErrorDetails.Message } }
    }
    # error-case evidence
    foreach ($bad in @("http://169.254.169.254/latest/meta-data", "ftp://example.com")) {
        try { Invoke-RestMethod -Method Post -Uri http://localhost:4000/link-analysis -ContentType "application/json" -Body (@{url=$bad} | ConvertTo-Json) | Out-Null }
        catch { "$bad -> $($_.ErrorDetails.Message)" | Add-Content docs\live-errors.txt }
    }
    # measured benchmark for the chosen model
    & .\.venv\Scripts\python.exe -m scripts.benchmark https://www.python.org https://fastapi.tiangolo.com --models nvidia:meta/llama-3.3-70b-instruct --runs 1 | Tee-Object docs\benchmark-results.md
} finally { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }

if (-not (Test-Path .git)) { git init -q; git branch -M main }
git add -A
git commit -q -m "AI-powered website link analysis API" 2>$null
Stop-Transcript | Out-Null
Write-Host "`nDone. Outputs are in docs\ . Next: create a PUBLIC GitHub repo and push (see SUBMIT.md)."
