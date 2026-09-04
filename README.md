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
│   ├── secrets.py          Cifrado reversible de credenciales de terceros
│   ├── business_hours.py   Horario de atención y objetivo de primera respuesta
│   ├── branding.py         Color de marca y derivación de la paleta accesible
│   ├── localized.py        Textos con una versión por idioma
│   ├── mailer.py           Correo de invitación a las cuentas nuevas
│   ├── google_oauth.py     Inicio de sesión con Google
│   ├── saml.py             Inicio de sesión único (SAML 2.0)
│   └── hub.py              Bus de difusión para WebSockets
├── channels/               WhatsApp, Microsoft Bot Framework, chatbox web
├── handlers/               Aforo, control humano, comandos, IA, red de seguridad
├── db/
│   ├── models.py           Esquema relacional (24 tablas)
│   ├── repositories.py     Todas las sentencias SQL del dominio
│   └── engine.py           Motor y unidad de trabajo asíncrona
├── api/
│   ├── webhooks.py         Entrada de los canales externos
│   ├── auth.py             Inicio y cierre de sesión de los agentes
│   ├── session.py          Login unificado de «/»: agente o cliente
│   ├── contact_auth.py     Cuentas del chatbox público
│   ├── google_auth.py      Entrada con cuenta de Google
│   ├── saml.py             Entrada por SAML 2.0
│   ├── console.py          Bandejas, derivación, notas, administración y supervisión
│   ├── attachments.py      Descarga de adjuntos, comprobando el acceso al hilo
│   ├── deps.py             Identidad de la petición y permisos
│   └── ws.py               WebSockets del chatbox y de la consola
└── web/                    Chatbox y consola de equipo (HTML, CSS, JS)
    ├── static/i18n.js      Español, inglés y alemán
    └── static/theme.js     Tema claro, oscuro o el del sistema

db/
├── migrations/0001_init.sql       Esquema completo, generado desde los modelos
├── migrations/0002…0012_*.sql     Añadidos incrementales sobre una base existente
└── alembic/                       Entorno de migraciones

scripts/
├── init_db.py              Crea el esquema en desarrollo
├── create_agent.py         Da de alta agentes y supervisores
├── generate_ddl.py         Regenera el DDL desde los modelos
├── generate_tls_cert.ps1   Certificado interno para el proxy inverso
├── backup_db.ps1           Volcado de la base con retención
└── restore_db.ps1          Restauración de un volcado

docker/
├── certs/                  Raíz corporativa añadida a la confianza del contenedor
└── nginx/
    ├── chatbox.conf        Proxy inverso con TLS (perfil `tls` de Compose)
    └── certs/              Certificado y clave del proxy (fuera del repositorio)

tests/                      328 pruebas sobre SQLite, sin servicios externos
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

**`SECRET_ENCRYPTION_KEY` conviene fijarla desde el principio.** Es la clave con
la que se cifran las credenciales que se guardan por cuenta de canal —el token
de un número de WhatsApp, el de una página de Facebook— y sin ella la consola no
puede guardarlas: el formulario «Cuentas de canal» rechaza el token y no queda
más remedio que editar el `.env` a mano por cada canal. Se genera una vez:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Y **no se cambia después**: los tokens ya guardados se cifraron con la anterior
y dejarían de poder leerse.

`WHATSAPP_WABA_ID` solo hace falta para iniciar conversaciones salientes desde
la consola; es el identificador de la cuenta de WhatsApp Business a la que
pertenece el número, y de ahí se leen las plantillas aprobadas.

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

**Cuenta de administrador por defecto**, para entrar de una vez sin esperar
el alta del equipo real:

```
Correo:      admin@local
Contraseña:  Admin1234
```

Cámbiela (o bórrela) antes de exponer el servicio fuera de la red local — es
una contraseña conocida, pensada para arrancar rápido en desarrollo, no para
producción. Se cambia desde la consola, en «Administrar usuarios», o con
`python scripts/create_agent.py --email admin@local --restablecer`.

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

### Paso 5 — TLS con nginx

Sobre HTTP la contraseña del agente y la cookie de sesión viajan legibles por
la red local. El perfil `tls` de Compose añade un **nginx** delante que termina
el TLS y deja la aplicación donde estaba: sigue hablando HTTP en claro por el
8000, solo que ahora dentro de la red de Docker.

```
   navegador ──HTTPS 443──▶ nginx ──HTTP 8000──▶ api ──▶ postgres
                            │
                            └── certificado, WebSockets, límite de subida
```

**1. Certificado.** Uno interno basta:

```bash
powershell -File scripts/generate_tls_cert.ps1 -Hostname chat.empresa.local
```

Escribe el par en `docker/nginx/certs/` — la clave privada nunca llega al
repositorio. Un certificado autofirmado cifra igual que cualquier otro, pero no
acredita la identidad del servidor: el navegador avisará hasta que se instale
ese mismo `chatbox.crt` como raíz de confianza en los equipos, o hasta que lo
sustituya por uno de su propia autoridad interna con el mismo nombre de
fichero.

**2. Configuración.** En `.env`, la dirección pública deja de llevar puerto:

```
PUBLIC_BASE_URL=https://chat.empresa.local
CORS_ALLOW_ORIGINS=https://chat.empresa.local
SESSION_COOKIE_SECURE=true
FORWARDED_ALLOW_IPS=*
```

`FORWARDED_ALLOW_IPS` no es opcional aquí, y su omisión falla de forma poco
evidente: `--proxy-headers` de uvicorn solo confía en `127.0.0.1`, de modo que
un proxy en otro contenedor queda descartado en silencio. El servicio vería
entonces `http` donde el navegador usó `https`, y el inicio de sesión único
—que compara el destino de la aserción con la URL calculada, en modo
estricto— rechazaría cada intento. Deje `*` solo si el 8000 ya no está
publicado a la red; en cuanto lo esté, indique la dirección concreta del proxy.

**3. Arranque.**

```bash
docker compose --profile tls up -d --build
```

Dos cosas tienen que estar en el disco del servidor antes de ese comando, y
cada una falla de una forma distinta si no está:

| Falta | Síntoma |
|---|---|
| `docker/nginx/chatbox.conf` | `not a directory: Are you trying to mount a directory onto a file` — Docker creó una carpeta con ese nombre en lugar del fichero. Bórrela y copie el fichero. |
| `docker/nginx/certs/chatbox.crt` y `.key` | El contenedor arranca y se detiene solo; `docker compose logs nginx` muestra `cannot load certificate`. Genere el par en el servidor. |

El certificado y su clave no viajan en el repositorio a propósito (ver
[.gitignore](.gitignore)): en cada servidor se generan una vez.

Sin `--profile tls` nada cambia: el proxy no existe y el servicio sigue
atendiendo por HTTP en el 8000, como en el Paso 3. Con el proxy en marcha,
conviene además dejar de publicar el 8000 a toda la red —cambiando
`"8000:8000"` por `"127.0.0.1:8000:8000"` en
[docker-compose.yml](docker-compose.yml)— para que nadie entre por HTTP
saltándose el certificado.

**4. Comprobación.**

```bash
curl -kI https://chat.empresa.local/health
```

Y abra la consola: si la cola común se actualiza sin recargar la página, el
WebSocket atraviesa el proxy correctamente. Ese es el punto que más veces se
rompe con un proxy inverso escrito a mano, porque `/ws/chat` y `/ws/inbox`
exigen el cambio de protocolo (`Upgrade`) y un plazo de lectura largo; ambos
están resueltos en [docker/nginx/chatbox.conf](docker/nginx/chatbox.conf), que
además eleva `client_max_body_size` a 10 MiB para que las subidas de hasta
`UPLOAD_MAX_BYTES` no las corte el proxy antes de llegar a la aplicación.

| Quién | Dirección |
|---|---|
| Cualquiera (clientes, agentes, supervisión) | `https://chat.empresa.local/` |
| Documentación de la API | `https://chat.empresa.local/docs` |

Los webhooks de WhatsApp y Facebook siguen necesitando una dirección **pública**
con un certificado de una autoridad reconocida: Meta no acepta un certificado
autofirmado ni alcanza una IP privada. Este paso resuelve el acceso del equipo,
no la recepción de esos dos canales.

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

Además del formulario están los botones **«Iniciar sesión con SSO»** y
**«Iniciar sesión con Google»**, cada uno visible solo cuando su proveedor está
configurado (ver «Inicio de sesión único» en la sección 6). El de Google sigue
las normas de marca del propio Google —logotipo, colores y rótulo oficial en
cada idioma—, porque el valor de ese botón está justamente en que se reconozca
igual en todas partes. Los dos son exclusivos del equipo, así que
siempre entregan en `/console`. Quien entra por SSO por primera vez con un
correo que todavía no tiene cuenta se da de alta automáticamente como agente
—nunca con más permisos, aunque el proveedor lo marque como alguien
importante—; quien ya tenía cuenta conserva su rol tal cual estaba.

Tres pestañas, con su contador en vivo:

* **Cola común** — todo lo que entra sin responsable. El botón «Atender yo» lo
  asigna a quien pulsa y silencia al asistente automático.
* **Mis chats** — la cartera propia.
* **Todos** — solo aparece para supervisión.

Dentro de cada pestaña, los filtros acotan por **estado**, canal, departamento
y etiqueta. Una conversación pasa por tres estados: `open` (pendiente),
`in_progress` (en proceso) y `closed` (solucionada). El estado intermedio
existe para distinguir lo que alguien ya está atendiendo de lo que todavía no
ha mirado nadie; sin él, una bandeja llena no dice cuánto trabajo queda de
verdad.

Cualquier combinación de filtros se guarda como **vista** con el botón
«+ Guardar vista». Una vista puede ser propia o compartida con todo el equipo
—«Sin responsable en Ventas», «Etiquetadas urgente»—; las compartidas las ve
todo el mundo y solo las borra quien las creó o administración.

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

Sobre el hilo se aplican **etiquetas** —con su color, definidas por
administración— que además sirven de filtro en la bandeja.

El compositor admite adjuntar una imagen (📎), tanto en el chatbox del cliente
como en la respuesta del agente; se sube a `/uploads` y se muestra como burbuja
de imagen en ambos lados.

**Lo que llega de fuera es más variado, y se guarda igual.** WhatsApp no manda
el fichero en el webhook, solo un identificador con el que hay que ir a
buscarlo, autenticado; y lo que hay al otro lado desaparece a los pocos días.
Por eso el adjunto se descarga en el momento de recibirlo y se guarda aquí:
esperar a que alguien abra la conversación deja media bandeja sin ficheros. En
el hilo, la imagen se ve, el vídeo y el audio se reproducen con sus controles, y
lo que no se puede previsualizar —un PDF— se ofrece como enlace con su nombre.

Los límites son distintos a propósito: al **subir** se admiten solo imágenes
(`UPLOAD_MAX_BYTES`, 8 MB), porque es lo único que la interfaz sabe componer;
al **recibir** se admite además vídeo, audio y PDF, con un tope propio
(`INBOUND_MEDIA_MAX_BYTES`, 16 MB) que cubre el máximo que permite WhatsApp.
Nadie elige lo que le mandan, y un vídeo descartado por un límite ajeno se
pierde para siempre.

Para salir, el camino es el inverso: el fichero se sube antes a Meta y se manda
su identificador. Mandarlo como enlace no funciona —la dirección guardada es
relativa, y aunque se compusiera la pública entera, `/uploads` exige sesión y
quien la descargaría es un servidor de Meta, sin ninguna.

Escribiendo `/` en el compositor aparecen las **respuestas guardadas**: textos
frecuentes con un atajo (`/saludo`, `/horario`) que se insertan sin salir del
teclado. Son texto, y quien responde puede retocarlo antes de enviar.

Las **macros** van un paso más allá: en vez de un texto, una secuencia de pasos
que se aplica de un clic —responder con un texto, poner una etiqueta, derivar a
un departamento y cerrar—. Sirven para lo que se repite igual muchas veces al
día; lo que cambia caso por caso se sigue haciendo a mano.

### Escribir primero

El botón **«+ Nueva conversación»** abre un hilo de WhatsApp con alguien que
todavía no ha escrito. No tiene campo de texto libre, y no por omisión: WhatsApp
solo admite texto libre hacia quien haya escrito en las últimas 24 horas, y
fuera de esa ventana exige una **plantilla aprobada**. El diálogo pide el número
y la plantilla, rellena sus huecos y enseña el texto final tal como lo recibirá
la persona —el nombre técnico de una plantilla no dice nada de lo que contiene—.

El servidor comprueba la plantilla contra la lista real de Meta antes de
encolar: que exista, que sea de ese idioma y que reciba tantos datos como
declara. Sin esa comprobación, la Graph API rechaza el envío con un error opaco
y el mensaje muere en la cola sin que nadie entienda por qué.

El hilo que se abre es el mismo que si hubiera escrito el cliente: el
identificador de la conversación es el número, de modo que una respuesta
posterior cae en ese hilo y no en uno nuevo. En cuanto la persona conteste, se
le responde con texto normal como en cualquier otra conversación.

### Horario de atención y primera respuesta

Fuera del horario configurado el asistente deja de responder: el mensaje del
cliente se recibe igual y queda en la cola, y —si se ha escrito uno— se le envía
un aviso de que está fuera de horario. El horario se fija por departamento, y
hay uno para toda la empresa que rige la cola común y todo departamento que no
fije el suyo. Admite turno partido.

Sobre ese horario se mide el **objetivo de primera respuesta**: los minutos que
el equipo se da para que una persona —no el asistente— conteste por primera vez.
El reloj se detiene de noche y los fines de semana, porque lo que se mide es
cuánto tardó el equipo en atender, no cuánto tiempo pasó. Las conversaciones que
se pasan del objetivo quedan marcadas como vencidas.

Por debajo de 1180 px de ancho el panel derecho pasa a ser un cajón que se abre
con el botón «Equipo»: en pantallas estrechas sigue siendo alcanzable en lugar de
desaparecer.

El **panel de supervisión** muestra la carga abierta por agente, la cola común
como fila aparte, los mensajes por canal y las últimas derivaciones con su motivo.

El **panel de administración** («Administrar usuarios», solo para `admin`) se
reparte en cinco pestañas, porque en una sola columna había que desplazarse
mucho para encontrar cada cosa:

* **Usuarios** — alta de cuentas, departamentos y la tabla «Usuarios registrados».
* **Canales** — las cuentas de canal (ver más abajo).
* **Etiquetas** — etiquetas, respuestas guardadas y macros.
* **Saludos y mensajes** — la respuesta automática del asistente y el horario
  de atención con su objetivo de primera respuesta.
* **Apariencia** — el color de la marca.

En **Apariencia** se elige **un** color, el de la marca, y de ahí sale el resto:
los botones, los enlaces y las burbujas, en tema claro y en oscuro. No se pide
un color por cada uso porque nadie acierta a rellenar ese formulario y basta una
combinación desafortunada para dejar un botón ilegible. De cada color elegido se
derivan tres valores por tema —el relleno, el texto que va encima y la versión
que se lee sobre el fondo— con los umbrales de contraste de la WCAG 2.1, de modo
que un amarillo de marca da botón amarillo con texto negro y enlaces en oliva
oscuro, en vez de enlaces amarillos invisibles sobre blanco. La vista previa
enseña los dos temas a la vez antes de guardar, y el color viaja incrustado en
la propia página para que no se vea un destello del azul de partida al cargar.
Esa tabla concentra toda la configuración por persona en una sola fila —nombre,
departamento principal, departamentos adicionales y una nueva contraseña
opcional— que se guarda de una vez con un solo botón «Guardar»; dejar la
contraseña en blanco la conserva sin cambios.

Cada fila ofrece dos formas de dar de baja, y no son intercambiables:

* **«Desactivar»** no borra la fila. La cuenta deja de poder iniciar sesión,
  pero el nombre de la persona sigue apareciendo en el historial de
  derivaciones, notas y auditoría. Es lo que corresponde cuando alguien deja el
  equipo: lo que hizo mientras estuvo debe poder leerse.
* **«Eliminar»** borra la cuenta de verdad, y entonces **el historial pierde el
  nombre**: las derivaciones, notas y mensajes siguen ahí, pero sin autor. Es
  para lo que desactivar no resuelve —una cuenta creada por error, o alguien que
  ejerce su derecho a que le borren los datos—. No tiene vuelta atrás, así que
  el propio botón pregunta antes: el primer clic lo arma y el segundo borra, y
  la pregunta se retira sola a los cinco segundos.

Ninguna de las dos admite la propia cuenta ni al último administrador activo que
quede: cualquiera de las dos cosas dejaría la instalación sin quien la
administre.

La sección **«Cuentas de canal»** conecta tantos números de WhatsApp, páginas
de Facebook o equipos de Teams como se necesite, cada uno con su propio
departamento: una conversación nueva de esa cuenta cae directo en esa cola,
sin que nadie tenga que derivarla a mano. Una cuenta sin departamento sigue
en la cola común, igual que siempre. Ver «Varias cuentas por canal» en la
sección 6 para el detalle de credenciales por canal.

### Idioma y tema

La interfaz está en **español, inglés y alemán**, y la elección se recuerda en
el navegador de cada persona; no es un dato del negocio y no tiene por qué
viajar a la base. Las dos pantallas eligen idioma de partida de forma distinta,
y a propósito: la consola arranca en español porque el equipo trabaja en un
idioma conocido, mientras que el chatbox adopta el del navegador del visitante
—a quien escribe desde fuera nadie le configuró nada, y encontrarse la ventana
en un idioma ajeno es una barrera—. En ambos casos, una elección hecha a mano
manda sobre todo lo demás.

Junto al selector de idioma, un botón cicla entre **🌓 automático, ☀️ claro y
🌙 oscuro**. «Automático» sigue al sistema operativo; las otras dos se imponen
sobre él. El tema se aplica antes del primer pintado —el script va en la
cabecera, sin `defer`— porque esperar al documento dejaría ver un destello claro
antes de pintar el oscuro cada vez que se abre la página.

Las novedades llegan por WebSocket: cuando alguien le deriva una conversación,
aparece en su bandeja sin recargar la página. Los enlaces al CSS y al JavaScript
llevan una huella derivada de la fecha de modificación de los ficheros, de modo
que tras una actualización nadie se queda con una versión antigua en caché.

---

## 6. Configuración de los canales

### Varias cuentas por canal, cada una con su departamento

WhatsApp, Facebook y Teams admiten **tantas cuentas como se quiera** —varios
números, varias páginas, varios equipos—, cada una conectada desde «Cuentas
de canal» en el panel de administración. Esa pantalla es el camino previsto:
conectar un canal más no debería exigir editar el `.env` ni reiniciar nada. Lo
que queda en `.env` es solo la credencial global de reserva, para el caso de un
único token de sistema compartido por varios números; una cuenta con token
propio no lo usa. Y hay una razón práctica para preferir la pantalla: las
variables del `.env` las lee Docker Compose al crear el contenedor, así que un
cambio ahí no surte efecto hasta reiniciar —y con el reinicio se cae el
servicio y se cortan los WebSocket de todo el equipo—, mientras que lo guardado
desde la consola rige desde el siguiente mensaje. Con departamento asignado, una
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

Cambiarla vuelve ilegibles las credenciales ya guardadas —no las claves
globales de `.env`, que no pasan por aquí—, así que consérvela en un lugar
seguro.

Cada cuenta de Meta guarda **los tres valores** que hacen falta para funcionar
sola, y no solo el token de acceso:

| Campo | Para qué |
|---|---|
| Token de acceso | Enviar mensajes por esa cuenta |
| Token de verificación del webhook | Responder al reto de alta que manda Meta |
| Clave secreta de la app | Comprobar la firma de cada webhook entrante |

El que se deje en blanco se resuelve con el valor global de `.env`. Es lo que
permite tener números en **aplicaciones de Meta distintas**, cada una con su
secreto, sin editar el fichero ni reiniciar: con un solo número y una sola
aplicación basta con lo global, como hasta ahora.

Los dos últimos se comprueban de una forma que conviene entender: la firma
llega **antes** de poder leer el cuerpo, así que en ese momento todavía no se
sabe de qué número es el mensaje. Se acepta el que valide con cualquiera de los
secretos dados de alta —todos los configuró quien administra, de modo que no se
rebaja la confianza— y se comparan todos en tiempo constante. Una cuenta
desactivada deja de contar de inmediato.

Sobre si el token de acceso es obligatorio, los canales difieren:

* **WhatsApp**: opcional. Si falta, se usa el `WHATSAPP_ACCESS_TOKEN` global
  de más abajo — cubre el caso común de un solo token de sistema para varios
  números de la misma cuenta de WhatsApp Business.
* **Facebook**: obligatorio. Cada página tiene su propio token; no hay uno
  "por defecto" razonable.
* **Teams**: no aplica, y por eso los tres campos se ocultan al elegirlo. Un
  solo bot de Azure ya puede estar en muchos equipos, y su autenticación es un
  JWT firmado por Microsoft, no un secreto compartido.

Una salvedad: los tres valores se escriben **al conectar la cuenta**. Desde la
tabla se puede sustituir el token de acceso; para cambiar el de verificación o
la clave secreta hace falta la API (`PATCH /api/channel-accounts/{id}`), que
acepta los tres por separado.

### WhatsApp Cloud API

1. Cree una aplicación en Meta for Developers y añada el producto WhatsApp.
2. Registre el webhook en `https://SU_DOMINIO/webhooks/whatsapp` con el valor de
   `WHATSAPP_VERIFY_TOKEN` y suscríbase al campo `messages`.
3. **Suscriba la cuenta de WhatsApp Business (WABA) a su aplicación.** Es un
   paso aparte del anterior y el que más se pasa por alto: Meta entrega los
   webhooks a la aplicación que la WABA tenga suscrita, de modo que con la URL
   bien puesta pero sin este paso no llega absolutamente nada —ni mensajes ni
   acuses—, y la configuración de la aplicación se ve impecable. Con los
   números de prueba viene suscrita la aplicación interna de Meta, no la suya.
   Se comprueba y se corrige así:

   ```bash
   TOKEN="$WHATSAPP_ACCESS_TOKEN"; WABA="$WHATSAPP_WABA_ID"
   # Ver qué aplicaciones tiene suscritas:
   curl -H "Authorization: Bearer $TOKEN" "https://graph.facebook.com/v21.0/$WABA/subscribed_apps"
   # Suscribir la suya:
   curl -X POST -H "Authorization: Bearer $TOKEN" "https://graph.facebook.com/v21.0/$WABA/subscribed_apps"
   ```

4. Complete `WHATSAPP_APP_SECRET`, `WHATSAPP_ACCESS_TOKEN` y
   `WHATSAPP_PHONE_NUMBER_ID`. `WHATSAPP_WABA_ID` solo hace falta para iniciar
   conversaciones salientes desde la consola.

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
3. Complete `FACEBOOK_VERIFY_TOKEN` y `FACEBOOK_APP_SECRET` en `.env`. Son las
   dos únicas variables de Facebook que tiene sentido poner ahí: valen para
   toda la aplicación de Meta. Si Messenger vive en la misma aplicación que
   WhatsApp, el secreto es **el mismo valor** que `WHATSAPP_APP_SECRET` —una
   aplicación de Meta tiene un solo secreto—.
4. Conecte cada página desde «Cuentas de canal». Ahí el **token de la página**
   es obligatorio: no existe un token global razonable, y sin él se reciben
   mensajes pero no se puede contestar. Los otros dos también pueden ir por
   cuenta, y entonces `.env` puede quedar vacío: sirve para tener páginas en
   aplicaciones de Meta distintas.

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
  `powershell -File scripts\register_backup_task.ps1 -IntervalMinutes 10` —
  crea la tarea «ChatboxDbBackup» del Programador de tareas de Windows, cada
  10 minutos (o `-IntervalHours` si prefiere esa unidad; sin ninguno de los
  dos, cada 4 horas), reteniendo 14 días de respaldos por defecto
  (`-RetentionDays`). Corre con la cuenta con dominio del usuario actual
  (`Interactive`): sin eso, la tarea no alcanza el Docker Desktop de la
  sesión y falla en silencio.
- **Respaldo manual**: `powershell -File scripts\backup_db.ps1`.
- **Restaurar a mano**: `powershell -File scripts\restore_db.ps1 -Latest` (o
  `-BackupFile <nombre.sql>` para uno puntual). Pide confirmación porque
  sobrescribe la base actual; `-Force` la omite.
- **Recuperación automática al arrancar**: `db/migrations/9999_restore_latest_backup.sh`
  corre solo, como el resto de `db/migrations/`, pero **solo** cuando el
  volumen de datos está vacío —es decir, exactamente cuando ya no queda nada
  que recuperar—. Si hay un respaldo en `backups/`, lo restaura antes de que
  el contenedor termine de arrancar, sin que nadie tenga que intervenir; si
  no hay ninguno, deja el esquema recién creado tal cual. Verificado en vivo
  borrando el volumen a propósito y levantando la pila de nuevo: la cuenta y
  los datos volvieron solos, sin ejecutar `restore_db.ps1` a mano.
- **Quitar la tarea**: `Unregister-ScheduledTask -TaskName ChatboxDbBackup`.

---

## 8. Modelo de datos

Veinticuatro tablas, con clave primaria `uuid` y aislamiento por `tenant_id`:

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
| `labels` | Etiquetas del inquilino, con su color |
| `conversation_labels` | Qué etiquetas lleva cada conversación |
| `canned_responses` | Respuestas guardadas, con su atajo `/comando` |
| `macros` | Secuencias de pasos que se aplican de un clic |
| `saved_views` | Combinaciones de filtros de la bandeja, propias o del equipo |
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

Sobre una instalación que ya tenía el esquema anterior, aplique en orden los
ficheros de `db/migrations/`, del `0002` al `0014`:

| | |
|---|---|
| `0002_teamwork.sql` | Derivaciones, notas internas y auditoría |
| `0003_contact_login.sql` | Cuentas del chatbox público |
| `0004_contact_comments.sql` | Comentarios de supervisión sobre un contacto |
| `0005_departments.sql` | Departamentos y pertenencia de los agentes |
| `0006_channel_accounts_department_and_secrets.sql` | Departamento y credenciales por cuenta de canal |
| `0007_labels_and_canned_responses.sql` | Etiquetas y respuestas guardadas |
| `0008_saved_views.sql` | Vistas guardadas de la bandeja |
| `0009_business_hours.sql` | Horario de atención por departamento |
| `0010_first_response_sla.sql` | Objetivo de primera respuesta y su reloj |
| `0011_macros.sql` | Macros de varios pasos |
| `0012_conversation_in_progress.sql` | El estado «en proceso» |
| `0013_hotel_module.sql` | Módulo de reservas de hotel por departamento |
| `0014_department_logo.sql` | Logo por departamento |

Lo que se guarda en `Tenant.settings` —la respuesta automática, el horario de
toda la empresa, el color de marca— no lleva migración: es una columna `JSONB` y
crece sin tocar el esquema.

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
python -m pytest tests/ -q      # 328 pruebas
python -m ruff check .
```

La batería corre sobre SQLite mediante `aiosqlite`, sin PostgreSQL ni acceso a
red. Cubre la normalización de ambos proveedores, la validación de firma y de
JWT, la composición de las cargas de salida, la idempotencia, la unificación de
contactos, los reintentos de la cola, el descarte a cola muerta, el contrato
HTTP completo y, en [test_teamwork.py](tests/test_teamwork.py), todo el trabajo
en equipo: credenciales, alcance por rol, derivación con historial intacto,
notas internas y panel de supervisión.

También cubre lo que no se ve a simple vista y rompe en silencio: que el color
de marca elegido siga siendo legible sea cual sea —con amarillos, blancos y
grises medios entre los casos de prueba, comprobando los umbrales de contraste
de la WCAG—, que una plantilla de WhatsApp no se encole si no coincide con las
aprobadas, y que borrar una cuenta deje el historial en pie aunque sin nombre.

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
| `GET` | `/api/auth/sso` | Si hay que mostrar los botones de SSO y de Google |
| `POST` | `/api/auth/presence` | Disponibilidad: `available`, `away`, `offline` |
| `GET` | `/saml/metadata` | Metadatos del SP, para darlos de alta en el IdP (404 sin configurar) |
| `GET` | `/saml/login` | Arranca el inicio de sesión único, redirige al IdP (404 sin configurar) |
| `POST` | `/saml/acs` | Recibe la aserción del IdP y abre la sesión (404 sin configurar) |
| `GET` | `/auth/google/login` | Arranca la entrada con Google (404 sin configurar) |
| `GET` | `/auth/google/callback` | Recibe el código de Google y abre la sesión |

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
| `GET` | `/api/conversations` | Bandeja; `?scope=` acepta `unassigned`, `mine`, `mine_or_unassigned`, `all` |
| `GET` | `/api/inbox/summary` | Contadores de las pestañas |
| `GET` | `/api/conversations/{id}/messages` | Historial completo del hilo |
| `POST` | `/api/conversations/{id}/reply` | Respuesta al cliente por su canal |
| `POST` | `/api/conversations/{id}/control` | Alterna entre `bot` y `human` |
| `POST` | `/api/conversations/{id}/close` | Cierra el hilo |
| `POST` | `/api/conversations/{id}/reopen` | Reabre un hilo cerrado |
| `POST` | `/api/conversations/{id}/state` | `open`, `in_progress` o `closed` |
| `POST` | `/api/conversations/start` | Abre un hilo de WhatsApp con una plantilla aprobada |
| `GET` | `/api/whatsapp/templates` | Plantillas aprobadas de la cuenta (503 sin configurar) |

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
| `GET` | `/uploads/{inquilino}/{fichero}` | Descarga un adjunto, comprobando el acceso al hilo |
| `PUT` | `/api/conversations/{id}/labels` | Fija las etiquetas del hilo |
| `POST` | `/api/conversations/{id}/macros/{macro_id}` | Aplica una macro al hilo |
| `GET` | `/api/agents` | Directorio del equipo |
| `POST` | `/api/agents` | Alta de agente, rol y departamento principal (solo administración) |
| `POST` | `/api/agents/{id}/password` | Cambio de contraseña (solo administración) |
| `PATCH` | `/api/agents/{id}` | Cambia el nombre visible o reactiva la cuenta (solo administración) |
| `DELETE` | `/api/agents/{id}` | Desactiva la cuenta, sin borrar la fila (solo administración) |
| `DELETE` | `/api/agents/{id}/permanently` | Borra la cuenta; el historial queda sin nombre (solo administración) |
| `GET` | `/api/departments` | Lista de departamentos |
| `POST` | `/api/departments` | Crea un departamento (solo administración) |
| `PUT` | `/api/agents/{id}/departments` | Fija el principal y los adicionales de una persona (solo administración) |
| `PUT` | `/api/departments/{id}/business-hours` | Horario y objetivo del departamento (solo administración) |
| `GET` | `/api/channel-accounts` | Lista de cuentas de canal (solo administración) |
| `POST` | `/api/channel-accounts` | Conecta una cuenta de WhatsApp, Facebook o Teams (solo administración) |
| `PATCH` | `/api/channel-accounts/{id}` | Cambia nombre, departamento, estado o token propio (solo administración) |
| `DELETE` | `/api/channel-accounts/{id}` | Desconecta la cuenta; sus conversaciones quedan (solo administración) |
| `GET`/`PUT` | `/api/admin/settings` | Texto de la respuesta automática (solo administración para editar) |
| `GET`/`PUT` | `/api/admin/service-defaults` | Horario y objetivo de toda la empresa y de la cola común |
| `GET`/`PUT` | `/api/admin/branding` | Color de marca; devuelve la paleta ya derivada |
| `GET` | `/api/admin/branding/preview` | Deriva la paleta de un color sin guardarlo |
| `GET`/`POST` | `/api/labels` | Etiquetas del inquilino |
| `DELETE` | `/api/labels/{id}` | Borra una etiqueta y la quita de sus conversaciones |
| `GET`/`POST` | `/api/canned-responses` | Respuestas guardadas, con su atajo |
| `PATCH`/`DELETE` | `/api/canned-responses/{id}` | Edita o borra una respuesta guardada |
| `GET`/`POST` | `/api/macros` | Macros de varios pasos |
| `DELETE` | `/api/macros/{id}` | Borra una macro |
| `GET`/`POST` | `/api/saved-views` | Vistas guardadas de la bandeja, propias o del equipo |
| `DELETE` | `/api/saved-views/{id}` | Borra una vista guardada |
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
  viajan en claro por la red local. El perfil `tls` de Compose pone nginx
  delante con certificado —basta uno interno—; ver «Paso 5 — TLS con nginx».
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
- **Imágenes.** `/uploads/{inquilino}/{fichero}` **no** es un montaje estático:
  cada descarga comprueba que quien la pide puede ver la conversación que
  contiene el fichero. Durante un tiempo sí se sirvió como estático, confiando
  en que el nombre aleatorio bastara de control de acceso; no basta, porque la
  URL viaja en la página, en los registros del servidor y en el historial del
  navegador. La ruta se conservó al cambiarlo, de modo que los adjuntos de
  mensajes antiguos siguen funcionando sin migrar nada. Copias de seguridad y
  retención deben cubrir también `UPLOADS_DIR` (volumen `uploads-data` en Docker
  Compose), no solo la base de datos.
