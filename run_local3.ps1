Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"
$env:LLM_MAX_RETRIES = "0"
$env:LLM_TIMEOUT_SECONDS = "100"
$out = "docs\benchmark-results.md"
Remove-Item $out -ErrorAction SilentlyContinue
Remove-Item benchmark_outputs\* -ErrorAction SilentlyContinue
& $py -u -m scripts.benchmark https://www.python.org https://fastapi.tiangolo.com --runs 1 --models nvidia:nvidia/nemotron-3.5-lightning-30b-a3b nvidia:z-ai/glm-5.3-flash nvidia:google/gemma-4-31b-it 2>&1 | Out-File -FilePath $out -Encoding utf8
git add -A 2>$null
git commit -q -m "Add benchmark results" 2>$null
