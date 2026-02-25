# Start the web app on port 8000 (single port — no switching).
# Run this from your own terminal so the process is under your control.
Set-Location $PSScriptRoot
& python -m uvicorn app.main:app --reload --port 8000
