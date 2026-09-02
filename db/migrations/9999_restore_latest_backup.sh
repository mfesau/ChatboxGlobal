#!/bin/sh
# Recuperación automática al arrancar, no una migración de esquema como el
# resto de este directorio (por eso el número tan alto: para correr siempre
# al final, después de 0001..000N).
#
# Postgres solo ejecuta docker-entrypoint-initdb.d/ cuando el volumen de
# datos está vacío — es decir, exactamente cuando ya no queda nada que
# recuperar. Si hay un respaldo en /backups (montado en docker-compose.yml
# como carpeta del host, no como volumen con nombre: sobrevive aunque el
# volumen de datos desaparezca), lo restaura solo; si no hay ninguno —una
# instalación realmente nueva—, no hace nada y deja el esquema recién creado
# por 0001_init.sql tal cual.
#
# Ver README, sección "Respaldo de la base de datos": esto es la contraparte
# automática de scripts/restore_db.ps1, para cuando nadie está mirando en el
# momento exacto en que el volumen se vació.
#
# Deliberadamente sin `set -e` ni `exit`: si el fichero llega sin permiso de
# ejecución (algo habitual al montarlo desde Windows), Postgres lo integra
# con `. "$f"` en vez de correrlo aparte, y un `exit`/`set -e` ahí adentro
# terminaría de golpe el proceso de arranque entero, no solo este script.

LATEST=$(ls -t /backups/chatbox_*.sql 2>/dev/null | head -n 1)

if [ -z "$LATEST" ]; then
    echo "[recuperación] No hay respaldos en /backups; se deja el esquema recién creado."
else
    echo "[recuperación] Volumen de datos nuevo; restaurando desde $LATEST ..."
    if psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$LATEST"; then
        echo "[recuperación] Restauración automática completa."
    else
        echo "[recuperación] ADVERTENCIA: la restauración automática falló; queda el esquema vacío de 0001_init.sql." >&2
    fi
fi
