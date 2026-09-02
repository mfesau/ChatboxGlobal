<#
.SYNOPSIS
    Respaldo periódico de la base de datos del orquestador.

.DESCRIPTION
    Ejecuta `pg_dump` dentro del contenedor `chatbox-postgres` y escribe el
    volcado directamente en `/backups`, una carpeta corriente del disco
    montada en el contenedor (ver docker-compose.yml) — no un volumen con
    nombre. Eso es a propósito: `docker compose down -v` borra los volúmenes
    con nombre pero nunca una carpeta común, así que el respaldo sobrevive
    aunque el volumen de datos de Postgres desaparezca (ya ocurrió varias
    veces sin que se haya identificado la causa).

    El volcado usa `--clean --if-exists`, de modo que restaurarlo alcanza por
    sí solo: no depende de que las migraciones ya hayan creado el esquema.

.PARAMETER RetentionDays
    Respaldos con más de esta antigüedad se borran al final. Por defecto, 14.

.EXAMPLE
    powershell -File scripts\backup_db.ps1
    powershell -File scripts\backup_db.ps1 -RetentionDays 30
#>

[CmdletBinding()]
param(
    [int]$RetentionDays = 14
)

$ErrorActionPreference = "Stop"

$ContainerName = "chatbox-postgres"
$BackupDirHost = Join-Path (Split-Path -Parent $PSScriptRoot) "backups"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$FileName = "chatbox_$Stamp.sql"
$ContainerPath = "/backups/$FileName"
$HostPath = Join-Path $BackupDirHost $FileName

function Write-Log([string]$Message) {
    Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
}

if (-not (Test-Path $BackupDirHost)) {
    New-Item -ItemType Directory -Path $BackupDirHost | Out-Null
}

$running = docker ps --filter "name=^$ContainerName$" --format "{{.Names}}" 2>$null
if ($running -ne $ContainerName) {
    Write-Log "El contenedor '$ContainerName' no está en ejecución; no se hizo ningún respaldo."
    exit 1
}

Write-Log "Iniciando volcado -> $ContainerPath"
docker exec $ContainerName pg_dump -U chatbox -d chatbox --no-owner --clean --if-exists -f $ContainerPath
if ($LASTEXITCODE -ne 0) {
    Write-Log "pg_dump terminó con código $LASTEXITCODE; revise el mensaje anterior."
    exit $LASTEXITCODE
}

if (-not (Test-Path $HostPath)) {
    Write-Log "ADVERTENCIA: pg_dump no reportó error, pero '$HostPath' no aparece en el host."
    exit 1
}

$size = (Get-Item $HostPath).Length
Write-Log ("Respaldo completo: {0} ({1:N0} KB)" -f $FileName, ($size / 1KB))

$cutoff = (Get-Date).AddDays(-$RetentionDays)
$old = Get-ChildItem -Path $BackupDirHost -Filter "chatbox_*.sql" |
    Where-Object { $_.LastWriteTime -lt $cutoff }
if ($old) {
    $old | Remove-Item -Force
    Write-Log ("Se eliminaron {0} respaldo(s) de más de {1} días." -f $old.Count, $RetentionDays)
}

$remaining = (Get-ChildItem -Path $BackupDirHost -Filter "chatbox_*.sql").Count
Write-Log "Respaldos vigentes en '$BackupDirHost': $remaining"
