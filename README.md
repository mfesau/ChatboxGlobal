# ChatboxGlobal — chat de equipo en red local

Servicio que reúne en **un único punto de entrada** todos los chats —WhatsApp,
Facebook Messenger, Microsoft Teams y el chatbox web propio—, los normaliza a
un formato interno común, les aplica lógica de negocio y de IA, y permite
**derivarlos entre compañeros conservando el historial íntegro**. De cada
canal se puede conectar tantas cuentas como se quiera —varios números, varias
páginas, varios equipos—, cada una con su propio departamento de destino.

Cada agente ve la cola común y su propia cartera. La supervisión ve todo. Se
despliega en la red interna: las credenciales locales bastan por sí solas, sin
depender de ningún proveedor externo; el inicio de sesión único (SAML) es
opcional, para quien lo quiera además de eso.

---

## 1. Cómo trabaja el equipo

```
   WhatsApp ─┐
   Facebook ─┤
   Teams ────┼──▶  ┌──────────────────┐   nadie asignado
   Chatbox ──┘     │   COLA COMÚN     │◀────────────────────┐
                   │ punto único de   │                     │
                   │ entrada de todo  │                     │ devolver
                   └────────┬─────────┘                     │
                            │ «Atender yo»                  │
                            ▼                               │
                   ┌──────────────────┐    derivar    ┌──────┴───────────┐
                   │  Cartera de Ana  │─────────────▶ │ Cartera de Luis  │
                   │  (solo Ana la ve)│   + motivo    │ (solo Luis la ve)│
                   └──────────────────┘               └──────────────────┘
                            │                                  │
                            └────────────┬─────────────────────┘
                                         ▼
                            ┌────────────────────────────┐
                            │  SUPERVISIÓN: lo ve todo,  │
                            │  reasigna y mide la carga  │
                            └────────────────────────────┘

   El hilo NUNCA se mueve ni se copia: es siempre la misma fila. Solo cambia el
   responsable, y cada cambio queda anotado con autor, destinatario y motivo.
```

### Reglas de visibilidad

| Rol | Cola común | Cartera propia | Cartera de compañeros | Panel de supervisión |
|---|---|---|---|---|
| `agent` | Sí, la de su(s) departamento(s) | Sí | **No** | No |
| `supervisor` / `admin` | Sí, sin restricción | Sí | Sí | Sí |

El filtro se aplica en SQL, no en memoria: un agente jamás recibe filas ajenas.
Al pedir una conversación que no le corresponde obtiene **404 y no 403**, porque
confirmar que existe ya revelaría información sobre la cartera de un compañero.

### Departamentos

Una conversación recibe su departamento por una de dos vías: lo hereda de la
cuenta de canal por la que entró —cada número de WhatsApp, página de Facebook o
bot de Teams puede llevar el suyo—, o se lo asigna quien la deriva. Una cuenta
de canal sin departamento deja el hilo en la cola común, visible para
cualquiera. En cuanto tiene departamento, solo lo ve quien atiende ese
departamento: el suyo por defecto, más los que el **administrador** le
otorgue. Supervisión y administración no tienen esa restricción. Derivar a un
departamento reutiliza el mismo panel "Derivar a un compañero": basta elegir
uno en vez de una persona, y el hilo vuelve a la cola —sin dueño, atendido
otra vez por el asistente— pero acotado a ese departamento.

### Qué sobrevive a una derivación

* Todos los mensajes del cliente, del asistente y de cada agente anterior.
* Las notas internas del equipo, invisibles para el cliente.
* La traza completa de derivaciones: quién la atendió, quién la pasó y por qué.
* El canal de origen: la respuesta sigue saliendo por WhatsApp o por Teams,
  aunque el hilo haya cambiado de manos tres veces.

Cuando una conversación tiene responsable humano, el asistente automático guarda
silencio. Al devolverla a la cola, vuelve a atenderla.

---

## 2. Arquitectura

```
┌──────────────┐   webhook firmado    ┐
│  WhatsApp    │─────────────────────▶│
└──────────────┘                      │
┌──────────────┐   Activity + JWT     │   ┌─────────────────────────────────┐
│  MS Teams    │─────────────────────▶├──▶│  Adaptadores de canal           │  (1) recepción
│  Direct Line │                      │   │  verify → parse → send          │
└──────────────┘                      │   └───────────────┬─────────────────┘
┌──────────────┐   WebSocket / REST   │                   │ formato canónico
│  Chatbox web │─────────────────────▶┘                   ▼                     (2) normalización
└──────────────┘                          ┌─────────────────────────────────┐
                                          │  Orquestador                    │
                                          │  idempotencia → identidad →     │
                                          │  persistencia → cadena          │
                                          └───────────────┬─────────────────┘
                                                          ▼
                                          ┌─────────────────────────────────┐
                                          │  Cadena de handlers             │  (3) lógica
                                          │  aforo → control humano →       │
                                          │  comandos → IA → red de         │
                                          │  seguridad                      │
                                          └───────────────┬─────────────────┘
                                                          ▼
                                          ┌─────────────────────────────────┐
                                          │  Cola de salida (outbox)        │  (4) entrega
                                          │  reintentos + retroceso         │
                                          └───────────────┬─────────────────┘
                                                          ▼
                                                 canal de origen
                    ┌───────────────────────────────────────────────────────┐
                    │  PostgreSQL: inquilinos, agentes, sesiones, contactos, │
                    │  conversaciones, mensajes, derivaciones, notas,        │
                    │  eventos, cola, idempotencia, auditoría, coste de IA   │
                    └───────────────────────────────────────────────────────┘
```

| Etapa | Responsable | Fichero |
|---|---|---|
| 1. Recepción del evento | Endpoint del canal más `verify_request` del adaptador | [webhooks.py](app/api/webhooks.py), [base.py](app/channels/base.py) |
| 2. Normalización | `parse` del adaptador → `InboundMessage` | [whatsapp.py](app/channels/whatsapp.py), [msbot.py](app/channels/msbot.py), [web.py](app/channels/web.py) |
| 3. Lógica de negocio y de IA | Cadena de handlers | [pipeline.py](app/core/pipeline.py), [handlers/](app/handlers) |
| 4. Envío de la respuesta | Cola de salida más `send` del adaptador | [dispatcher.py](app/core/dispatcher.py) |
| Trabajo en equipo | Alcance por rol, derivación y notas | [console.py](app/api/console.py), [deps.py](app/api/deps.py) |

---

## 3. Estructura del proyecto

```
app/
├── main.py                 Punto de entrada ASGI y ciclo de vida
├── config.py               Configuración por entorno (pydantic-settings)
├── core/
│   ├── envelope.py         Formato interno común
│   ├── orchestrator.py     Capa central: recibe, normaliza, aplica, responde
│   ├── pipeline.py         Cadena de middlewares y contexto del turno
│   ├── dispatcher.py       Trabajadores de la cola de salida
│   ├── security.py         Contraseñas con scrypt y tokens de sesión
│   └── hub.py              Bus de difusión para WebSockets
├── channels/               WhatsApp, Microsoft Bot Framework, chatbox web
├── handlers/               Aforo, control humano, comandos, IA, red de seguridad
├── db/
│   ├── models.py           Esquema relacional (15 tablas)
│   ├── repositories.py     Todas las sentencias SQL del dominio
│   └── engine.py           Motor y unidad de trabajo asíncrona
├── api/
│   ├── webhooks.py         Entrada de los canales externos
│   ├── auth.py             Inicio y cierre de sesión de los agentes
│   ├── console.py          Bandejas, derivación, notas y supervisión
│   ├── deps.py             Identidad de la petición y permisos
│   └── ws.py               WebSockets del chatbox y de la consola
└── web/                    Chatbox y consola de equipo (HTML, CSS, JS)

db/
├── migrations/0001_init.sql       Esquema completo, generado desde los modelos
├── migrations/0002_teamwork.sql   Añadidos de equipo sobre una base existente
└── alembic/                       Entorno de migraciones

scripts/
├── init_db.py              Crea el esquema en desarrollo
├── create_agent.py         Da de alta agentes y supervisores
└── generate_ddl.py         Regenera el DDL desde los modelos

tests/                      131 pruebas sobre SQLite, sin servicios externos
```

---

## 4. Puesta en marcha en la red local

### Paso 1 — configuración

```bash
cp .env.example .env
```

> **Cuidado con los comentarios en línea.** `python-dotenv` toma como valor todo
> lo que sigue al `=`, comentario incluido: `ADMIN_API_KEY=  # obligatoria` deja
> una clave que vale literalmente `"# obligatoria"`. Deje siempre el comentario
> en su propia línea, como hace la plantilla.

En `.env`, ajuste al menos:

```
PUBLIC_BASE_URL=http://192.168.1.50:8000
CORS_ALLOW_ORIGINS=http://192.168.1.50:8000
SESSION_COOKIE_SECURE=false
```

`SESSION_COOKIE_SECURE` debe quedar en `false` mientras sirva por HTTP: el
navegador descartaría una cookie `Secure` recibida sin TLS.

### Paso 2 — base de datos y equipo

```bash
docker compose up -d postgres
python scripts/init_db.py
python scripts/create_agent.py --email supervisor@empresa.local --nombre "Marta Giménez" --rol supervisor
python scripts/create_agent.py --email ana@empresa.local --nombre "Ana Rodríguez" --rol agent
```

Sin `--password` el script genera una contraseña temporal y la muestra una única
vez. `--listar` enumera el equipo y `--restablecer` genera una contraseña nueva.
`--departamento <nombre>` fija el departamento principal; debe crearse antes
desde la consola de administración (`POST /api/departments`).

### Paso 3 — arranque accesible desde la red

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

`--host 0.0.0.0` es lo que hace que el servicio acepte conexiones de otros
equipos; con el valor por omisión solo responde a la propia máquina. Averigüe la
dirección del servidor con `ipconfig` en Windows o `ip addr` en Linux, y abra el
puerto en el cortafuegos:

```bash
netsh advfirewall firewall add rule name="Chat equipo" dir=in action=allow protocol=TCP localport=8000
```

Con Docker Compose, `docker compose up --build` ya publica el puerto 8000 en
todas las interfaces.

### Paso 4 — acceso del equipo

Una sola dirección para todos: `http://192.168.1.50:8000/`. El mismo
formulario de correo y contraseña sirve tanto para un cliente como para
alguien del equipo — el servidor determina cuál es por la cuenta que
encuentra, y lo manda a la consola o al chatbox según corresponda. `/console`
ya no es una dirección de acceso aparte: solo se llega ahí tras iniciar
sesión, y quien la visite sin sesión de agente vuelve a `/` automáticamente.

| Quién | Dirección |
|---|---|
| Cualquiera (clientes, agentes, supervisión) | `http://192.168.1.50:8000/` |
| Documentación de la API | `http://192.168.1.50:8000/docs` |

---

## 5. La consola de equipo

**«/» es la entrada única** de toda la aplicación (`POST /api/session/login`):
un solo formulario de correo y contraseña, sin que quien lo llena tenga que
elegir de antemano si es cliente o del equipo. El servidor prueba primero si
la cuenta es de un agente y, si no, si es de un cliente, y deja la sesión que
corresponde — a un agente lo manda a `/console`; a un cliente lo deja en el
chatbox, ahí mismo. `/console` ya no tiene login propio: sin sesión de agente
válida, redirige a `/`; con una sesión de agente ya abierta, `/` redirige para
el otro lado, directo a `/console`.

Además del formulario, está el botón **«Iniciar sesión con SSO»**, que solo
aparece cuando hay un proveedor de identidad configurado (ver «Inicio de
sesión único» en la sección 6) — es exclusivo para el equipo, así que
siempre entrega en `/console`. Quien entra por SSO por primera vez con un
correo que todavía no tiene cuenta se da de alta automáticamente como agente
—nunca con más permisos, aunque el proveedor lo marque como alguien
importante—; quien ya tenía cuenta conserva su rol tal cual estaba.

Tres pestañas, con su contador en vivo:

* **Cola común** — todo lo que entra sin responsable. El botón «Atender yo» lo
  asigna a quien pulsa y silencia al asistente automático.
* **Mis chats** — la cartera propia.
* **Todos** — solo aparece para supervisión.

El panel derecho reúne el trabajo en equipo:

* **Derivar a un compañero o a un departamento** — desplegable con el equipo
  (`•` disponible, `◦` ausente) o, en su lugar, con los departamentos; el
  motivo queda guardado como nota interna, de modo que quien la recibe
  entiende el contexto sin preguntar. Derivar a un departamento libera al
  responsable y devuelve la conversación a la cola, acotada a quien atienda
  ese departamento.
* **Notas internas** — visibles solo para el equipo. Se guardan en su propia
  tabla y no en `messages`, así ningún adaptador de canal puede enviarlas al
  cliente por descuido.
* **Historial de derivaciones** — la cadena completa de responsables.
* **Datos del contacto** — nombre, teléfono y correo, con un historial de
  comentarios con autor y fecha. Cualquier agente con acceso a la conversación
  puede consultarla; editarla queda reservado a supervisión y administración.

El botón **«Contactos»**, visible solo para supervisión y administración,
abre el directorio completo de clientes del inquilino: nombre, teléfono,
correo, cantidad de conversaciones y última actividad, con una búsqueda por
cualquiera de esos tres primeros datos. La ficha de cada contacto reúne lo
mismo que el panel «Datos del contacto» —edición y comentarios— más el
listado de **todas sus conversaciones**, en cualquier canal; al elegir una,
la consola la abre directamente y cierra el directorio.

El compositor admite adjuntar una imagen (📎), tanto en el chatbox del cliente
como en la respuesta del agente; se sube a `/uploads` y se muestra como burbuja
de imagen en ambos lados.

Por debajo de 1180 px de ancho el panel derecho pasa a ser un cajón que se abre
con el botón «Equipo»: en pantallas estrechas sigue siendo alcanzable en lugar de
desaparecer.

El **panel de supervisión** muestra la carga abierta por agente, la cola común
como fila aparte, los mensajes por canal y las últimas derivaciones con su motivo.

El **panel de administración** («Administrar usuarios», solo para `admin`)
reúne el texto de la respuesta automática del asistente, la creación de
cuentas, la creación de departamentos y la tabla «Usuarios registrados».
Esa tabla concentra toda la configuración por persona en una sola fila —nombre,
departamento principal, departamentos adicionales y una nueva contraseña
opcional— que se guarda de una vez con un solo botón «Guardar»; dejar la
contraseña en blanco la conserva sin cambios. El botón «Desactivar»/«Reactivar»
no borra la fila: sigue apareciendo en el historial de derivaciones, notas y
auditoría, solo que ya no puede iniciar sesión. No se puede desactivar la
propia cuenta ni al único administrador activo que quede.

La sección **«Cuentas de canal»** conecta tantos números de WhatsApp, páginas
de Facebook o equipos de Teams como se necesite, cada uno con su propio
departamento: una conversación nueva de esa cuenta cae directo en esa cola,
sin que nadie tenga que derivarla a mano. Una cuenta sin departamento sigue
en la cola común, igual que siempre. Ver «Varias cuentas por canal» en la
sección 6 para el detalle de credenciales por canal.

Las novedades llegan por WebSocket: cuando alguien le deriva una conversación,
aparece en su bandeja sin recargar la página. Los enlaces al CSS y al JavaScript
llevan una huella derivada de la fecha de modificación de los ficheros, de modo
que tras una actualización nadie se queda con una versión antigua en caché.

---

## 6. Configuración de los canales

### Varias cuentas por canal, cada una con su departamento

WhatsApp, Facebook y Teams admiten **tantas cuentas como se quiera** —varios
números, varias páginas, varios equipos—, cada una conectada desde «Cuentas
de canal» en el panel de administración. Con departamento asignado, una
conversación nueva de esa cuenta cae directo en esa cola; sin departamento,
sigue en la cola común, exactamente como si esta función no existiera.

El identificador que pide el formulario es, según el canal: el
`phone_number_id` en WhatsApp, el id de la página en Facebook, o el id del
equipo en Teams —este último no se conoce de antemano, así que la cuenta se
crea sola con el primer mensaje que llegue de ese equipo; luego se le asigna
un departamento desde la misma pantalla—.

Las credenciales propias de una cuenta (el token de acceso) se guardan
cifradas, nunca en claro: hace falta `SECRET_ENCRYPTION_KEY` en `.env` antes
de poder cargar una. Genérela una sola vez con:

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Cambiarla vuelve ilegibles las credenciales ya guardadas —no la clave global
de WhatsApp en `.env`, que no pasa por aquí—, así que consérvela en un lugar
seguro. WhatsApp y Facebook difieren en si el token es obligatorio por cuenta:

* **WhatsApp**: opcional. Si falta, se usa el `WHATSAPP_ACCESS_TOKEN` global
  de más abajo — cubre el caso común de un solo token de sistema para varios
  números de la misma cuenta de WhatsApp Business.
* **Facebook**: obligatorio. Cada página tiene su propio token; no hay uno
  "por defecto" razonable.
* **Teams**: no aplica. Un solo bot de Azure ya puede estar en muchos equipos;
  no hace falta una credencial por equipo.

### WhatsApp Cloud API

1. Cree una aplicación en Meta for Developers y añada el producto WhatsApp.
2. Registre el webhook en `https://SU_DOMINIO/webhooks/whatsapp` con el valor de
   `WHATSAPP_VERIFY_TOKEN` y suscríbase al campo `messages`.
3. Complete `WHATSAPP_APP_SECRET`, `WHATSAPP_ACCESS_TOKEN` y
   `WHATSAPP_PHONE_NUMBER_ID`.

Se valida la cabecera `X-Hub-Signature-256` sobre el cuerpo sin modificar. Con
`ENVIRONMENT=prod` y sin `WHATSAPP_APP_SECRET`, el servicio rechaza toda entrada
en lugar de aceptarla sin verificar.

Meta exige una dirección pública con TLS: el webhook no alcanza una IP privada.
Publique el servicio tras un proxy inverso con certificado, o use un túnel.

**Antivirus o proxy con inspección TLS (p. ej. Kaspersky):** si el contenedor
falla al llamar a `graph.facebook.com` con `certificate verify failed:
self-signed certificate in certificate chain`, es porque algo en la red
reemplaza el certificado de Meta por uno propio para revisar el tráfico
cifrado. La solución no es desactivar la verificación: hay que sumar esa raíz
al conjunto de confianza del contenedor. Exporte el certificado raíz desde el
almacén de Windows (`Cert:\LocalMachine\Root`, o el que corresponda al
antivirus) en formato PEM y colóquelo en `docker/certs/`; el `Dockerfile` lo
suma al paquete de `certifi` y expone `SSL_CERT_FILE` con el resultado, así
que toda llamada saliente —WhatsApp, IA, Microsoft— queda cubierta sin tocar
el código. La carpeta puede quedar vacía en una red sin inspección TLS: el
resultado es entonces idéntico al paquete normal de `certifi`.

### Facebook Messenger

1. En la misma aplicación de Meta for Developers (o en una nueva), añada el
   producto Messenger.
2. Registre el webhook en `https://SU_DOMINIO/webhooks/facebook` con el valor
   de `FACEBOOK_VERIFY_TOKEN` y suscríbase a los campos `messages`,
   `messaging_postbacks`.
3. Complete `FACEBOOK_APP_SECRET` en `.env` — es lo único común a todas las
   páginas; el token de cada página se carga por separado desde «Cuentas de
   canal» (ver más arriba), porque ahí sí es obligatorio, no hay uno global.

Misma disciplina de firma que WhatsApp (`X-Hub-Signature-256` sobre el cuerpo
sin modificar) y la misma exigencia de una dirección pública con TLS.

### Microsoft Bot Framework

1. Cree un recurso *Azure Bot* con *Messaging endpoint*
   `https://SU_DOMINIO/api/messages`.
2. Complete `MICROSOFT_APP_ID` y `MICROSOFT_APP_PASSWORD`; en modo
   `SingleTenant`, también `MICROSOFT_APP_TENANT_ID`.
3. Instale el bot en tantos equipos de Microsoft Teams como necesite — es el
   mismo bot para todos, no hace falta un registro por equipo. Cada equipo
   aparece solo en «Cuentas de canal» tras su primer mensaje; desde ahí se le
   asigna un departamento.

Cada llamada se valida contra los metadatos OpenID del servicio de canales
—firma, audiencia y emisor—. Las respuestas salen por la Connector API con un
token de Microsoft Entra ID que el adaptador renueva por su cuenta. Frente al
Bot Framework Emulator, ponga `MICROSOFT_VALIDATE_JWT=false`.

### Inicio de sesión único (SAML 2.0 / Microsoft Entra ID)

Solo para la consola del equipo — el chatbox de clientes no cambia. Convive
con el correo y contraseña de siempre: mientras falte cualquiera de los tres
datos del IdP, el botón queda oculto y nada más cambia.

1. En Microsoft Entra ID: dé de alta una *aplicación empresarial* con inicio
   de sesión único SAML. Como *Identificador de entidad* y *URL de respuesta
   (ACS)*, use respectivamente `https://SU_DOMINIO/saml/metadata` y
   `https://SU_DOMINIO/saml/acs` — o cárguelos a partir de los metadatos que
   sirve el propio servicio en esa primera URL, una vez configurado.
2. Complete `SAML_IDP_ENTITY_ID` (Entra ID Identifier), `SAML_IDP_SSO_URL`
   (Login URL) y `SAML_IDP_X509_CERT` (el certificado en Base64, sin las
   líneas `-----BEGIN/END CERTIFICATE-----`) con los datos de esa aplicación.
3. En los atributos y notificaciones de la aplicación, asegúrese de que viaje
   el correo del usuario — como NameID en formato correo, o como el reclamo
   `.../claims/emailaddress` — y opcionalmente el nombre visible
   (`.../claims/displayname`).

Quien entra por primera vez con un correo sin cuenta previa se da de alta
automáticamente como agente, con el rol básico y sin contraseña propia
(`password_hash` queda `NULL`: esa cuenta solo entra por SSO, a menos que
administración le fije una contraseña aparte). Una cuenta desactivada no
revive por este camino: sigue rechazada aunque el IdP la autentique.
`GET /saml/metadata` y `GET /saml/login` responden 404 mientras falte
configurar el IdP, así que no hace falta ningún interruptor aparte para
desactivar la función.

### Chatbox web

No requiere configuración y funciona íntegramente dentro de la red local. El
login es obligatorio: quien visita `/` debe registrarse con correo y
contraseña (`POST /api/contact/register`) o iniciar sesión por el formulario
único (`POST /api/session/login`, ver sección 5) antes de poder escribir; la
sesión viaja en la cookie `chatbox_contact_session`, independiente de la
cookie de la consola.

El encabezado muestra «Cliente `<nombre>`» mientras atiende el asistente
automático, y el nombre del agente o supervisor en cuanto alguien toma la
conversación —sin recargar la página, por WebSocket—.

---

## 7. Garantías operativas

| Riesgo | Mecanismo |
|---|---|
| El proveedor reintenta el webhook y el bot responde dos veces | Tabla `inbound_dedupe` con `INSERT … ON CONFLICT DO NOTHING` |
| El webhook agota su tiempo de espera mientras se genera la respuesta | La respuesta se encola en `outbox` en la misma transacción |
| Caída momentánea de la Graph API o de la Connector API | Reintentos con retroceso exponencial (5 s → 1 h) y cola muerta |
| Un trabajador muere a mitad de un envío | El mantenimiento reencola los bloqueos de más de cinco minutos |
| Varias instancias compitiendo por la cola | `SELECT … FOR UPDATE SKIP LOCKED` en PostgreSQL |
| Dos agentes contestando al mismo cliente | La conversación tiene un único responsable; tomarla es atómico |
| Un agente leyendo la cartera de un compañero | Filtro en SQL por `assignee_id` y respuesta 404 |
| Robo de la base de credenciales | Contraseñas derivadas con `scrypt` y sal propia; de las sesiones solo se guarda el resumen |
| Sesión que hay que revocar de inmediato | Las sesiones viven en la base: basta con borrar la fila |
| Acceso a la consola o al chatbox sin credenciales | El login es obligatorio en todo entorno, también en `dev`; no existe acceso anónimo ni siquiera sin `ADMIN_API_KEY` configurada |
| Un fallo en la lógica de negocio deja al usuario sin respuesta | La cadena aísla cada handler y `FallbackHandler` garantiza contestación |
| Acuses de recibo desordenados | El estado del mensaje solo avanza, nunca retrocede |
| El volumen de Postgres desaparece (ya ocurrió varias veces sin causa identificada) | Respaldo periódico con `pg_dump`, ver más abajo |
| Alguien con acceso al IdP se gana permisos de supervisión o administración | El alta automática por SSO siempre entra con el rol básico de agente; el ascenso de rol es un paso aparte, manual, desde la consola |
| Un enlace de SSO manipulado redirige tras el login a un sitio ajeno | `next`/`RelayState` solo admite una ruta propia (`/algo`); cualquier otro valor cae a `/console` |
| Robo de la base expone los tokens de WhatsApp/Facebook de cada cuenta | Se guardan cifrados (`SECRET_ENCRYPTION_KEY`), nunca en claro; los adaptadores no acceden a la base directamente |

### Respaldo de la base de datos

`scripts/backup_db.ps1` ejecuta `pg_dump --clean --if-exists` dentro de
`chatbox-postgres` y escribe el volcado en `backups/`, una carpeta común del
proyecto montada en el contenedor (`docker-compose.yml`) — no un volumen con
nombre. La diferencia importa: `docker compose down -v` borra los volúmenes
con nombre pero nunca una carpeta del disco, así que los respaldos sobreviven
aunque el volumen de datos desaparezca por completo, que es exactamente lo que
ya pasó más de una vez en este proyecto sin que se identificara la causa.

- **Alta de la tarea programada** (una sola vez):
  `powershell -File scripts\register_backup_task.ps1` — crea la tarea
  «ChatboxDbBackup» del Programador de tareas de Windows, cada 4 horas por
  defecto (`-IntervalHours`), reteniendo 14 días de respaldos por defecto
  (`-RetentionDays`). Corre con la cuenta con dominio del usuario actual
  (`Interactive`): sin eso, la tarea no alcanza el Docker Desktop de la
  sesión y falla en silencio.
- **Respaldo manual**: `powershell -File scripts\backup_db.ps1`.
- **Restaurar**: `powershell -File scripts\restore_db.ps1 -Latest` (o
  `-BackupFile <nombre.sql>` para uno puntual). Pide confirmación porque
  sobrescribe la base actual; `-Force` la omite.
- **Quitar la tarea**: `Unregister-ScheduledTask -TaskName ChatboxDbBackup`.

---

## 8. Modelo de datos

Diecinueve tablas, con clave primaria `uuid` y aislamiento por `tenant_id`:

| Tabla | Contenido |
|---|---|
| `tenants` | Empresa, marca o unidad de negocio |
| `agents` | Equipo: rol, presencia, departamento principal y contraseña derivada |
| `agent_sessions` | Sesiones de consola; se guarda el resumen del token |
| `departments` | Departamentos del inquilino, por nombre |
| `agent_departments` | Departamentos adicionales que un agente puede atender |
| `channel_accounts` | Identidad del bot en cada canal |
| `contacts`, `contact_identities` | Persona externa, sus identificadores por canal y —si se registró en el chatbox— su contraseña derivada |
| `contact_sessions` | Sesiones del chatbox público; se guarda el resumen del token |
| `contact_comments` | Historial de comentarios de supervisión sobre un contacto, con autor y fecha |
| `conversations` | Hilo por canal, con responsable, `conversation_ref` y `state` |
| `messages` | Mensaje en formato canónico, con la carga original en `raw` |
| `message_events` | Ciclo de vida de entrega |
| `assignments` | Registro inmutable de cada derivación |
| `internal_notes` | Anotaciones visibles solo para el equipo |
| `outbox` | Cola transaccional de salida |
| `inbound_dedupe` | Claves de idempotencia |
| `ai_runs` | Tokens, latencia y motivo de parada de cada llamada al modelo |
| `audit_log` | Acciones administrativas, accesos y cambios de responsable |

Las columnas semiestructuradas usan `JSONB`. Los enumerados se guardan como
`VARCHAR` y no como tipo `ENUM` nativo: añadir un canal, un estado o un rol no
exige `ALTER TYPE` ni migración de datos.

### Evolución del esquema

```bash
python scripts/generate_ddl.py > db/migrations/0001_init.sql   # regenerar el DDL
alembic revision --autogenerate -m "descripción del cambio"    # nueva migración
alembic upgrade head
```

Sobre una instalación que ya tenía el esquema anterior, aplique en orden
`db/migrations/0002_teamwork.sql`, `0003_contact_login.sql`,
`0004_contact_comments.sql` y `0005_departments.sql`.

---

## 9. Ampliación

### Añadir un canal

```python
# app/channels/telegram.py
from app.channels.base import ChannelAdapter, register_channel
from app.core.envelope import ChannelKind

@register_channel(ChannelKind.TELEGRAM)      # añada el valor al enumerado
class TelegramAdapter(ChannelAdapter):
    async def verify_request(self, *, headers, body): ...
    async def parse(self, *, payload, headers) -> list[InboundMessage]: ...
    async def send(self, *, ref, message) -> DeliveryReceipt: ...
```

Impórtelo en `app/channels/__init__.py`. La ruta genérica
`POST /webhooks/{canal}` ya lo atiende, y sus conversaciones aparecen en la
misma cola común que el resto.

### Añadir lógica de negocio

```python
class OrderStatusHandler(Handler):
    name = "estado_pedido"

    async def handle(self, ctx: TurnContext, next_) -> None:
        if "pedido" not in ctx.text.lower():
            await next_()
            return
        ctx.reply("Su pedido sale mañana.", quick_replies=[{"id": "ok", "title": "Gracias"}])
```

Insértelo en `build_default_pipeline` antes del handler de IA: los handlers
deterministas resuelven sin gastar una llamada al modelo.

### Capa de IA

[handlers/ai.py](app/handlers/ai.py) invoca a Claude con el SDK oficial de
Anthropic: transmisión por fragmentos que el chatbox muestra en directo,
pensamiento adaptativo, caché de prompt sobre el prefijo estable y reintento en
servidor ante una declinación de seguridad. Expone la herramienta
`derivar_a_agente`, con la que el modelo devuelve la conversación a la cola
humana. Sin `ANTHROPIC_API_KEY` el handler se desactiva y el servicio sigue
funcionando con lógica determinista.

---

## 10. Pruebas y verificación

```bash
python -m pytest tests/ -q      # 131 pruebas
python -m ruff check .
```

La batería corre sobre SQLite mediante `aiosqlite`, sin PostgreSQL ni acceso a
red. Cubre la normalización de ambos proveedores, la validación de firma y de
JWT, la composición de las cargas de salida, la idempotencia, la unificación de
contactos, los reintentos de la cola, el descarte a cola muerta, el contrato
HTTP completo y, en [test_teamwork.py](tests/test_teamwork.py), todo el trabajo
en equipo: credenciales, alcance por rol, derivación con historial intacto,
notas internas y panel de supervisión.

---

## 11. Referencia de endpoints

### Canales

| Método | Ruta | Uso |
|---|---|---|
| `GET` | `/webhooks/whatsapp` | Reto de suscripción de Meta |
| `POST` | `/webhooks/whatsapp` | Mensajes y acuses de WhatsApp |
| `GET` | `/webhooks/facebook` | Reto de suscripción de Meta (Messenger) |
| `POST` | `/webhooks/facebook` | Mensajes y acuses de Facebook Messenger |
| `POST` | `/api/messages` | *Activities* del Bot Framework |
| `POST` | `/api/web/messages` | Mensaje del chatbox por REST |
| `POST` | `/webhooks/{canal}` | Ruta genérica para canales nuevos |
| `WS` | `/ws/chat` | Conversación del chatbox web |
| `WS` | `/ws/inbox` | Novedades para la consola del equipo |

### Sesión

| Método | Ruta | Uso |
|---|---|---|
| `POST` | `/api/session/login` | **Login unificado** (formulario de «/»): agente o cliente, según la cuenta |
| `POST` | `/api/auth/login` | Entrada del agente por su cuenta, sin pasar por el login unificado |
| `POST` | `/api/auth/logout` | Cierre y revocación de la sesión |
| `GET` | `/api/auth/me` | Identidad y rol efectivos |
| `GET` | `/api/auth/sso` | Si hay que mostrar el botón de inicio de sesión único |
| `POST` | `/api/auth/presence` | Disponibilidad: `available`, `away`, `offline` |
| `GET` | `/saml/metadata` | Metadatos del SP, para darlos de alta en el IdP (404 sin configurar) |
| `GET` | `/saml/login` | Arranca el inicio de sesión único, redirige al IdP (404 sin configurar) |
| `POST` | `/saml/acs` | Recibe la aserción del IdP y abre la sesión (404 sin configurar) |

### Cuenta del chatbox

| Método | Ruta | Uso |
|---|---|---|
| `POST` | `/api/contact/register` | Alta de cliente; deja la cookie de sesión de una vez |
| `POST` | `/api/contact/login` | Entrada del cliente registrado |
| `POST` | `/api/contact/logout` | Cierre y revocación de la sesión |
| `GET` | `/api/contact/me` | Identidad del cliente autenticado |
| `POST` | `/api/contact/uploads` | Sube una imagen para adjuntarla al siguiente mensaje |

### Bandejas y conversación

| Método | Ruta | Uso |
|---|---|---|
| `GET` | `/api/conversations?scope=` | `unassigned`, `mine`, `mine_or_unassigned`, `all` |
| `GET` | `/api/inbox/summary` | Contadores de las pestañas |
| `GET` | `/api/conversations/{id}/messages` | Historial completo del hilo |
| `POST` | `/api/conversations/{id}/reply` | Respuesta al cliente por su canal |
| `POST` | `/api/conversations/{id}/control` | Alterna entre `bot` y `human` |
| `POST` | `/api/conversations/{id}/close` · `/reopen` | Cierre y reapertura |

### Trabajo en equipo

| Método | Ruta | Uso |
|---|---|---|
| `POST` | `/api/conversations/{id}/claim` | Tomar de la cola común |
| `POST` | `/api/conversations/{id}/transfer` | Derivar a un compañero o a un departamento, con motivo |
| `POST` | `/api/conversations/{id}/release` | Devolver a la cola común |
| `GET` | `/api/conversations/{id}/assignments` | Traza de derivaciones |
| `GET`/`POST` | `/api/conversations/{id}/notes` | Notas internas del equipo |
| `GET` | `/api/conversations/{id}/contact` | Ficha del contacto y su historial de comentarios |
| `PATCH` | `/api/conversations/{id}/contact` | Edita nombre, teléfono o correo (solo supervisión/administración) |
| `POST` | `/api/conversations/{id}/contact/comments` | Añade un comentario (solo supervisión/administración) |
| `GET` | `/api/contacts` | Directorio de clientes, con búsqueda (solo supervisión/administración) |
| `GET` | `/api/contacts/{id}` | Ficha completa: datos, comentarios y todas sus conversaciones (solo supervisión/administración) |
| `PATCH` | `/api/contacts/{id}` | Edita nombre, teléfono o correo desde el directorio (solo supervisión/administración) |
| `POST` | `/api/contacts/{id}/comments` | Añade un comentario desde el directorio (solo supervisión/administración) |
| `POST` | `/api/uploads` | Sube una imagen para adjuntarla a la próxima respuesta |
| `GET` | `/api/agents` | Directorio del equipo |
| `POST` | `/api/agents` | Alta de agente, rol y departamento principal (solo administración) |
| `POST` | `/api/agents/{id}/password` | Cambio de contraseña (solo administración) |
| `PATCH` | `/api/agents/{id}` | Cambia el nombre visible o reactiva la cuenta (solo administración) |
| `DELETE` | `/api/agents/{id}` | Desactiva la cuenta, sin borrar la fila (solo administración) |
| `GET` | `/api/departments` | Lista de departamentos |
| `POST` | `/api/departments` | Crea un departamento (solo administración) |
| `PUT` | `/api/agents/{id}/departments` | Fija el principal y los adicionales de una persona (solo administración) |
| `GET` | `/api/channel-accounts` | Lista de cuentas de canal (solo administración) |
| `POST` | `/api/channel-accounts` | Conecta una cuenta de WhatsApp, Facebook o Teams (solo administración) |
| `PATCH` | `/api/channel-accounts/{id}` | Cambia nombre, departamento, estado o token propio (solo administración) |
| `GET`/`PUT` | `/api/admin/settings` | Texto de la respuesta automática (solo administración para editar) |
| `GET` | `/api/supervisor/overview` | Carga por agente y derivaciones (supervisión) |
| `GET` | `/api/stats` | Mensajes por canal |

Todas las rutas de consola exigen sesión de agente, en todo entorno —también
en `dev`—. `ADMIN_API_KEY`, si está configurada, actúa como credencial de
servicio con alcance de supervisión y es la única alternativa a la sesión:
sirve para el arranque —cuando aún no existe ningún agente— y para
integraciones.

---

## 12. Consideraciones antes de producción

- **TLS.** Sobre HTTP, tanto la contraseña del agente como la cookie de sesión
  viajan en claro por la red local. Sitúe el servicio detrás de un proxy inverso
  con certificado —basta uno interno— y ponga `SESSION_COOKIE_SECURE=true`.
- **Secretos.** `.env` sirve para desarrollo. En producción, inyecte las
  credenciales desde un gestor de secretos; el código nunca las registra.
- **Escala horizontal.** El bus de [hub.py](app/core/hub.py) reside en memoria y
  solo alcanza los WebSockets del propio proceso: con varias réplicas, un agente
  conectado a la réplica B no recibiría el aviso de una derivación hecha en la
  réplica A. Sustitúyalo por Redis Pub/Sub o por `LISTEN`/`NOTIFY` de PostgreSQL
  conservando la misma interfaz. La cola de salida ya es segura entre procesos.
- **Retención.** El mantenimiento purga las claves de idempotencia a los tres
  días. Defina además su política para `messages` y `raw`, que conservan datos
  personales, y para `audit_log`.
- **Copias de seguridad.** Todo el historial del que depende una derivación vive
  en PostgreSQL. Programe `pg_dump` y compruebe la restauración.
- **Observabilidad.** El registro sale en JSON por la salida estándar. Los
  contadores de `ai_runs`, `message_events` y `assignments` alimentan los paneles
  de coste, entregabilidad y reparto de carga.
- **Imágenes.** `/uploads` se sirve sin autenticación propia, igual que `/static`:
  el nombre aleatorio del fichero hace de control de acceso, como ya ocurre con
  los enlaces de medios de WhatsApp o de Teams. Copias de seguridad y retención
  deben cubrir también `UPLOADS_DIR` (volumen `uploads-data` en Docker Compose),
  no solo la base de datos.
