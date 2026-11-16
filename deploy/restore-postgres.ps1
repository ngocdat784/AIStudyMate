$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($env:RESTORE_DATABASE_URL)) {
    throw "Set RESTORE_DATABASE_URL to a newly created, isolated, empty database."
}
if ([string]::IsNullOrWhiteSpace($env:BACKUP_FILE)) {
    throw "Set BACKUP_FILE to the custom-format PostgreSQL dump to restore."
}
if ($env:CONFIRM_RESTORE -cne "RESTORE_TO_EMPTY_DATABASE") {
    throw "Set CONFIRM_RESTORE=RESTORE_TO_EMPTY_DATABASE after verifying the target."
}
if (-not (Test-Path -LiteralPath $env:BACKUP_FILE -PathType Leaf)) {
    throw "Backup file does not exist: $env:BACKUP_FILE"
}
if (-not (Get-Command pg_restore -ErrorAction SilentlyContinue)) {
    throw "pg_restore was not found. Install PostgreSQL client tools first."
}
if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
    throw "psql was not found. Install PostgreSQL client tools first."
}
if (
    -not [string]::IsNullOrWhiteSpace($env:DATABASE_URL) -and
    $env:RESTORE_DATABASE_URL -ceq $env:DATABASE_URL
) {
    throw "Refusing to restore into the configured application database."
}
if (-not [string]::IsNullOrWhiteSpace($env:DATABASE_URL)) {
    $applicationMatch = [regex]::Match(
        $env:DATABASE_URL,
        '^.+?://(?<authority>[^/]+)/(?<database>[^?]+)'
    )
    $restoreMatch = [regex]::Match(
        $env:RESTORE_DATABASE_URL,
        '^.+?://(?<authority>[^/]+)/(?<database>[^?]+)'
    )
    if (-not $applicationMatch.Success -or -not $restoreMatch.Success) {
        throw "Could not safely parse database URLs; refusing to restore."
    }

    $applicationHost = $applicationMatch.Groups["authority"].Value.Split("@")[-1]
    $restoreHost = $restoreMatch.Groups["authority"].Value.Split("@")[-1]
    if (
        $applicationHost -ieq $restoreHost -and
        $applicationMatch.Groups["database"].Value -ieq
            $restoreMatch.Groups["database"].Value
    ) {
        throw "Refusing to restore into the same database host and database name as the application."
    }
}

$manifestPath = "$($env:BACKUP_FILE).sha256"
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    $manifestLine = Get-Content -LiteralPath $manifestPath -TotalCount 1
    if ($manifestLine -notmatch '^(?<hash>[0-9A-Fa-f]{64})\s+') {
        throw "Backup SHA-256 manifest is malformed: $manifestPath"
    }

    $expectedHash = $Matches["hash"].ToUpperInvariant()
    $actualHash = (
        Get-FileHash -Algorithm SHA256 -LiteralPath $env:BACKUP_FILE
    ).Hash.ToUpperInvariant()
    if ($actualHash -cne $expectedHash) {
        throw "Backup SHA-256 does not match its manifest."
    }
}

$restoreClientUrl = [regex]::Replace(
    $env:RESTORE_DATABASE_URL,
    '^(postgresql|postgres)\+psycopg://',
    '$1://',
    [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
)

& pg_restore --list $env:BACKUP_FILE | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Backup archive validation failed; refusing to restore."
}

$emptyDatabaseQuery = @"
SELECT NOT EXISTS (
    SELECT 1
    FROM pg_class AS rel
    JOIN pg_namespace AS ns
      ON ns.oid = rel.relnamespace
    WHERE ns.nspname NOT IN ('pg_catalog', 'information_schema')
      AND ns.nspname NOT LIKE 'pg_toast%'
      AND rel.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
);
"@
$isEmpty = & psql `
    --no-psqlrc `
    --quiet `
    --tuples-only `
    --no-align `
    --set ON_ERROR_STOP=1 `
    $restoreClientUrl `
    --command $emptyDatabaseQuery
if ($LASTEXITCODE -ne 0) {
    throw "Could not verify the restore database; refusing to restore."
}
if (($isEmpty | Out-String).Trim() -cne "t") {
    throw "Restore target contains application data objects; refusing to restore into a non-empty database."
}

& pg_restore --exit-on-error --no-owner --dbname $restoreClientUrl $env:BACKUP_FILE
if ($LASTEXITCODE -ne 0) {
    throw "pg_restore failed with exit code $LASTEXITCODE. The target may be partially restored; inspect it before retrying."
}

Write-Output "Restore completed. Verify the isolated database and migration state before cutover."
