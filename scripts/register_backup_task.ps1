<#
.SYNOPSIS
    Da de alta la tarea programada que ejecuta scripts\backup_db.ps1 sola.

.DESCRIPTION
    Crea (o reemplaza) una tarea del Programador de tareas de Windows con el
    nombre "ChatboxDbBackup", que corre bajo la cuenta actual cada
    -IntervalHours horas, empezando ya mismo. Requiere una ventana con
    privilegios elevados si la cuenta actual no puede registrar tareas sin
    ellos (normalmente no hace falta para una tarea del propio usuario).

.PARAMETER IntervalHours
    Frecuencia del respaldo. Por defecto, cada 4 horas.

.PARAMETER RetentionDays
    Se pasa tal cual a backup_db.ps1. Por defecto, 14 días.

.EXAMPLE
    powershell -File scripts\register_backup_task.ps1
    powershell -File scripts\register_backup_task.ps1 -IntervalHours 2 -RetentionDays 30
#>

[CmdletBinding()]
param(
    [int]$IntervalHours = 4,
    [int]$RetentionDays = 14
)

$ErrorActionPreference = "Stop"

$TaskName = "ChatboxDbBackup"
$ScriptPath = Join-Path $PSScriptRoot "backup_db.ps1"
$LogPath = Join-Path (Split-Path -Parent $PSScriptRoot) "backups\task.log"

$innerCommand = "& '$ScriptPath' -RetentionDays $RetentionDays *>> '$LogPath'"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    "-NoProfile -ExecutionPolicy Bypass -Command `"$innerCommand`""
) -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
# Docker Desktop solo es alcanzable dentro de la sesión interactiva del
# usuario actual; sin fijar aquí la cuenta con dominio, la tarea puede
# registrarse contra una cuenta local homónima que no ve el `docker` de esta
# sesión y fallar en silencio (código 1, sin bitácora).
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal `
    -Description "Respaldo periódico de la base 'chatbox' (pg_dump) del chat de equipo en c:\Users\me07\Chat." | Out-Null

Write-Output "Tarea '$TaskName' registrada: cada $IntervalHours horas, reteniendo $RetentionDays días."
Write-Output "Registro de ejecución: $LogPath"
Write-Output "Para quitarla más adelante: Unregister-ScheduledTask -TaskName '$TaskName'"
