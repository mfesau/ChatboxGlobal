<#
.SYNOPSIS
    Certificado autofirmado para el proxy inverso de la red local.

.DESCRIPTION
    Escribe `chatbox.crt` y `chatbox.key` en docker/nginx/certs, la carpeta que
    docker-compose.yml monta en el contenedor `chatbox-nginx`.

    Usa el `openssl` del equipo si lo hay —Git para Windows trae uno— y, si
    no, lo ejecuta dentro de un contenedor efímero de `python:3.13-slim`, la
    misma imagen base de la aplicación. Así no hace falta instalar nada, y el
    certificado se puede emitir aunque Docker todavía no esté arrancado: tiene
    que existir antes de levantar el proxy.

    Un certificado autofirmado cifra el tránsito igual que cualquier otro: la
    contraseña del agente y la cookie de sesión dejan de viajar legibles por la
    red. Lo que no hace es acreditar la identidad del servidor, de modo que el
    navegador avisará hasta que se instale este mismo certificado como raíz de
    confianza en los equipos del equipo. Los webhooks de WhatsApp y Facebook no
    lo aceptan en ningún caso: Meta exige una dirección pública con un
    certificado de una autoridad reconocida.

.PARAMETER Hostname
    Nombre o dirección por la que el equipo accede al servicio. Se escribe en
    el CN y en el SAN; sin SAN, los navegadores actuales rechazan el
    certificado. Por defecto, `chatbox.local`.

.PARAMETER Days
    Vigencia en días. Por defecto, 825 — el máximo que aceptan los navegadores.

.PARAMETER Force
    Sobrescribe un par ya existente. Sin esta marca, el script se detiene antes
    de tocar nada.

.EXAMPLE
    powershell -File scripts\generate_tls_cert.ps1
    powershell -File scripts\generate_tls_cert.ps1 -Hostname chat.empresa.local
    powershell -File scripts\generate_tls_cert.ps1 -Hostname 192.168.1.50
#>

[CmdletBinding()]
param(
    [string]$Hostname = "chatbox.local",
    [int]$Days = 825,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$CertDir = Join-Path (Split-Path -Parent $PSScriptRoot) "docker\nginx\certs"
$CertPath = Join-Path $CertDir "chatbox.crt"
$KeyPath = Join-Path $CertDir "chatbox.key"

function Write-Log([string]$Message) {
    Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
}

if (-not (Test-Path $CertDir)) {
    New-Item -ItemType Directory -Path $CertDir | Out-Null
}

if ((Test-Path $CertPath) -or (Test-Path $KeyPath)) {
    if (-not $Force) {
        throw "Ya existe un certificado en $CertDir. Use -Force para reemplazarlo."
    }
    Write-Log "Reemplazando el certificado existente (-Force)."
}

# Una dirección IP va en el SAN como `IP:`, un nombre como `DNS:`.
if ($Hostname -match '^\d{1,3}(\.\d{1,3}){3}$') {
    $SubjectAltName = "IP:$Hostname"
} else {
    $SubjectAltName = "DNS:$Hostname"
}

Write-Log "Emitiendo certificado para $Hostname ($SubjectAltName), $Days dias."

$LocalOpenSsl = Get-Command openssl -ErrorAction SilentlyContinue

if ($null -ne $LocalOpenSsl) {
    Write-Log "openssl del equipo: $($LocalOpenSsl.Source)"
    & $LocalOpenSsl.Source req -x509 -nodes -newkey rsa:2048 -sha256 `
        -days $Days `
        -subj "/CN=$Hostname" `
        -addext "subjectAltName=$SubjectAltName" `
        -keyout $KeyPath `
        -out $CertPath
} else {
    Write-Log "Sin openssl local; se emite dentro de un contenedor efimero."
    docker run --rm `
        -v "${CertDir}:/certs" `
        python:3.13-slim `
        openssl req -x509 -nodes -newkey rsa:2048 -sha256 `
            -days $Days `
            -subj "/CN=$Hostname" `
            -addext "subjectAltName=$SubjectAltName" `
            -keyout /certs/chatbox.key `
            -out /certs/chatbox.crt
}

if ($LASTEXITCODE -ne 0) {
    throw "openssl devolvio $LASTEXITCODE; el certificado no se genero."
}

Write-Log "Certificado: $CertPath"
Write-Log "Clave:       $KeyPath"
Write-Log "La clave privada queda fuera del repositorio (ver .gitignore)."
Write-Log "Arranque el proxy con: docker compose --profile tls up -d"
