$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) {
    throw "Set DATABASE_URL before creating a PostgreSQL backup."
}
if ([string]::IsNullOrWhiteSpace($env:BACKUP_DIR)) {
    throw "Set BACKUP_DIR to a protected backup directory."
}
if (-not (Get-Command pg_dump -ErrorAction SilentlyContinue)) {
    throw "pg_dump was not found. Install PostgreSQL client tools first."
}
if (-not (Get-Command pg_restore -ErrorAction SilentlyContinue)) {
    throw "pg_restore was not found. Install PostgreSQL client tools first."
}

$backupDirectory = [System.IO.Path]::GetFullPath($env:BACKUP_DIR)
New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
$timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
$backupPath = Join-Path $backupDirectory "hardwareai-$timestamp.dump"
$backupClientUrl = [regex]::Replace(
    $env:DATABASE_URL,
    '^(postgresql|postgres)\+psycopg://',
    '$1://',
    [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
)

& pg_dump --format=custom --no-owner --file $backupPath $backupClientUrl
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $backupPath -ErrorAction SilentlyContinue
    throw "pg_dump failed with exit code $LASTEXITCODE."
}

& pg_restore --list $backupPath | Out-Null
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $backupPath -ErrorAction SilentlyContinue
    throw "Backup archive validation failed."
}

$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $backupPath
"$($hash.Hash)  $([System.IO.Path]::GetFileName($backupPath))" |
    Set-Content -Encoding ascii -LiteralPath "$backupPath.sha256"
Write-Output "Backup created: $backupPath"
Write-Output "SHA-256 manifest: $backupPath.sha256"
