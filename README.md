# Chat de equipo en red local — capa central de orquestación

Servicio que reúne en **un único punto de entrada** todos los chats —WhatsApp,
Microsoft Teams y el chatbox web propio—, los normaliza a un formato interno
común, les aplica lógica de negocio y de IA, y permite **derivarlos entre
compañeros conservando el historial íntegro**.

Cada agente ve la cola común y su propia cartera. La supervisión ve todo. Se
despliega en la red interna: las credenciales son locales y no hace falta un
proveedor de identidad externo.

---

## 1. Cómo trabaja el equipo

```
   WhatsApp ─┐
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

Una conversación nace sin departamento —como siempre— y sigue visible para
cualquiera hasta que alguien la deriva explícitamente a uno; no hay enrutado
automático por canal. A partir de ahí, solo la ve quien atiende ese
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

| Quién | Dirección |
|---|---|
| Clientes (chatbox) | `http://192.168.1.50:8000/` |
| Agentes y supervisión | `http://192.168.1.50:8000/console` |
| Documentación de la API | `http://192.168.1.50:8000/docs` |

---

## 5. La consola de equipo

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

El compositor admite adjuntar una imagen (📎), tanto en el chatbox del cliente
como en la respuesta del agente; se sube a `/uploads` y se muestra como burbuja
de imagen en ambos lados.

Por debajo de 1180 px de ancho el panel derecho pasa a ser un cajón que se abre
con el botón «Equipo»: en pantallas estrechas sigue siendo alcanzable en lugar de
desaparecer.

El **panel de supervisión** muestra la carga abierta por agente, la cola común
como fila aparte, los mensajes por canal y las últimas derivaciones con su motivo.

El **panel de administración** («Administrar usuarios», solo para `admin`)
reúne, además de las cuentas del equipo: el texto de la respuesta automática
del asistente, la creación de departamentos, y el departamento principal más
los adicionales de cada persona. La tabla de usuarios permite editar el nombre
visible y desactivar una cuenta —no se borra la fila: sigue apareciendo en el
historial de derivaciones, notas y auditoría, solo que ya no puede iniciar
sesión— y reactivarla más tarde. No se puede desactivar la propia cuenta ni al
único administrador activo que quede.

Las novedades llegan por WebSocket: cuando alguien le deriva una conversación,
aparece en su bandeja sin recargar la página. Los enlaces al CSS y al JavaScript
llevan una huella derivada de la fecha de modificación de los ficheros, de modo
que tras una actualización nadie se queda con una versión antigua en caché.

---

## 6. Configuración de los canales

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

### Microsoft Bot Framework

1. Cree un recurso *Azure Bot* con *Messaging endpoint*
   `https://SU_DOMINIO/api/messages`.
2. Complete `MICROSOFT_APP_ID` y `MICROSOFT_APP_PASSWORD`; en modo
   `SingleTenant`, también `MICROSOFT_APP_TENANT_ID`.

Cada llamada se valida contra los metadatos OpenID del servicio de canales
—firma, audiencia y emisor—. Las respuestas salen por la Connector API con un
token de Microsoft Entra ID que el adaptador renueva por su cuenta. Frente al
Bot Framework Emulator, ponga `MICROSOFT_VALIDATE_JWT=false`.

### Chatbox web

No requiere configuración y funciona íntegramente dentro de la red local. El
login es obligatorio: quien visita `/` debe registrarse con correo y
contraseña (`POST /api/contact/register`) o iniciar sesión (`POST
/api/contact/login`) antes de poder escribir; la sesión viaja en la cookie
`chatbox_contact_session`, independiente de la cookie de la consola.

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
| `POST` | `/api/messages` | *Activities* del Bot Framework |
| `POST` | `/api/web/messages` | Mensaje del chatbox por REST |
| `POST` | `/webhooks/{canal}` | Ruta genérica para canales nuevos |
| `WS` | `/ws/chat` | Conversación del chatbox web |
| `WS` | `/ws/inbox` | Novedades para la consola del equipo |

### Sesión

| Método | Ruta | Uso |
|---|---|---|
| `POST` | `/api/auth/login` | Entrada del agente; deja la cookie de sesión |
| `POST` | `/api/auth/logout` | Cierre y revocación de la sesión |
| `GET` | `/api/auth/me` | Identidad y rol efectivos |
| `POST` | `/api/auth/presence` | Disponibilidad: `available`, `away`, `offline` |

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
| `POST` | `/api/uploads` | Sube una imagen para adjuntarla a la próxima respuesta |
| `GET` | `/api/agents` | Directorio del equipo |
| `POST` | `/api/agents` | Alta de agente, rol y departamento principal (solo administración) |
| `POST` | `/api/agents/{id}/password` | Cambio de contraseña (solo administración) |
| `PATCH` | `/api/agents/{id}` | Cambia el nombre visible o reactiva la cuenta (solo administración) |
| `DELETE` | `/api/agents/{id}` | Desactiva la cuenta, sin borrar la fila (solo administración) |
| `GET` | `/api/departments` | Lista de departamentos |
| `POST` | `/api/departments` | Crea un departamento (solo administración) |
| `PUT` | `/api/agents/{id}/departments` | Fija el principal y los adicionales de una persona (solo administración) |
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
