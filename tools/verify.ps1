# PowerShell verification script
param (
    [string]$Url = "http://127.0.0.1:8080"
)

Write-Host "Running GridWise Verification Gate against $Url ..." -ForegroundColor Cyan
python tools/harness.py --url $Url
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python tools/harness.py --url $Url --paraphrase
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python tools/harness.py --url $Url --adversarial
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python tools/harness.py --url $Url --soak
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "All gates passed successfully!" -ForegroundColor Green
