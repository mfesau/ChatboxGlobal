/**
 * Traducción de la interfaz: español, inglés y alemán.
 *
 * El español es el idioma de partida y hace de respaldo: si a una clave le
 * falta traducción, se muestra en español antes que dejar el hueco a la vista.
 *
 * El HTML se marca con atributos y esta hoja los recorre:
 *
 *   data-i18n              → reemplaza el texto del elemento
 *   data-i18n-html         → ídem, para lo que lleva marcado propio dentro
 *   data-i18n-placeholder  → el marcador de un campo
 *   data-i18n-title        → el rótulo emergente
 *   data-i18n-aria         → la etiqueta accesible
 *
 * Desde JavaScript se usa `i18n.t("clave")`, que devuelve el texto del idioma
 * activo. Para lo que lleva un dato variable, la traducción admite huecos con
 * llaves: `i18n.t("consola.mensajes", { total: 12 })`.
 *
 * Las dos pantallas eligen idioma de forma distinta, y a propósito. La consola
 * arranca en español porque el equipo trabaja en un idioma conocido y quien
 * quiera otro lo elige una vez. El chatbox llama a `useBrowserLanguage()` y
 * adopta el del visitante: a quien escribe desde fuera nadie le configuró
 * nada, y encontrarse la ventana en un idioma ajeno es una barrera. En ambos
 * casos, una elección hecha a mano manda sobre todo lo demás.
 */
(() => {
  "use strict";

  //: Cada persona elige el suyo y se recuerda en su propio navegador; no es
  //: un dato del negocio ni tiene por qué viajar a la base.
  const STORAGE_KEY = "chatbox.lang";
  const FALLBACK = "es";

  const LANGUAGES = [
    { code: "es", label: "Español" },
    { code: "en", label: "English" },
    { code: "de", label: "Deutsch" },
  ];

  const DICTIONARIES = {
    es: {
      "lang.label": "Idioma",
      "theme.label": "Tema",
      "theme.auto": "Automático",
      "theme.light": "Claro",
      "theme.dark": "Oscuro",

      // --- Cabecera y bandejas ---
      "nav.contacts": "Contactos",
      "nav.logout": "Salir",
      "nav.inboxes": "Bandejas",
      "nav.admin": "Administrar usuarios",
      "nav.supervisor": "Panel de supervisión",
      "tabs.unassigned": "Cola",
      "tabs.mine": "Mis chats",
      "tabs.all": "Todos",
      "role.supervisor": "Supervisión",
      "role.agent": "Agente",

      // --- Filtros ---
      "filter.status": "Filtrar por estado",
      "filter.status.open": "Abiertas",
      "filter.status.closed": "Cerradas",
      "filter.status.all": "Todas",
      "filter.channel": "Filtrar por canal",
      "filter.channel.all": "Todos los canales",
      "filter.department": "Filtrar por departamento",
      "filter.department.all": "Todos los departamentos",
      "filter.label": "Filtrar por etiqueta",
      "filter.label.all": "Todas las etiquetas",

      // --- Vistas guardadas ---
      "views.save": "+ Guardar vista",
      "views.name": "Nombre de la vista",
      "views.shared": "Para todo el equipo",
      "views.saved": "Vista «{name}» guardada.",
      "views.storageFailed": "No se pudo guardar la vista en este navegador.",
      "views.apply": "Aplicar «{name}»",
      "views.applyShared": "Aplicar «{name}» (vista del equipo)",
      "views.remove": "Quitar «{name}»",
      "views.default": "Vista {n}",

      // --- Hilo ---
      "thread.selectOne": "Seleccione una conversación",
      "thread.team": "Equipo",
      "thread.claim": "Atender yo",
      "thread.release": "Devolver a la cola",
      "thread.close": "Cerrar",
      "state.group": "Estado de la conversación",
      "state.pending": "Pendiente",
      "state.inProgress": "En proceso",
      "state.solved": "Solucionado",
      "state.set.pending": "Marcada como pendiente.",
      "state.set.in_progress": "Marcada como en proceso.",
      "state.set.solved": "Marcada como solucionada.",
      "filter.status.unresolved": "Sin resolver",
      "filter.status.pending": "Pendientes",
      "filter.status.inProgress": "En proceso",
      "filter.status.solved": "Solucionadas",
      "thread.empty":
        "La cola común reúne todo lo que entra por WhatsApp, Microsoft Teams y el chatbox web. Tome una conversación o revise las suyas.",
      "thread.listEmptyQueue": "La cola común está vacía. Buen trabajo.",
      "thread.listEmptyFilters": "No hay conversaciones con estos filtros.",
      "thread.noContact": "Contacto sin nombre",
      "thread.messageCount": "{n} mensajes en el historial",
      "thread.loadFailed": "No se pudo cargar el hilo — {error}",

      // --- Distintivos ---
      "badge.queued": "En cola",
      "badge.mine": "Mía",
      "badge.breached": "Vencida",
      "badge.breachedTitle": "Se pasó el objetivo de primera respuesta",
      "badge.slaTitle": "Objetivo de primera respuesta: {time}",
      "sla.dueNow": "vence ya",
      "sla.minutes": "{n} min",
      "sla.hours": "{n} h",
      "sla.days": "{n} d",

      // --- Redacción ---
      "composer.answer": "Respuesta",
      "composer.placeholder":
        "Escriba la respuesta al cliente… (escriba «/» para insertar una respuesta guardada)",
      "composer.attach": "Adjuntar una imagen",
      "composer.send": "Responder",
      "composer.noSelection": "Sin conversación seleccionada",
      "composer.hint": "La respuesta sale por el canal de origen",
      "composer.queued": "Respuesta encolada para envío.",
      "composer.attachReady": "Imagen lista: {name}",
      "composer.attachFailed": "No se pudo subir la imagen — {error}",

      // --- Panel lateral ---
      "aside.transfer": "Derivar a un compañero",
      "aside.transferSection": "Derivación",
      "aside.internalComments": "Comentarios internos",
      "aside.transferHint":
        "La conversación conserva todo su historial; solo cambia el responsable.",
      "aside.colleague": "Compañero",
      "aside.orDepartment": "o departamento (vuelve a la cola, sin dueño)",
      "aside.choose": "— Elegir —",
      "aside.reason": "Motivo (se guarda como nota interna)",
      "aside.reasonPlaceholder": "Contexto para quien la reciba",
      "aside.transferButton": "Derivar",
      "aside.labels": "Etiquetas",
      "aside.labelsHint": "Clasifica la conversación para filtrarla luego.",
      "aside.labelsEmpty": "Todavía no hay etiquetas creadas.",
      "aside.macros": "Macros",
      "aside.macrosHint": "Aplica varias acciones de una sola vez.",
      "aside.notes": "Notas internas",
      "aside.notesHint": "Visibles solo para el equipo. El cliente no las recibe.",
      "aside.notesEmpty": "Todavía no hay notas.",
      "aside.notePlaceholder": "Añadir una nota…",
      "aside.noteSave": "Guardar nota",
      "aside.noteSaved": "Nota interna guardada.",
      "aside.history": "Historial de derivaciones",
      "aside.contact": "Datos del cliente",
      "aside.contactHint": "Editar queda reservado a supervisión y administración.",
      "aside.name": "Nombre",
      "aside.phone": "Teléfono",
      "aside.email": "Correo",
      "aside.save": "Guardar",
      "aside.comments": "Comentarios",
      "aside.commentPlaceholder": "Añadir un comentario…",
      "aside.commentSave": "Guardar comentario",

      // --- Administración ---
      "admin.title": "Administración de usuarios",
      "admin.close": "Cerrar",
      "admin.autoReply": "Respuesta automática",
      "admin.autoReplyHint":
        "Se envía una sola vez, en el primer mensaje que el asistente no logra resolver.",
      "admin.text": "Texto",
      "admin.autoReplySave": "Guardar respuesta automática",
      "admin.createUser": "Crear usuario",
      "admin.displayName": "Nombre visible",
      "admin.role": "Rol",
      "admin.mainDepartment": "Departamento principal",
      "admin.noDepartment": "Sin departamento",
      "admin.initialPassword": "Contraseña inicial",
      "admin.departments": "Departamentos",
      "admin.createDepartment": "Crear departamento",
      "admin.departmentsEmpty": "Todavía no hay departamentos.",
      "admin.departmentCreated": "Departamento creado.",
      "admin.labels": "Etiquetas",
      "admin.color": "Color",
      "admin.createLabel": "Crear etiqueta",
      "admin.labelsEmpty": "Todavía no hay etiquetas.",
      "admin.labelCreated": "Etiqueta creada.",
      "admin.cannedResponses": "Respuestas guardadas",
      "admin.shortcode": "Atajo",
      "admin.title2": "Título",
      "admin.createCanned": "Crear respuesta",
      "admin.cannedEmpty": "Todavía no hay respuestas guardadas.",
      "admin.cannedCreated": "Respuesta guardada creada.",
      "admin.remove": "Eliminar",
      "admin.registeredUsers": "Usuarios registrados",

      // --- Macros ---
      "macro.title": "Macros",
      "macro.hint":
        "Una secuencia de acciones que el equipo aplica de un clic sobre una conversación. Si un paso falla no se aplica ninguno.",
      "macro.addStep": "Añadir paso",
      "macro.addStepButton": "+ Añadir paso",
      "macro.create": "Crear macro",
      "macro.empty": "Todavía no hay macros.",
      "macro.created": "Macro «{name}» creada.",
      "macro.applied": "Macro «{name}» aplicada: {n} pasos.",
      "macro.needsNameAndStep": "La macro necesita un nombre y al menos un paso.",
      "macro.noteNeedsText": "La nota necesita un texto.",
      "macro.createFirst": "Cree primero {what}.",
      "macro.stepCount": "{name} ({n} pasos)",
      "macro.removeStep": "Quitar este paso",
      "macro.action.label": "Etiquetar",
      "macro.action.reply": "Responder con una plantilla",
      "macro.action.note": "Dejar una nota interna",
      "macro.action.transfer": "Derivar a un departamento",
      "macro.action.close": "Cerrar la conversación",
      "macro.short.label": "Etiquetar",
      "macro.short.reply": "Responder",
      "macro.short.note": "Nota interna",
      "macro.short.transfer": "Derivar",
      "macro.short.close": "Cerrar",
      "macro.target.label": "Etiqueta",
      "macro.target.reply": "Plantilla",
      "macro.target.transfer": "Departamento",

      // --- Horario y objetivo ---
      "hours.title": "Horario de atención",
      "hours.hint":
        "Fuera de este horario el asistente deja de responder: el mensaje del cliente se recibe igual y queda en la cola. Un departamento sin horario atiende a toda hora.",
      "hours.department": "Departamento",
      "hours.defaults": "Toda la empresa (y cola común)",
      "hours.defaultsHint":
        "Rige la cola común —lo que todavía no se derivó— y todo departamento que no fije lo suyo.",
      "hours.timezone": "Zona horaria",
      "hours.message": "Aviso al cliente fuera de horario (vacío = no se le avisa)",
      "hours.messagePlaceholder": "Estamos fuera de horario; le respondemos mañana.",
      "hours.day": "Día",
      "hours.opens": "Abre",
      "hours.closes": "Cierra",
      "hours.and": "y",
      "hours.morning": "Mañana",
      "hours.afternoon": "Tarde",
      "hours.splitHint":
        "Deje la tarde vacía si no hace turno partido. Un día sin marcar está cerrado.",
      "hours.slaTarget": "Objetivo de primera respuesta, en minutos (vacío = sin objetivo)",
      "hours.slaHint":
        "Se cuentan minutos de atención: con horario configurado, el reloj se detiene de noche y los fines de semana. Solo lo detiene la respuesta de una persona, no la del asistente.",
      "hours.save": "Guardar horario",
      "hours.saved": "Horario de «{name}» guardado.",
      "hours.savedDefaults": "Horario de toda la empresa guardado.",
      "day.1": "Lun",
      "day.2": "Mar",
      "day.3": "Mié",
      "day.4": "Jue",
      "day.5": "Vie",
      "day.6": "Sáb",
      "day.7": "Dom",

      // --- Acceso y chatbox ---
      "gate.subtitle": "Inicie sesión para continuar la conversación.",
      "gate.email": "Correo",
      "gate.password": "Contraseña",
      "gate.enter": "Entrar",
      "gate.or": "o",
      "gate.sso": "Iniciar sesión con SSO",
      "gate.google": "Iniciar sesión con Google",
      "gate.staffOnly": "Acceso del personal del equipo",
      "gate.noAccount": "¿No tiene cuenta?",
      "gate.register": "Regístrese",
      "gate.registerTitle": "Regístrese con su correo para empezar a chatear.",
      "gate.haveAccount": "¿Ya tiene cuenta?",
      "gate.login": "Inicie sesión",
      "chat.placeholder": "Escriba su mensaje…",
      "chat.send": "Enviar",
      "chat.you": "Usted",
      "chat.assistant": "Asistente",
      "chat.client": "Cliente",
      "chat.createAccount": "Crear una cuenta",
      "chat.registerButton": "Registrarme",
      "chat.connecting": "Conectando…",
      "chat.width": "Ancho",
      "chat.widthTitle": "Alternar el ancho del panel",
      "chat.refresh": "Actualizar",
      "chat.refreshTitle": "Recargar la conversación",
      "chat.logout": "Cerrar sesión",
      "chat.threadLabel": "Historial de la conversación",
      "chat.emptyTitle": "¿En qué podemos ayudarle?",
      "chat.emptyBody":
        "Escriba su consulta. Este chatbox comparte la misma capa de orquestación que WhatsApp y Microsoft Teams, de modo que la conversación continúa allí donde la deje.",
      "chat.suggestion.order": "Estado de mi pedido",
      "chat.suggestion.invoice": "Solicitar una factura",
      "chat.suggestion.human": "Hablar con una persona",
      "chat.messageLabel": "Mensaje",
      "chat.composerPlaceholder": "Escriba su mensaje. Mayús + Intro inserta un salto de línea.",
      "chat.attach": "Adjuntar una imagen",
      "chat.hint": "Intro envía · Mayús + Intro salta de línea",
      "chat.online": "En línea",
      "chat.offline": "Sin conexión. Reintentando…",
      "chat.offlineNotSent": "Sin conexión. El mensaje no se ha enviado.",
      "chat.customerNamed": "Cliente {name}",
      "common.close": "Cerrar",
      "common.save": "Guardar",
      "admin.passwordHint": "Supervisión y administración reciben una contraseña generada automáticamente, enviada por correo de invitación.",
      "admin.usersTableHint": "El nombre, el departamento principal, los adicionales y la contraseña se guardan juntos con un solo «Guardar» por fila. Dejar la contraseña en blanco la conserva sin cambios.",
      "table.name": "Nombre",
      "table.email": "Correo",
      "table.role": "Rol",
      "table.status": "Estado",
      "table.mainDepartment": "Departamento principal",
      "table.extraDepartments": "Adicionales",
      "table.newPassword": "Nueva contraseña",
      "table.actions": "Acciones",
      "table.channel": "Canal",
      "table.identifier": "Identificador",
      "table.department": "Departamento",
      "table.newToken": "Nuevo token",
      "table.agent": "Agente",
      "table.presence": "Presencia",
      "table.open": "Abiertas",
      "table.unread": "Sin leer",
      "table.phone": "Teléfono",
      "table.conversations": "Conversaciones",
      "table.lastActivity": "Última actividad",
      "accounts.connect": "Conectar cuenta",
      "accounts.title": "Cuentas de canal",
      "accounts.delete": "Eliminar",
      "accounts.deleted": "Cuenta «{name}» eliminada.",
      "accounts.deactivated": "Cuenta desactivada.",
      "accounts.reactivated": "Cuenta reactivada.",
      "admin.sections": "Secciones de administración",
      "admin.tab.users": "Usuarios",
      "admin.tab.channels": "Canales",
      "admin.tab.labels": "Etiquetas",
      "admin.tab.messages": "Saludos y mensajes",
      "admin.tab.appearance": "Apariencia",
      "admin.deactivate": "Desactivar",
      "admin.reactivate": "Reactivar",
      "admin.delete": "Eliminar",
      "admin.deleteConfirm": "¿Seguro?",
      "admin.deleted": "Cuenta eliminada. El historial queda sin nombre.",
      "brand.title": "Color de la marca",
      "brand.hint":
        "Un solo color, el de su marca. De él salen los botones, los enlaces y las " +
        "burbujas, en tema claro y oscuro, con el contraste comprobado para que todo " +
        "siga leyéndose.",
      "brand.color": "Color",
      "brand.presets": "Colores sugeridos",
      "brand.previewTitle": "Vista previa",
      "brand.sampleBubble": "Buenos días",
      "brand.sampleButton": "Enviar",
      "brand.sampleLink": "Ver la conversación",
      "brand.save": "Guardar color",
      "brand.reset": "Volver al color de partida",
      "brand.saved": "Color de la marca actualizado.",
      "brand.resetDone": "Se restableció el color de partida.",
      "brand.badHex": "Escriba un color como #2f5bd7.",

      // --- Conversación saliente ---
      "start.open": "+ Nueva conversación",
      "start.title": "Nueva conversación de WhatsApp",
      "start.hint":
        "WhatsApp no permite escribir texto libre a quien no ha escrito en las últimas " +
        "24 horas. Por eso se empieza con una plantilla aprobada; en cuanto la persona " +
        "conteste, podrá responderle normalmente desde el hilo.",
      "start.number": "Número con prefijo de país",
      "start.template": "Plantilla",
      "start.variable": "Dato {n}",
      "start.send": "Enviar y abrir la conversación",
      "start.sent": "Conversación iniciada.",
      "start.sentHidden":
        "Conversación iniciada. Está en la cola común, fuera de la bandeja que mira ahora.",
      "start.noTemplates": "Esta cuenta de WhatsApp no tiene ninguna plantilla aprobada.",
      "accounts.hint": "Conecte tantos números de WhatsApp, páginas de Facebook o equipos de Teams como necesite. Con departamento asignado, una conversación nueva de esa cuenta cae directo en su cola, sin que nadie tenga que derivarla a mano; sin departamento, sigue en la cola común.",
      "accounts.accessToken": "Token de acceso",
      "accounts.verifyToken": "Token de verificación del webhook",
      "accounts.appSecret": "Clave secreta de la app",
      "accounts.idHint":
        "El identificador es el <code>phone_number_id</code> en WhatsApp, el id de la página en Facebook, o el id del equipo en Teams (se ve solo tras el primer mensaje de ese equipo). Los tres valores de Meta —token de acceso, token de verificación y clave secreta— se guardan cifrados y valen solo para esta cuenta; el que deje en blanco se resuelve con el global de <code>.env</code>. Teams no usa ninguno: se autentica con un JWT de Microsoft.",
    },

    en: {
      "lang.label": "Language",
      "theme.label": "Theme",
      "theme.auto": "Automatic",
      "theme.light": "Light",
      "theme.dark": "Dark",

      "nav.contacts": "Contacts",
      "nav.logout": "Sign out",
      "nav.inboxes": "Inboxes",
      "nav.admin": "Manage users",
      "nav.supervisor": "Supervisor panel",
      "tabs.unassigned": "Queue",
      "tabs.mine": "My chats",
      "tabs.all": "All",
      "role.supervisor": "Supervision",
      "role.agent": "Agent",

      "filter.status": "Filter by status",
      "filter.status.open": "Open",
      "filter.status.closed": "Closed",
      "filter.status.all": "All",
      "filter.channel": "Filter by channel",
      "filter.channel.all": "All channels",
      "filter.department": "Filter by department",
      "filter.department.all": "All departments",
      "filter.label": "Filter by label",
      "filter.label.all": "All labels",

      "views.save": "+ Save view",
      "views.name": "View name",
      "views.shared": "For the whole team",
      "views.saved": "View “{name}” saved.",
      "views.storageFailed": "The view could not be saved in this browser.",
      "views.apply": "Apply “{name}”",
      "views.applyShared": "Apply “{name}” (team view)",
      "views.remove": "Remove “{name}”",
      "views.default": "View {n}",

      "thread.selectOne": "Select a conversation",
      "thread.team": "Team",
      "thread.claim": "Take it",
      "thread.release": "Return to queue",
      "thread.close": "Close",
      "state.group": "Conversation state",
      "state.pending": "Pending",
      "state.inProgress": "In progress",
      "state.solved": "Solved",
      "state.set.pending": "Marked as pending.",
      "state.set.in_progress": "Marked as in progress.",
      "state.set.solved": "Marked as solved.",
      "filter.status.unresolved": "Unresolved",
      "filter.status.pending": "Pending",
      "filter.status.inProgress": "In progress",
      "filter.status.solved": "Solved",
      "thread.empty":
        "The shared queue gathers everything coming in from WhatsApp, Microsoft Teams and the web chatbox. Take a conversation or review your own.",
      "thread.listEmptyQueue": "The shared queue is empty. Good work.",
      "thread.listEmptyFilters": "No conversations match these filters.",
      "thread.noContact": "Unnamed contact",
      "thread.messageCount": "{n} messages in the history",
      "thread.loadFailed": "The thread could not be loaded — {error}",

      "badge.queued": "Queued",
      "badge.mine": "Mine",
      "badge.breached": "Overdue",
      "badge.breachedTitle": "The first-response target has passed",
      "badge.slaTitle": "First-response target: {time}",
      "sla.dueNow": "due now",
      "sla.minutes": "{n} min",
      "sla.hours": "{n} h",
      "sla.days": "{n} d",

      "composer.answer": "Reply",
      "composer.placeholder":
        "Write your reply to the customer… (type “/” to insert a saved reply)",
      "composer.attach": "Attach an image",
      "composer.send": "Reply",
      "composer.noSelection": "No conversation selected",
      "composer.hint": "The reply goes out through the original channel",
      "composer.queued": "Reply queued for delivery.",
      "composer.attachReady": "Image ready: {name}",
      "composer.attachFailed": "The image could not be uploaded — {error}",

      "aside.transfer": "Hand over to a colleague",
      "aside.transferSection": "Hand-over",
      "aside.internalComments": "Internal comments",
      "aside.transferHint":
        "The conversation keeps its full history; only the owner changes.",
      "aside.colleague": "Colleague",
      "aside.orDepartment": "or department (back to the queue, unassigned)",
      "aside.choose": "— Choose —",
      "aside.reason": "Reason (saved as an internal note)",
      "aside.reasonPlaceholder": "Context for whoever picks it up",
      "aside.transferButton": "Hand over",
      "aside.labels": "Labels",
      "aside.labelsHint": "Classify the conversation to filter it later.",
      "aside.labelsEmpty": "No labels created yet.",
      "aside.macros": "Macros",
      "aside.macrosHint": "Apply several actions at once.",
      "aside.notes": "Internal notes",
      "aside.notesHint": "Visible to the team only. The customer never sees them.",
      "aside.notesEmpty": "No notes yet.",
      "aside.notePlaceholder": "Add a note…",
      "aside.noteSave": "Save note",
      "aside.noteSaved": "Internal note saved.",
      "aside.history": "Hand-over history",
      "aside.contact": "Customer details",
      "aside.contactHint": "Editing is reserved for supervision and administration.",
      "aside.name": "Name",
      "aside.phone": "Phone",
      "aside.email": "Email",
      "aside.save": "Save",
      "aside.comments": "Comments",
      "aside.commentPlaceholder": "Add a comment…",
      "aside.commentSave": "Save comment",

      "admin.title": "User administration",
      "admin.close": "Close",
      "admin.autoReply": "Automatic reply",
      "admin.autoReplyHint":
        "Sent once, on the first message the assistant cannot resolve.",
      "admin.text": "Text",
      "admin.autoReplySave": "Save automatic reply",
      "admin.createUser": "Create user",
      "admin.displayName": "Display name",
      "admin.role": "Role",
      "admin.mainDepartment": "Main department",
      "admin.noDepartment": "No department",
      "admin.initialPassword": "Initial password",
      "admin.departments": "Departments",
      "admin.createDepartment": "Create department",
      "admin.departmentsEmpty": "No departments yet.",
      "admin.departmentCreated": "Department created.",
      "admin.labels": "Labels",
      "admin.color": "Colour",
      "admin.createLabel": "Create label",
      "admin.labelsEmpty": "No labels yet.",
      "admin.labelCreated": "Label created.",
      "admin.cannedResponses": "Saved replies",
      "admin.shortcode": "Shortcut",
      "admin.title2": "Title",
      "admin.createCanned": "Create reply",
      "admin.cannedEmpty": "No saved replies yet.",
      "admin.cannedCreated": "Saved reply created.",
      "admin.remove": "Remove",
      "admin.registeredUsers": "Registered users",

      "macro.title": "Macros",
      "macro.hint":
        "A sequence of actions the team applies to a conversation in one click. If one step fails, none are applied.",
      "macro.addStep": "Add step",
      "macro.addStepButton": "+ Add step",
      "macro.create": "Create macro",
      "macro.empty": "No macros yet.",
      "macro.created": "Macro “{name}” created.",
      "macro.applied": "Macro “{name}” applied: {n} steps.",
      "macro.needsNameAndStep": "The macro needs a name and at least one step.",
      "macro.noteNeedsText": "The note needs some text.",
      "macro.createFirst": "Create {what} first.",
      "macro.stepCount": "{name} ({n} steps)",
      "macro.removeStep": "Remove this step",
      "macro.action.label": "Add a label",
      "macro.action.reply": "Reply with a template",
      "macro.action.note": "Leave an internal note",
      "macro.action.transfer": "Hand over to a department",
      "macro.action.close": "Close the conversation",
      "macro.short.label": "Label",
      "macro.short.reply": "Reply",
      "macro.short.note": "Internal note",
      "macro.short.transfer": "Hand over",
      "macro.short.close": "Close",
      "macro.target.label": "Label",
      "macro.target.reply": "Template",
      "macro.target.transfer": "Department",

      "hours.title": "Business hours",
      "hours.hint":
        "Outside these hours the assistant stops replying: the customer's message still arrives and stays in the queue. A department with no hours is available around the clock.",
      "hours.department": "Department",
      "hours.defaults": "Whole company (and shared queue)",
      "hours.defaultsHint":
        "Governs the shared queue —what has not been handed over yet— and every department that sets none of its own.",
      "hours.timezone": "Time zone",
      "hours.message": "Out-of-hours notice to the customer (empty = no notice)",
      "hours.messagePlaceholder": "We are out of hours; we will reply tomorrow.",
      "hours.day": "Day",
      "hours.opens": "Opens",
      "hours.closes": "Closes",
      "hours.and": "and",
      "hours.morning": "Morning",
      "hours.afternoon": "Afternoon",
      "hours.splitHint":
        "Leave the afternoon empty if you do not run a split shift. An unticked day is closed.",
      "hours.slaTarget": "First-response target, in minutes (empty = no target)",
      "hours.slaHint":
        "Counted in working minutes: with hours configured, the clock stops overnight and at weekends. Only a reply from a person stops it, never the assistant's.",
      "hours.save": "Save hours",
      "hours.saved": "Hours for “{name}” saved.",
      "hours.savedDefaults": "Company-wide hours saved.",
      "day.1": "Mon",
      "day.2": "Tue",
      "day.3": "Wed",
      "day.4": "Thu",
      "day.5": "Fri",
      "day.6": "Sat",
      "day.7": "Sun",

      "gate.subtitle": "Sign in to continue the conversation.",
      "gate.email": "Email",
      "gate.password": "Password",
      "gate.enter": "Sign in",
      "gate.or": "or",
      "gate.sso": "Sign in with SSO",
      "gate.google": "Sign in with Google",
      "gate.staffOnly": "Team staff access",
      "gate.noAccount": "No account yet?",
      "gate.register": "Register",
      "gate.registerTitle": "Register with your email to start chatting.",
      "gate.haveAccount": "Already have an account?",
      "gate.login": "Sign in",
      "chat.placeholder": "Write your message…",
      "chat.send": "Send",
      "chat.you": "You",
      "chat.assistant": "Assistant",
      "chat.client": "Customer",
      "chat.createAccount": "Create an account",
      "chat.registerButton": "Register",
      "chat.connecting": "Connecting…",
      "chat.width": "Width",
      "chat.widthTitle": "Toggle the panel width",
      "chat.refresh": "Refresh",
      "chat.refreshTitle": "Reload the conversation",
      "chat.logout": "Sign out",
      "chat.threadLabel": "Conversation history",
      "chat.emptyTitle": "How can we help you?",
      "chat.emptyBody":
        "Write your question. This chatbox shares the same orchestration layer as WhatsApp and Microsoft Teams, so the conversation carries on wherever you leave it.",
      "chat.suggestion.order": "Status of my order",
      "chat.suggestion.invoice": "Request an invoice",
      "chat.suggestion.human": "Talk to a person",
      "chat.messageLabel": "Message",
      "chat.composerPlaceholder": "Write your message. Shift + Enter inserts a line break.",
      "chat.attach": "Attach an image",
      "chat.hint": "Enter sends · Shift + Enter breaks the line",
      "chat.online": "Online",
      "chat.offline": "Offline. Retrying…",
      "chat.offlineNotSent": "Offline. The message was not sent.",
      "chat.customerNamed": "Customer {name}",
      "common.close": "Close",
      "common.save": "Save",
      "admin.passwordHint": "Supervision and administration receive an automatically generated password, sent by invitation email.",
      "admin.usersTableHint": "The name, main department, extra departments and password are saved together with a single “Save” per row. Leaving the password blank keeps it unchanged.",
      "table.name": "Name",
      "table.email": "Email",
      "table.role": "Role",
      "table.status": "Status",
      "table.mainDepartment": "Main department",
      "table.extraDepartments": "Extra",
      "table.newPassword": "New password",
      "table.actions": "Actions",
      "table.channel": "Channel",
      "table.identifier": "Identifier",
      "table.department": "Department",
      "table.newToken": "New token",
      "table.agent": "Agent",
      "table.presence": "Presence",
      "table.open": "Open",
      "table.unread": "Unread",
      "table.phone": "Phone",
      "table.conversations": "Conversations",
      "table.lastActivity": "Last activity",
      "accounts.connect": "Connect account",
      "accounts.title": "Channel accounts",
      "accounts.delete": "Delete",
      "accounts.deleted": "Account “{name}” deleted.",
      "accounts.deactivated": "Account deactivated.",
      "accounts.reactivated": "Account reactivated.",
      "admin.sections": "Administration sections",
      "admin.tab.users": "Users",
      "admin.tab.channels": "Channels",
      "admin.tab.labels": "Labels",
      "admin.tab.messages": "Greetings & messages",
      "admin.tab.appearance": "Appearance",
      "admin.deactivate": "Deactivate",
      "admin.reactivate": "Reactivate",
      "admin.delete": "Delete",
      "admin.deleteConfirm": "Sure?",
      "admin.deleted": "Account deleted. The history is left without a name.",
      "brand.title": "Brand colour",
      "brand.hint":
        "A single colour — yours. Buttons, links and bubbles all follow from it, in " +
        "light and dark themes, with the contrast checked so everything stays readable.",
      "brand.color": "Colour",
      "brand.presets": "Suggested colours",
      "brand.previewTitle": "Preview",
      "brand.sampleBubble": "Good morning",
      "brand.sampleButton": "Send",
      "brand.sampleLink": "Open the conversation",
      "brand.save": "Save colour",
      "brand.reset": "Back to the default colour",
      "brand.saved": "Brand colour updated.",
      "brand.resetDone": "Default colour restored.",
      "brand.badHex": "Enter a colour such as #2f5bd7.",

      // --- Outbound conversation ---
      "start.open": "+ New conversation",
      "start.title": "New WhatsApp conversation",
      "start.hint":
        "WhatsApp does not allow free text to someone who has not written in the last " +
        "24 hours. That is why you start with an approved template; as soon as they " +
        "reply, you can answer normally from the thread.",
      "start.number": "Number with country code",
      "start.template": "Template",
      "start.variable": "Value {n}",
      "start.send": "Send and open the conversation",
      "start.sent": "Conversation started.",
      "start.sentHidden":
        "Conversation started. It is in the common queue, outside the inbox you are viewing.",
      "start.noTemplates": "This WhatsApp account has no approved templates.",
      "accounts.hint": "Connect as many WhatsApp numbers, Facebook pages or Teams teams as you need. With a department assigned, a new conversation from that account lands straight in its queue, with nobody having to hand it over; without one, it stays in the shared queue.",
      "accounts.accessToken": "Access token",
      "accounts.verifyToken": "Webhook verify token",
      "accounts.appSecret": "App secret",
      "accounts.idHint":
        "The identifier is the <code>phone_number_id</code> on WhatsApp, the page id on Facebook, or the team id on Teams (only visible after that team’s first message). All three Meta values — access token, verify token and app secret — are stored encrypted and apply to this account alone; whichever you leave blank falls back to the global one in <code>.env</code>. Teams uses none of them: it authenticates with a Microsoft JWT.",
    },

    de: {
      "lang.label": "Sprache",
      "theme.label": "Design",
      "theme.auto": "Automatisch",
      "theme.light": "Hell",
      "theme.dark": "Dunkel",

      "nav.contacts": "Kontakte",
      "nav.logout": "Abmelden",
      "nav.inboxes": "Postfächer",
      "nav.admin": "Benutzer verwalten",
      "nav.supervisor": "Aufsichtsbereich",
      "tabs.unassigned": "Warteschlange",
      "tabs.mine": "Meine Chats",
      "tabs.all": "Alle",
      "role.supervisor": "Aufsicht",
      "role.agent": "Mitarbeiter",

      "filter.status": "Nach Status filtern",
      "filter.status.open": "Offen",
      "filter.status.closed": "Geschlossen",
      "filter.status.all": "Alle",
      "filter.channel": "Nach Kanal filtern",
      "filter.channel.all": "Alle Kanäle",
      "filter.department": "Nach Abteilung filtern",
      "filter.department.all": "Alle Abteilungen",
      "filter.label": "Nach Etikett filtern",
      "filter.label.all": "Alle Etiketten",

      "views.save": "+ Ansicht speichern",
      "views.name": "Name der Ansicht",
      "views.shared": "Für das ganze Team",
      "views.saved": "Ansicht „{name}“ gespeichert.",
      "views.storageFailed": "Die Ansicht konnte in diesem Browser nicht gespeichert werden.",
      "views.apply": "„{name}“ anwenden",
      "views.applyShared": "„{name}“ anwenden (Team-Ansicht)",
      "views.remove": "„{name}“ entfernen",
      "views.default": "Ansicht {n}",

      "thread.selectOne": "Wählen Sie ein Gespräch",
      "thread.team": "Team",
      "thread.claim": "Übernehmen",
      "thread.release": "Zurück in die Warteschlange",
      "thread.close": "Schließen",
      "state.group": "Status des Gesprächs",
      "state.pending": "Offen",
      "state.inProgress": "In Bearbeitung",
      "state.solved": "Gelöst",
      "state.set.pending": "Als offen markiert.",
      "state.set.in_progress": "Als in Bearbeitung markiert.",
      "state.set.solved": "Als gelöst markiert.",
      "filter.status.unresolved": "Unerledigt",
      "filter.status.pending": "Offen",
      "filter.status.inProgress": "In Bearbeitung",
      "filter.status.solved": "Gelöst",
      "thread.empty":
        "In der gemeinsamen Warteschlange läuft alles zusammen, was über WhatsApp, Microsoft Teams und die Web-Chatbox hereinkommt. Übernehmen Sie ein Gespräch oder sehen Sie Ihre eigenen durch.",
      "thread.listEmptyQueue": "Die gemeinsame Warteschlange ist leer. Gute Arbeit.",
      "thread.listEmptyFilters": "Keine Gespräche mit diesen Filtern.",
      "thread.noContact": "Kontakt ohne Namen",
      "thread.messageCount": "{n} Nachrichten im Verlauf",
      "thread.loadFailed": "Der Verlauf konnte nicht geladen werden — {error}",

      "badge.queued": "In der Warteschlange",
      "badge.mine": "Meins",
      "badge.breached": "Überfällig",
      "badge.breachedTitle": "Die Frist für die erste Antwort ist verstrichen",
      "badge.slaTitle": "Frist für die erste Antwort: {time}",
      "sla.dueNow": "jetzt fällig",
      "sla.minutes": "{n} Min.",
      "sla.hours": "{n} Std.",
      "sla.days": "{n} T.",

      "composer.answer": "Antwort",
      "composer.placeholder":
        "Schreiben Sie Ihre Antwort an den Kunden… („/“ fügt eine gespeicherte Antwort ein)",
      "composer.attach": "Ein Bild anhängen",
      "composer.send": "Antworten",
      "composer.noSelection": "Kein Gespräch ausgewählt",
      "composer.hint": "Die Antwort geht über den ursprünglichen Kanal hinaus",
      "composer.queued": "Antwort zum Versand eingereiht.",
      "composer.attachReady": "Bild bereit: {name}",
      "composer.attachFailed": "Das Bild konnte nicht hochgeladen werden — {error}",

      "aside.transfer": "An eine Kollegin oder einen Kollegen übergeben",
      "aside.transferSection": "Übergabe",
      "aside.internalComments": "Interne Kommentare",
      "aside.transferHint":
        "Das Gespräch behält seinen gesamten Verlauf; nur die Zuständigkeit wechselt.",
      "aside.colleague": "Kollegin oder Kollege",
      "aside.orDepartment": "oder Abteilung (zurück in die Warteschlange, ohne Zuständige)",
      "aside.choose": "— Auswählen —",
      "aside.reason": "Grund (wird als interne Notiz gespeichert)",
      "aside.reasonPlaceholder": "Zusammenhang für die übernehmende Person",
      "aside.transferButton": "Übergeben",
      "aside.labels": "Etiketten",
      "aside.labelsHint": "Ordnen Sie das Gespräch ein, um später danach zu filtern.",
      "aside.labelsEmpty": "Noch keine Etiketten angelegt.",
      "aside.macros": "Makros",
      "aside.macrosHint": "Mehrere Aktionen auf einmal anwenden.",
      "aside.notes": "Interne Notizen",
      "aside.notesHint": "Nur für das Team sichtbar. Der Kunde bekommt sie nie zu sehen.",
      "aside.notesEmpty": "Noch keine Notizen.",
      "aside.notePlaceholder": "Notiz hinzufügen…",
      "aside.noteSave": "Notiz speichern",
      "aside.noteSaved": "Interne Notiz gespeichert.",
      "aside.history": "Verlauf der Übergaben",
      "aside.contact": "Kundendaten",
      "aside.contactHint": "Das Bearbeiten bleibt Aufsicht und Verwaltung vorbehalten.",
      "aside.name": "Name",
      "aside.phone": "Telefon",
      "aside.email": "E-Mail",
      "aside.save": "Speichern",
      "aside.comments": "Kommentare",
      "aside.commentPlaceholder": "Kommentar hinzufügen…",
      "aside.commentSave": "Kommentar speichern",

      "admin.title": "Benutzerverwaltung",
      "admin.close": "Schließen",
      "admin.autoReply": "Automatische Antwort",
      "admin.autoReplyHint":
        "Wird einmal gesendet, bei der ersten Nachricht, die der Assistent nicht lösen kann.",
      "admin.text": "Text",
      "admin.autoReplySave": "Automatische Antwort speichern",
      "admin.createUser": "Benutzer anlegen",
      "admin.displayName": "Anzeigename",
      "admin.role": "Rolle",
      "admin.mainDepartment": "Hauptabteilung",
      "admin.noDepartment": "Keine Abteilung",
      "admin.initialPassword": "Anfangspasswort",
      "admin.departments": "Abteilungen",
      "admin.createDepartment": "Abteilung anlegen",
      "admin.departmentsEmpty": "Noch keine Abteilungen.",
      "admin.departmentCreated": "Abteilung angelegt.",
      "admin.labels": "Etiketten",
      "admin.color": "Farbe",
      "admin.createLabel": "Etikett anlegen",
      "admin.labelsEmpty": "Noch keine Etiketten.",
      "admin.labelCreated": "Etikett angelegt.",
      "admin.cannedResponses": "Gespeicherte Antworten",
      "admin.shortcode": "Kürzel",
      "admin.title2": "Titel",
      "admin.createCanned": "Antwort anlegen",
      "admin.cannedEmpty": "Noch keine gespeicherten Antworten.",
      "admin.cannedCreated": "Gespeicherte Antwort angelegt.",
      "admin.remove": "Entfernen",
      "admin.registeredUsers": "Registrierte Benutzer",

      "macro.title": "Makros",
      "macro.hint":
        "Eine Abfolge von Aktionen, die das Team mit einem Klick auf ein Gespräch anwendet. Schlägt ein Schritt fehl, wird keiner angewendet.",
      "macro.addStep": "Schritt hinzufügen",
      "macro.addStepButton": "+ Schritt hinzufügen",
      "macro.create": "Makro anlegen",
      "macro.empty": "Noch keine Makros.",
      "macro.created": "Makro „{name}“ angelegt.",
      "macro.applied": "Makro „{name}“ angewendet: {n} Schritte.",
      "macro.needsNameAndStep": "Das Makro braucht einen Namen und mindestens einen Schritt.",
      "macro.noteNeedsText": "Die Notiz braucht einen Text.",
      "macro.createFirst": "Legen Sie zuerst {what} an.",
      "macro.stepCount": "{name} ({n} Schritte)",
      "macro.removeStep": "Diesen Schritt entfernen",
      "macro.action.label": "Etikett vergeben",
      "macro.action.reply": "Mit einer Vorlage antworten",
      "macro.action.note": "Interne Notiz hinterlassen",
      "macro.action.transfer": "An eine Abteilung übergeben",
      "macro.action.close": "Das Gespräch schließen",
      "macro.short.label": "Etikett",
      "macro.short.reply": "Antworten",
      "macro.short.note": "Interne Notiz",
      "macro.short.transfer": "Übergeben",
      "macro.short.close": "Schließen",
      "macro.target.label": "Etikett",
      "macro.target.reply": "Vorlage",
      "macro.target.transfer": "Abteilung",

      "hours.title": "Servicezeiten",
      "hours.hint":
        "Außerhalb dieser Zeiten antwortet der Assistent nicht mehr: Die Nachricht des Kunden kommt trotzdem an und bleibt in der Warteschlange. Eine Abteilung ohne Zeiten ist rund um die Uhr erreichbar.",
      "hours.department": "Abteilung",
      "hours.defaults": "Ganzes Unternehmen (und gemeinsame Warteschlange)",
      "hours.defaultsHint":
        "Gilt für die gemeinsame Warteschlange —was noch nicht übergeben wurde— und für jede Abteilung, die nichts Eigenes festlegt.",
      "hours.timezone": "Zeitzone",
      "hours.message": "Hinweis an den Kunden außerhalb der Zeiten (leer = kein Hinweis)",
      "hours.messagePlaceholder": "Wir sind außerhalb der Servicezeiten; wir antworten morgen.",
      "hours.day": "Tag",
      "hours.opens": "Öffnet",
      "hours.closes": "Schließt",
      "hours.and": "und",
      "hours.morning": "Vormittag",
      "hours.afternoon": "Nachmittag",
      "hours.splitHint":
        "Lassen Sie den Nachmittag leer, wenn Sie keine geteilte Schicht fahren. Ein nicht angehakter Tag ist geschlossen.",
      "hours.slaTarget": "Frist für die erste Antwort, in Minuten (leer = keine Frist)",
      "hours.slaHint":
        "Gezählt werden Serviceminuten: Mit eingerichteten Zeiten steht die Uhr nachts und am Wochenende still. Nur die Antwort einer Person hält sie an, nie die des Assistenten.",
      "hours.save": "Zeiten speichern",
      "hours.saved": "Zeiten für „{name}“ gespeichert.",
      "hours.savedDefaults": "Unternehmensweite Zeiten gespeichert.",
      "day.1": "Mo",
      "day.2": "Di",
      "day.3": "Mi",
      "day.4": "Do",
      "day.5": "Fr",
      "day.6": "Sa",
      "day.7": "So",

      "gate.subtitle": "Melden Sie sich an, um das Gespräch fortzusetzen.",
      "gate.email": "E-Mail",
      "gate.password": "Passwort",
      "gate.enter": "Anmelden",
      "gate.or": "oder",
      "gate.sso": "Mit SSO anmelden",
      "gate.google": "Über Google anmelden",
      "gate.staffOnly": "Zugang für Teammitglieder",
      "gate.noAccount": "Noch kein Konto?",
      "gate.register": "Registrieren",
      "gate.registerTitle": "Registrieren Sie sich mit Ihrer E-Mail, um zu chatten.",
      "gate.haveAccount": "Sie haben schon ein Konto?",
      "gate.login": "Anmelden",
      "chat.placeholder": "Schreiben Sie Ihre Nachricht…",
      "chat.send": "Senden",
      "chat.you": "Sie",
      "chat.assistant": "Assistent",
      "chat.client": "Kunde",
      "chat.createAccount": "Konto anlegen",
      "chat.registerButton": "Registrieren",
      "chat.connecting": "Verbindung wird hergestellt…",
      "chat.width": "Breite",
      "chat.widthTitle": "Die Breite des Bereichs umschalten",
      "chat.refresh": "Aktualisieren",
      "chat.refreshTitle": "Das Gespräch neu laden",
      "chat.logout": "Abmelden",
      "chat.threadLabel": "Gesprächsverlauf",
      "chat.emptyTitle": "Wie können wir Ihnen helfen?",
      "chat.emptyBody":
        "Schreiben Sie Ihre Frage. Diese Chatbox nutzt dieselbe Vermittlungsschicht wie WhatsApp und Microsoft Teams, sodass das Gespräch dort weitergeht, wo Sie es verlassen.",
      "chat.suggestion.order": "Status meiner Bestellung",
      "chat.suggestion.invoice": "Eine Rechnung anfordern",
      "chat.suggestion.human": "Mit einer Person sprechen",
      "chat.messageLabel": "Nachricht",
      "chat.composerPlaceholder":
        "Schreiben Sie Ihre Nachricht. Umschalt + Eingabe fügt einen Zeilenumbruch ein.",
      "chat.attach": "Ein Bild anhängen",
      "chat.hint": "Eingabe sendet · Umschalt + Eingabe bricht die Zeile um",
      "chat.online": "Online",
      "chat.offline": "Keine Verbindung. Neuer Versuch…",
      "chat.offlineNotSent": "Keine Verbindung. Die Nachricht wurde nicht gesendet.",
      "chat.customerNamed": "Kunde {name}",
      "common.close": "Schließen",
      "common.save": "Speichern",
      "admin.passwordHint": "Aufsicht und Verwaltung erhalten ein automatisch erzeugtes Passwort, das per Einladungs-E-Mail zugestellt wird.",
      "admin.usersTableHint": "Name, Hauptabteilung, zusätzliche Abteilungen und Passwort werden gemeinsam mit einem einzigen „Speichern“ pro Zeile gesichert. Bleibt das Passwort leer, wird es nicht geändert.",
      "table.name": "Name",
      "table.email": "E-Mail",
      "table.role": "Rolle",
      "table.status": "Status",
      "table.mainDepartment": "Hauptabteilung",
      "table.extraDepartments": "Zusätzliche",
      "table.newPassword": "Neues Passwort",
      "table.actions": "Aktionen",
      "table.channel": "Kanal",
      "table.identifier": "Kennung",
      "table.department": "Abteilung",
      "table.newToken": "Neues Token",
      "table.agent": "Mitarbeiter",
      "table.presence": "Anwesenheit",
      "table.open": "Offen",
      "table.unread": "Ungelesen",
      "table.phone": "Telefon",
      "table.conversations": "Gespräche",
      "table.lastActivity": "Letzte Aktivität",
      "accounts.connect": "Konto verbinden",
      "accounts.title": "Kanalkonten",
      "accounts.delete": "Löschen",
      "accounts.deleted": "Konto „{name}“ gelöscht.",
      "accounts.deactivated": "Konto deaktiviert.",
      "accounts.reactivated": "Konto reaktiviert.",
      "admin.sections": "Verwaltungsbereiche",
      "admin.tab.users": "Benutzer",
      "admin.tab.channels": "Kanäle",
      "admin.tab.labels": "Etiketten",
      "admin.tab.messages": "Begrüßungen & Nachrichten",
      "admin.tab.appearance": "Erscheinungsbild",
      "admin.deactivate": "Deaktivieren",
      "admin.reactivate": "Reaktivieren",
      "admin.delete": "Löschen",
      "admin.deleteConfirm": "Sicher?",
      "admin.deleted": "Konto gelöscht. Der Verlauf bleibt ohne Namen.",
      "brand.title": "Markenfarbe",
      "brand.hint":
        "Eine einzige Farbe – Ihre Markenfarbe. Schaltflächen, Links und Sprechblasen " +
        "leiten sich daraus ab, in hellem und dunklem Design, mit geprüftem Kontrast, " +
        "damit alles lesbar bleibt.",
      "brand.color": "Farbe",
      "brand.presets": "Vorgeschlagene Farben",
      "brand.previewTitle": "Vorschau",
      "brand.sampleBubble": "Guten Morgen",
      "brand.sampleButton": "Senden",
      "brand.sampleLink": "Zum Gespräch",
      "brand.save": "Farbe speichern",
      "brand.reset": "Zurück zur Standardfarbe",
      "brand.saved": "Markenfarbe aktualisiert.",
      "brand.resetDone": "Standardfarbe wiederhergestellt.",
      "brand.badHex": "Geben Sie eine Farbe wie #2f5bd7 ein.",

      // --- Ausgehendes Gespräch ---
      "start.open": "+ Neues Gespräch",
      "start.title": "Neues WhatsApp-Gespräch",
      "start.hint":
        "WhatsApp erlaubt keinen freien Text an jemanden, der in den letzten 24 Stunden " +
        "nicht geschrieben hat. Deshalb beginnt man mit einer genehmigten Vorlage; " +
        "sobald die Person antwortet, können Sie im Verlauf normal antworten.",
      "start.number": "Nummer mit Ländervorwahl",
      "start.template": "Vorlage",
      "start.variable": "Wert {n}",
      "start.send": "Senden und Gespräch öffnen",
      "start.sent": "Gespräch begonnen.",
      "start.sentHidden":
        "Gespräch begonnen. Es liegt in der gemeinsamen Warteschlange, außerhalb des Postfachs, das Sie gerade sehen.",
      "start.noTemplates": "Dieses WhatsApp-Konto hat keine genehmigten Vorlagen.",
      "accounts.hint": "Verbinden Sie so viele WhatsApp-Nummern, Facebook-Seiten oder Teams-Teams, wie Sie brauchen. Mit zugewiesener Abteilung landet ein neues Gespräch dieses Kontos direkt in deren Warteschlange, ohne dass es jemand übergeben muss; ohne Abteilung bleibt es in der gemeinsamen Warteschlange.",
      "accounts.accessToken": "Zugriffstoken",
      "accounts.verifyToken": "Webhook-Verify-Token",
      "accounts.appSecret": "App-Secret",
      "accounts.idHint":
        "Die Kennung ist die <code>phone_number_id</code> bei WhatsApp, die Seiten-ID bei Facebook oder die Team-ID bei Teams (erst nach der ersten Nachricht dieses Teams sichtbar). Alle drei Meta-Werte — Zugriffstoken, Verify-Token und App-Secret — werden verschlüsselt gespeichert und gelten nur für dieses Konto; was Sie leer lassen, fällt auf den globalen Wert aus <code>.env</code> zurück. Teams nutzt keinen davon: es authentifiziert sich mit einem Microsoft-JWT.",
    },
  };

  function readStored() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return DICTIONARIES[stored] ? stored : null;
    } catch {
      // Navegación privada o almacenamiento bloqueado.
      return null;
    }
  }

  /** Primer idioma del navegador que sepamos hablar, o ``null``. */
  function detectFromBrowser() {
    const preferred = navigator.languages?.length
      ? navigator.languages
      : [navigator.language || ""];
    for (const tag of preferred) {
      // "de-DE" y "de" valen lo mismo: interesa el idioma, no la región.
      const code = String(tag).toLowerCase().split("-")[0];
      if (DICTIONARIES[code]) {
        return code;
      }
    }
    return null;
  }

  let current = readStored() || FALLBACK;

  /**
   * Adopta el idioma del navegador, si es uno de los tres.
   *
   * Solo lo usa el chatbox público. Una elección hecha a mano se respeta: la
   * detección es el punto de partida de quien llega por primera vez, no algo
   * que reescriba lo que el visitante ya decidió.
   */
  function useBrowserLanguage() {
    if (readStored()) {
      return;
    }
    const detected = detectFromBrowser();
    if (detected && detected !== current) {
      current = detected;
      apply();
      document.dispatchEvent(new CustomEvent("languagechange"));
    }
  }

  /** Texto de la clave en el idioma activo, con los huecos ya rellenados. */
  function t(key, values) {
    const text = DICTIONARIES[current]?.[key] ?? DICTIONARIES[FALLBACK][key] ?? key;
    if (!values) {
      return text;
    }
    return text.replace(/\{(\w+)\}/g, (whole, name) =>
      values[name] === undefined ? whole : String(values[name]),
    );
  }

  /** Traduce todo lo marcado dentro de `root`. */
  function apply(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-i18n]").forEach((el) => {
      el.textContent = t(el.dataset.i18n);
    });
    // Solo para los textos que llevan marcado propio dentro, como un <code>.
    // La cadena sale de este mismo diccionario, nunca de lo que escribe nadie.
    scope.querySelectorAll("[data-i18n-html]").forEach((el) => {
      el.innerHTML = t(el.dataset.i18nHtml);
    });
    scope.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      el.placeholder = t(el.dataset.i18nPlaceholder);
    });
    scope.querySelectorAll("[data-i18n-title]").forEach((el) => {
      el.title = t(el.dataset.i18nTitle);
    });
    scope.querySelectorAll("[data-i18n-aria]").forEach((el) => {
      el.setAttribute("aria-label", t(el.dataset.i18nAria));
    });
    document.documentElement.lang = current;
  }

  function setLanguage(code) {
    if (!DICTIONARIES[code]) {
      return;
    }
    current = code;
    try {
      localStorage.setItem(STORAGE_KEY, code);
    } catch {
      // Se pierde al cerrar, pero la sesión en curso ya quedó traducida.
    }
    apply();
    // Lo pintado por JavaScript no lleva marcas en el HTML: cada pantalla se
    // vuelve a dibujar al escuchar este aviso.
    document.dispatchEvent(new CustomEvent("languagechange"));
  }

  /** Rellena un `<select>` con los idiomas y lo deja escuchando. */
  function mountSelector(select) {
    if (!select) {
      return;
    }
    select.textContent = "";
    LANGUAGES.forEach(({ code, label }) => {
      const option = document.createElement("option");
      option.value = code;
      option.textContent = label;
      select.appendChild(option);
    });
    select.value = current;
    select.addEventListener("change", () => setLanguage(select.value));
  }

  window.i18n = {
    t,
    apply,
    setLanguage,
    mountSelector,
    useBrowserLanguage,
    LANGUAGES,
    current: () => current,
  };

  // El HTML ya presente se traduce en cuanto el documento está listo.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => apply());
  } else {
    apply();
  }
})();
