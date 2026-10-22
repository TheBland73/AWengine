# Run the offline demo suite and print the generated reports.
#
#   powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
#
# It runs all six offline demos, prints the result summary, and opens the
# most recent fix-loop report.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    python -m awengine demo
    Write-Host ""
    Write-Host "Reports (newest first):" -ForegroundColor Cyan
    $runs = Get-ChildItem -Path "runs" -Directory |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 6
    foreach ($run in $runs) {
        $report = Join-Path $run.FullName "report.md"
        if (Test-Path $report) {
            Write-Host ("  {0}  {1}" -f $run.Name, $report)
        }
    }
    $bugfix = $runs | Where-Object { $_.Name -like "*-bugfix" } | Select-Object -First 1
    if ($bugfix) {
        $report = Join-Path $bugfix.FullName "report.md"
        Write-Host ""
        Write-Host "Bugfix report: $report" -ForegroundColor Green
        if (Get-Command code -ErrorAction SilentlyContinue) {
            code $report
        } else {
            Start-Process $report
        }
    }
} finally {
    Pop-Location
}
