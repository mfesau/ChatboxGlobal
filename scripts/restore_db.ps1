<#
.SYNOPSIS
    Restaura la base de datos a partir de un respaldo de scripts\backup_db.ps1.

.DESCRIPTION
    Ejecuta el volcado dentro del contenedor `chatbox-postgres` con `psql`.
    Como el volcado incluye `--clean --if-exists`, esta operación borra y
    vuelve a crear cada tabla antes de cargar sus filas: es destructiva sobre
    el estado actual de la base. Pide confirmación salvo que se use -Force.

.PARAMETER BackupFile
    Nombre del fichero dentro de la carpeta backups\ (p. ej.
    "chatbox_20260824_180000.sql"), o ruta completa a él.

.PARAMETER Latest
    En vez de -BackupFile, usa el respaldo más reciente de backups\.

.PARAMETER Force
    Omite la confirmación interactiva.

.EXAMPLE
    powershell -File scripts\restore_db.ps1 -Latest
    powershell -File scripts\restore_db.ps1 -BackupFile chatbox_20260824_180000.sql
#>

[CmdletBinding(DefaultParameterSetName = "Named")]
param(
    [Parameter(ParameterSetName = "Named", Mandatory = $true, Position = 0)]
    [string]$BackupFile,

    [Parameter(ParameterSetName = "Latest", Mandatory = $true)]
    [switch]$Latest,

    [switch]$Force
)

$ErrorActionPreference = "Stop"

$ContainerName = "chatbox-postgres"
$BackupDirHost = Join-Path (Split-Path -Parent $PSScriptRoot) "backups"

if ($Latest) {
    $candidate = Get-ChildItem -Path $BackupDirHost -Filter "chatbox_*.sql" |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $candidate) {
        Write-Error "No hay ningún respaldo en '$BackupDirHost'."
        exit 1
    }
    $FileName = $candidate.Name
} else {
    $FileName = Split-Path -Leaf $BackupFile
}

$HostPath = Join-Path $BackupDirHost $FileName
if (-not (Test-Path $HostPath)) {
    Write-Error "No se encontró '$HostPath'. Los respaldos deben estar en la carpeta backups\ del proyecto."
    exit 1
}

$running = docker ps --filter "name=^$ContainerName$" --format "{{.Names}}" 2>$null
if ($running -ne $ContainerName) {
    Write-Error "El contenedor '$ContainerName' no está en ejecución."
    exit 1
}

if (-not $Force) {
    Write-Warning "Esto reemplaza el contenido actual de la base 'chatbox' con '$FileName'."
    $answer = Read-Host "Escriba SI para continuar"
    if ($answer -ne "SI") {
        Write-Output "Cancelado."
        exit 0
    }
}

Write-Output "Restaurando desde /backups/$FileName ..."
docker exec $ContainerName psql -U chatbox -d chatbox -v ON_ERROR_STOP=1 -f "/backups/$FileName"
if ($LASTEXITCODE -ne 0) {
    Write-Error "psql terminó con código $LASTEXITCODE; revise el mensaje anterior."
    exit $LASTEXITCODE
}

Write-Output "Restauración completa."
