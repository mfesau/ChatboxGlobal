<#
.SYNOPSIS
    Da de alta la tarea programada que ejecuta scripts\backup_db.ps1 sola.

.DESCRIPTION
    Crea (o reemplaza) una tarea del Programador de tareas de Windows con el
    nombre "ChatboxDbBackup", que corre bajo la cuenta actual cada
    -IntervalMinutes minutos (o -IntervalHours horas, si se prefiere esa
    unidad), empezando ya mismo. Requiere una ventana con privilegios
    elevados si la cuenta actual no puede registrar tareas sin ellos
    (normalmente no hace falta para una tarea del propio usuario).

.PARAMETER IntervalMinutes
    Frecuencia del respaldo, en minutos. Tiene prioridad sobre -IntervalHours
    si se indican los dos. Sin ninguno de los dos, cada 4 horas.

.PARAMETER IntervalHours
    Frecuencia del respaldo, en horas. Se ignora si se indica -IntervalMinutes.

.PARAMETER RetentionDays
    Se pasa tal cual a backup_db.ps1. Por defecto, 14 días.

.EXAMPLE
    powershell -File scripts\register_backup_task.ps1
    powershell -File scripts\register_backup_task.ps1 -IntervalMinutes 10
    powershell -File scripts\register_backup_task.ps1 -IntervalHours 2 -RetentionDays 30
#>

[CmdletBinding()]
param(
    [int]$IntervalMinutes = 0,
    [int]$IntervalHours = 4,
    [int]$RetentionDays = 14
)

$ErrorActionPreference = "Stop"

$TaskName = "ChatboxDbBackup"
$ScriptPath = Join-Path $PSScriptRoot "backup_db.ps1"
$LogPath = Join-Path (Split-Path -Parent $PSScriptRoot) "backups\task.log"

if ($IntervalMinutes -gt 0) {
    $interval = New-TimeSpan -Minutes $IntervalMinutes
    $label = "cada $IntervalMinutes minutos"
} else {
    $interval = New-TimeSpan -Hours $IntervalHours
    $label = "cada $IntervalHours horas"
}
# El límite de ejecución de una corrida debe quedar bien por debajo del
# intervalo entre corridas, para que dos nunca se superpongan aunque una se
# demore (pg_dump de esta base tarda bien por debajo de un minuto).
$executionLimitMinutes = [Math]::Max(1, [Math]::Min(5, [int]($interval.TotalMinutes / 2)))

$innerCommand = "& '$ScriptPath' -RetentionDays $RetentionDays *>> '$LogPath'"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    "-NoProfile -ExecutionPolicy Bypass -Command `"$innerCommand`""
) -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval $interval `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Minutes $executionLimitMinutes) `
    -MultipleInstances IgnoreNew
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

Write-Output "Tarea '$TaskName' registrada: $label, reteniendo $RetentionDays días."
Write-Output "Registro de ejecución: $LogPath"
Write-Output "Para quitarla más adelante: Unregister-ScheduledTask -TaskName '$TaskName'"
