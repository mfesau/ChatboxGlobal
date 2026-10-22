# PostgreSQL 17 con la extensión `vector`, para la base de conocimiento del
# asistente (ver db/migrations/0017_knowledge_base.sql).
#
# Se compila sobre `postgres:17-alpine` en lugar de usar la imagen
# `pgvector/pgvector:pg17`, que es Debian. El motivo es el directorio de datos
# que ya existe: PostgreSQL delega la ordenación de texto en la biblioteca C
# del sistema, y musl (Alpine) y glibc (Debian) no ordenan igual. Cambiar de
# una a otra sobre el mismo ./pgdata deja los índices de texto en un orden que
# ya no corresponde —las búsquedas dejan de encontrar filas que están ahí— y
# arreglarlo exige un REINDEX completo, o un volcado y una restauración.
#
# Compilar la extensión aquí cuesta un minuto la primera vez y evita todo eso.

FROM postgres:17-alpine AS build

# La versión se fija a propósito: una extensión que cambia sola bajo una base
# de datos en marcha no es una sorpresa que convenga tener.
ARG PGVECTOR_VERSION=v0.8.0

# `with_llvm=no` evita traerse clang y LLVM enteros: solo sirven para generar
# el bitcode de la compilación al vuelo, que es una optimización opcional.
RUN apk add --no-cache --virtual .build-deps \
        build-base git postgresql17-dev \
    && git clone --branch "$PGVECTOR_VERSION" --depth 1 \
        https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && make -C /tmp/pgvector OPTFLAGS="" with_llvm=no \
    && make -C /tmp/pgvector install with_llvm=no \
    && apk del .build-deps \
    && rm -rf /tmp/pgvector

FROM postgres:17-alpine

# Solo los artefactos: la imagen final no lleva compilador.
COPY --from=build /usr/local/lib/postgresql/vector.so /usr/local/lib/postgresql/
COPY --from=build /usr/local/share/postgresql/extension/vector* \
     /usr/local/share/postgresql/extension/
