$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
Start-Transcript -Path "$PSScriptRoot\docs\run-log2.txt" -Force | Out-Null
$py = ".\.venv\Scripts\python.exe"
$port = 4010

# list models available to this key
$key = (Get-Content .env | Where-Object { $_ -like "NVIDIA_API_KEY=*" }) -replace "NVIDIA_API_KEY=", ""
try { (Invoke-RestMethod -Uri https://integrate.api.nvidia.com/v1/models -Headers @{Authorization="Bearer $key"}).data.id | Sort-Object | Set-Content docs\nvidia-models.txt } catch { "models list failed: $($_.Exception.Message)" | Set-Content docs\nvidia-models.txt }

$server = Start-Process -FilePath $py -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","$port" -PassThru -WindowStyle Hidden -RedirectStandardError docs\server2.log
Start-Sleep -Seconds 5
try {
    foreach ($site in @("https://fastapi.tiangolo.com", "https://www.djangoproject.com")) {
        $name = ([uri]$site).Host -replace '[^a-z0-9]', '_'
        Write-Host "Analyzing $site ..."
        try {
            $r = Invoke-RestMethod -Method Post -Uri "http://localhost:$port/link-analysis" -ContentType "application/json" -Body (@{url=$site} | ConvertTo-Json) -TimeoutSec 400
            $r | ConvertTo-Json -Depth 20 | Set-Content "docs\live-$name.json" -Encoding utf8
            Write-Host ("  OK  model {0} tokens {1}/{2}  llm {3} ms  total {4} ms" -f $r.meta.model, $r.meta.input_tokens, $r.meta.output_tokens, $r.meta.llm_latency_ms, $r.meta.total_ms)
        } catch { Write-Host "  FAILED: $($_.Exception.Message)"; if ($_.ErrorDetails) { Write-Host $_.ErrorDetails.Message } }
    }
    & $py -m scripts.benchmark https://www.python.org --runs 1 --models nvidia:nvidia/nemotron-3-ultra-550b-a55b nvidia:meta/llama-3.1-8b-instruct nvidia:openai/gpt-oss-20b nvidia:mistralai/mistral-small-3.1-24b-instruct-2503 nvidia:nvidia/llama-3.3-nemotron-super-49b-v1.5 | Tee-Object docs\benchmark-results.md
} finally { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
git add -A 2>$null
git commit -q -m "Add live outputs and benchmark results" 2>$null
Stop-Transcript | Out-Null
