<#
.SYNOPSIS  Common project commands. Usage: .\tasks.ps1 <task>
.EXAMPLE   .\tasks.ps1 setup
#>
param([Parameter(Position = 0)][string]$Task = "help", [Parameter(ValueFromRemainingArguments = $true)]$Rest)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

switch ($Task) {
    "setup"  { py -3.11 -m venv .venv; & $py -m pip install --upgrade pip; & $py -m pip install -e ".[dev]" }
    "test"   { & $py -m pytest --cov @Rest }
    "lint"   { & $py -m ruff check . ; & $py -m ruff format --check . }
    "fmt"    { & $py -m ruff check --fix . ; & $py -m ruff format . }
    "api"    { & $py -m uvicorn f1tel.api:app --reload --port 8000 }
    "up"     { docker compose up --build }
    "down"   { docker compose down }
    "measure" { & $py scripts/measure.py }
    default  { "Tasks: setup | test | lint | fmt | api | up | down | measure" }
}
