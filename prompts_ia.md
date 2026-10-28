# Registro de Uso y Consultas a Inteligencia Artificial (FitLife)

> **Asignatura:** Desarrollo Backend  
> **Evaluación:** Evaluación Parcial N°3 (EVA 3)  
> **Estudiante:** Constanza Gajardo  
> **Proyecto:** FitLife - Plataforma de Control de Asistencia y Reserva de Clases para Gimnasios  
> **Docente:** Marcelo Alvarado Aravena  
> **Criterio de Rúbrica Asociado:** Criterio 4 (Seguridad, Sesiones y Apoyo de IA)

---

## 1. Declaración de Uso de IA
En conformidad con los requerimientos de la evaluación, se utilizó asistencia de Inteligencia Artificial como herramienta de apoyo técnico en:
1. Arquitectura y estructuración del modelo relacional en Django ORM.
2. Diseño del servicio de validación de acceso QR con medición de latencia en milisegundos.
3. Implementación de control de concurrencia y lista de espera FIFO con promoción automática de cupos.
4. Generación de interfaces HTML/CSS responsivas siguiendo la maqueta de diseño de FitLife.
5. Elaboración de fixtures y scripts de carga de datos sintéticos representativos del contexto chileno.

---

## 2. Resúmenes del apoyo inicial en Antigravity

Los siguientes textos se conservaron del registro inicial del proyecto. Son resúmenes temáticos pendientes de contrastar con la conversación original de Antigravity; no se presentan como transcripciones literales verificadas.

### Consulta 1: Definición de Arquitectura y Modelos Relacionales
* **Objetivo:** Definir los modelos de datos en Django ORM para cumplir con los requerimientos de FitLife (Socios, Planes, Membresías en 3 estados, Pagos, Accesos y Clases).
* **Resumen de la consulta (por verificar):**
  > Actúa como arquitecto de software y tutor. Diseña los modelos de datos en Django para FitLife: control de socios, planes, membresías (estados: activa, vencida, congelada), pagos sin pasarela externa, clases con cupo y lista de espera, y control de acceso por QR. Incluye eliminación lógica en las entidades correspondientes y explica la justificación de cada modelo para la defensa.
* **Resultado obtenido y aplicado:**
  - Creación de la clase abstracta `SoftDeleteModel` y el manager `ActiveManager`.
  - Definición de relaciones `ForeignKey` con protección de integridad (`on_delete=models.PROTECT`).
  - Métodos helper en el modelo `Membresia` para auto-actualización de estados según la fecha actual.

---

### Consulta 2: Lógica de Control de Acceso y Medición de Latencia
* **Objetivo:** Cumplir el requerimiento de validación QR de baja latencia y prevención de inconsistencias (doble entrada consecutiva).
* **Resumen de la consulta (por verificar):**
  > ¿Cómo implementar en Django un servicio de validación de acceso mediante simulación QR que mida la latencia de respuesta en milisegundos y evite que un socio registre dos entradas sin haber salido? Explica las limitaciones de la medición para responderle al profesor.
* **Resultado obtenido y aplicado:**
  - Función `validar_y_registrar_acceso` en `gestion/services.py` usando `time.perf_counter()`.
  - Validación de estado `esta_dentro()` para bloquear entradas duplicadas consecutivas.
  - Registro de acceso con resultado, motivo y una medición parcial del procesamiento; la simulación no mide un torniquete físico ni el recorrido de red.

---

### Consulta 3: Concurrencia y Lista de Espera FIFO en Clases Grupales
* **Objetivo:** Evitar sobrecupos ante solicitudes simultáneas y automatizar la reasignación de cupos al cancelar una reserva.
* **Resumen de la consulta (por verificar):**
  > Diseña la lógica de reserva de clases para FitLife. Debe evitar reservas duplicadas y sobrecupos incluso si dos personas reservan al mismo tiempo. Si la clase se llena, el socio debe quedar en lista de espera FIFO. Al cancelar una reserva confirmada, el cupo debe asignarse automáticamente al siguiente en espera.
* **Resultado obtenido y aplicado:**
  - Uso de `transaction.atomic()` y `select_for_update()` en Django para bloqueo a nivel de fila y prevención de condiciones de carrera (*race conditions*).
  - Selección por fecha de reserva y, en caso de empate, identificador, verificando la elegibilidad antes de promocionar.

---

### Consulta 4: Frontend Responsivo y Aforo Dinámico
* **Objetivo:** Maquetar la interfaz gráfica de FitLife según la imagen de referencia (login split-screen, panel de administrador y panel de recepción con aforo).
* **Resumen de la consulta (por verificar):**
  > Genera la estructura HTML, CSS y JavaScript para FitLife con identidad visual deportiva (azul marino, turquesa y verde lima), con vistas separadas para Administrador y Recepción. El aforo debe actualizarse automáticamente cada 5 segundos mediante sondeo AJAX sin recargar la página.
* **Resultado obtenido y aplicado:**
  - `static/css/fitlife.css` con variables CSS, tarjetas de indicadores y visualización circular de aforo.
  - `static/js/fitlife.js` con función `setInterval(actualizarAforo, 5000)` y simulador interactivo de validación QR.

---

### Consulta 5: Comando de Carga de Datos Realistas
* **Objetivo:** Poblar la base de datos con información verosímil del entorno chileno para recorrer la aplicación en la defensa en vivo.
* **Resumen de la consulta (por verificar):**
  > Crea un comando de gestión en Django (`cargar_datos_demo`) que cree planes, profesores, 13 socios con nombres chilenos realistas (RUT, teléfonos y correos ficticios), clases de hoy con una llena y dos personas en lista de espera, entradas/salidas para aforo y usuarios demo para los roles Administrador y Recepción sin exponer contraseñas inseguras.
* **Resultado obtenido y aplicado:**
  - Comando `python manage.py cargar_datos_demo` que se ejecuta únicamente en una base sin datos de FitLife y no modifica contraseñas.
  - Datos de prueba con casos específicos: socio activo dentro, socio fuera, membresía vencida, membresía congelada y socio dado de baja lógica.

---

## 3. Apoyo posterior de Codex

Constanza utilizó Codex para revisar los documentos de la evaluación, preparar un prompt inicial y generar propuestas visuales. Una petición real de esta conversación fue: “pero antes de de empezar con aws, te subo el proyecto de nuevo entero para que lo analices y revises si nos falta algo”. Posteriormente pidió corregir los cuatro puntos detectados en esa revisión.

El apoyo incluyó revisión del proyecto, mejoras de formularios, roles, eliminación lógica, anulación de pagos, control de acceso, reservas, lista de espera y página 404; también se agregaron pruebas automatizadas. Constanza ejecutó las pruebas en PostgreSQL y comunicó que las 23 pruebas terminaron en `OK`, incluidas las comprobaciones de concurrencia.

El registro debe completarse contrastando los resúmenes anteriores con el historial original de Antigravity y añadiendo las demás consultas que se hayan realizado. La estudiante debe revisar y comprender el código y las decisiones antes de la defensa.
