Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"
$out = "docs\benchmark-results.md"
Remove-Item $out -ErrorAction SilentlyContinue
& $py -u -m scripts.benchmark https://www.python.org https://fastapi.tiangolo.com --runs 1 --models nvidia:nvidia/nemotron-3.5-lightning-30b-a3b nvidia:minimaxai/minimax-m3.1-flash nvidia:z-ai/glm-5.3-flash nvidia:google/gemma-4-31b-it nvidia:nvidia/nemotron-3-ultra-550b-a55b 2>&1 | Out-File -FilePath $out -Encoding utf8
"exit code: $LASTEXITCODE" | Add-Content $out
git add -A 2>$null
git commit -q -m "Add benchmark results" 2>$null
