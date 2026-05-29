# Installation OCR avec timeout étendu (connexion lente / gros paquets)
# Usage : cd backend ; .\install-ml.ps1

$ErrorActionPreference = "Stop"
$pip = Join-Path $PSScriptRoot "venv\Scripts\pip.exe"

if (-not (Test-Path $pip)) {
    Write-Error "venv introuvable. Lancez d'abord : python -m venv venv"
}

$timeout = 1000  # secondes (défaut pip = 15s par chunk → timeouts fréquents)

Write-Host "Mise a jour de pip..." -ForegroundColor Cyan
& $pip install --upgrade pip --default-timeout=$timeout

Write-Host "Etape 1/3 : unidic_lite (~47 Mo) — le plus long..." -ForegroundColor Cyan
& $pip install --default-timeout=$timeout --retries 5 unidic_lite

Write-Host "Etape 2/3 : easyocr..." -ForegroundColor Cyan
& $pip install --default-timeout=$timeout --retries 5 easyocr

Write-Host "Etape 3/4 : manga-ocr (sans re-telecharger unidic_lite)..." -ForegroundColor Cyan
& $pip install --default-timeout=$timeout --retries 5 manga-ocr

Write-Host "OK — OCR installe. Dictionnaires RAG : POST http://127.0.0.1:8000/api/glossary/preload" -ForegroundColor Green
& $pip show manga-ocr easyocr unidic_lite
