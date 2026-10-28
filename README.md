# FitLife - Plataforma de Control de Asistencia y Reserva de Clases

> **Asignatura:** Desarrollo Backend  
> **Evaluación:** Evaluación Parcial N°3 (EVA 3) - Ponderación 30%  
> **Estudiante:** Constanza Gajardo  
> **Docente:** Marcelo Alvarado Aravena  
> **Framework:** Django 5.x / Django REST Framework  
> **Base de Datos:** PostgreSQL 17  

---

## 🌐 Despliegue en AWS Cloud (EC2)

* **Instancia AWS EC2:** En proceso de configuración.
* **IP Pública / DNS Funcional:** `http://[IP_PUBLICA_AWS_AQUI]:8000/` *(Se agregará la dirección definitiva tras el despliegue asistido en AWS Academy)*.
* **Acceso Django Admin:** `http://[IP_PUBLICA_AWS_AQUI]:8000/admin/`

---

## 📋 Descripción del Proyecto y Problemática

El centro deportivo **FitLife** experimentó una saturación operativa debido a su crecimiento: congestión en el acceso principal, sobrecupo en clases grupales de alta demanda (Spinning, Funcional, Yoga) e ingreso indebido de usuarios con membresías vencidas o congeladas.

FitLife resuelve esta problemática mediante:
1. **Control de Membresías y Pagos:** Gestión de socios y contratos con control automático de estados (`Activa`, `Vencida`, `Congelada`) y borrado lógico para auditoría.
2. **Validación de Acceso QR:** Simulador de torniquete con medición de latencia en milisegundos y control de consistencia (evita doble entrada consecutiva).
3. **Reserva Inteligente de Clases:** Control de concurrencia atómico para evitar sobrecupos y gestión de **lista de espera automatizada (FIFO)** con promoción de cupos en tiempo real al cancelar.
4. **Dashboard de aforo con actualización periódica:** Cálculo dinámico de ocupación actual mediante entradas y salidas registradas, con actualización automática vía sondeo AJAX cada 5 segundos.
5. **Seguridad Basada en Roles:** Separación estricta entre **Administrador** (gestión financiera, socios, clases, Django Admin) y **Recepción** (atención rápida, validación QR, aforo y clases).

---

## 🏛️ Arquitectura del Sistema (Paradigma 4+1)

* **Vista de Casos de Uso (+1):** Escenarios del personal del gimnasio (recepción valida acceso, socio reserva clase con lista de espera, administrador audita finanzas).
* **Vista Lógica:** Capa de datos relacional con Django ORM (`Socio`, `Plan`, `Membresia`, `Pago`, `Profesor`, `Clase`, `Reserva`, `RegistroAcceso`) y capa de servicios desacoplada (`gestion/services.py`).
* **Vista de Procesos:** Concurrencia controlada con transacciones atómicas (`select_for_update()`) para evitar sobreventa de cupos en reservas simultáneas.
* **Vista de Desarrollo:** Estructura modular Django con separación en apps, serializers REST, templates HTML responsivos y archivos estáticos desacoplados.
* **Vista Física:** Despliegue previsto en una instancia AWS EC2 con PostgreSQL en la misma instancia; pendiente de verificación.

---

## 🚀 Requisitos e Instalación Local

### 1. Prerrequisitos
* Python 3.11 o superior.
* PostgreSQL 15, 16 o 17 instalado y en ejecución en el puerto 5432.
* Git.

### 2. Clonación y Creación del Entorno Virtual
```bash
# Clonar el repositorio
git clone <URL_REPOSITORIO>
cd Fitlife

# Crear y activar entorno virtual
python -m venv venv

# En Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# En Linux / Mac / Git Bash:
source venv/bin/activate
```

### 3. Instalación de Dependencias
```bash
pip install -r requirements.txt
```

### 4. Configuración de Variables de Entorno (`.env`)
Copia el archivo de ejemplo y configura tus credenciales locales de PostgreSQL:
```bash
# En Windows (PowerShell):
Copy-Item .env.example .env
```
Edita `.env` en tu editor de código (ej. VS Code) asegurando tu contraseña de PostgreSQL:
```env
SECRET_KEY=clave-secreta-desarrollo-fitlife
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

DB_NAME=fitlife_db
DB_USER=postgres
DB_PASSWORD=tu_contraseña_aqui
DB_HOST=localhost
DB_PORT=5432

AFORO_MAXIMO=200
POLLING_INTERVAL_SECONDS=5
```

### 5. Creación de la Base de Datos en PostgreSQL
Abre **SQL Shell (psql)** o **pgAdmin 4** y ejecuta:
```sql
CREATE DATABASE fitlife_db;
```

### 6. Aplicar Migraciones del ORM
```bash
python manage.py migrate
```

### 7. Carga de Datos Realistas de Demostración
Puebla la base de datos con socios chilenos realistas, planes, profesores, clases y registros de aforo:
```bash
python manage.py cargar_datos_demo
```
Esta carga solo funciona si las tablas de FitLife están vacías. Si ya hay datos, se detiene sin modificarlos. No crea usuarios ni cambia contraseñas. Las clases de ejemplo se programan para el día de la carga.

### 8. Ejecución del Servidor de Desarrollo
```bash
python manage.py runserver
```
Ingresa a tu navegador en: **`http://127.0.0.1:8000/`**

---

## 🔑 Usuarios y roles

Crea una cuenta administradora mediante `python manage.py createsuperuser` y elige una contraseña privada. Desde Django Admin puedes crear la cuenta de recepción y asignarla al grupo `Recepcion`, sin marcarla como personal (`staff`) ni superusuario. El comando de carga prepara los grupos, pero no crea cuentas. No publiques contraseñas en el repositorio. Si existen cuentas creadas por versiones anteriores de la carga, cambia sus contraseñas antes de publicar el sistema.

---

## 🧪 Ejecución de Pruebas Automatizadas

El proyecto incluye pruebas automatizadas de reglas de negocio, permisos y concurrencia. La estudiante ejecutó 23 pruebas en PostgreSQL con resultado `OK`. Estas pruebas no sustituyen la comprobación del despliegue y la defensa:
```bash
python manage.py test gestion
```
*Pruebas cubiertas:*
- ✅ Validación QR para membresía activa (Permitido).
- ✅ Bloqueo de acceso por membresía vencida o congelada (Denegado explicativo).
- ✅ Prevención de doble entrada consecutiva (Consistencia de torniquete).
- ✅ Manejo de cupos máximos y lista de espera FIFO.
- ✅ Promoción automática de lista de espera al cancelar cupo confirmado.
- ✅ Eliminación lógica (`soft_delete`) y conservación de relaciones de auditoría.
- ✅ Cálculo del aforo y actualización periódica en la interfaz.

---

## Preparación de entrega y producción

- El repositorio debe contener el código fuente, migraciones, `.env.example`, este README y `prompts_ia.md`. No incluir `.env`, contraseñas ni `venv/`.
- El ZIP utilizado en desarrollo contenía `.env` y `venv/`; no subir ese ZIP tal como está a un repositorio público. Revisar también los archivos ya rastreados por Git.
- Cambiar las contraseñas de cuentas de demostración creadas por versiones anteriores. No documentar las nuevas contraseñas en el repositorio.
- AWS requiere `DEBUG=False`, una clave secreta nueva y privada, hosts permitidos y configuración segura; faltan los pasos concretos de instalación, servicio de aplicación y archivos estáticos en EC2.
- Actualizar este README con la IP o DNS real después de comprobar que la aplicación funciona públicamente. El despliegue sigue pendiente.

## 📁 Estructura del Proyecto

```
Fitlife/
├── fitlife_project/          # Configuración global del proyecto Django
│   ├── settings.py           # Conexión PostgreSQL, .env y parámetros
│   ├── urls.py               # Enrutador principal y handler 404
│   ├── wsgi.py
│   └── asgi.py
├── gestion/                  # Aplicación de dominio de FitLife
│   ├── models.py             # Modelos ORM relacionales y SoftDelete
│   ├── admin.py              # Django Admin personalizado con badges y acciones
│   ├── services.py           # Lógica de negocio (QR, Aforo, Lista de Espera)
│   ├── views.py              # Vistas web y controladores API DRF
│   ├── serializers.py        # Serializadores Django REST Framework
│   ├── urls.py               # Rutas web y endpoints API
│   ├── tests.py              # Suite de pruebas unitarias
│   └── management/
│       └── commands/
│           └── cargar_datos_demo.py  # Carga reproducible de datos demo
├── static/
│   ├── css/fitlife.css       # Estilos visuales acordes a maqueta FitLife
│   └── js/fitlife.js         # Polling AJAX de aforo y simulador QR
├── templates/
│   ├── base.html             # Layout base con sidebar según rol
│   ├── login.html            # Pantalla de login deportiva split-screen
│   ├── admin_dashboard.html  # Panel de administración (KPIs y finanzas)
│   ├── recepcion_dashboard.html # Panel operativo de recepción
│   ├── control_acceso.html   # Simulador de lector QR con latencia
│   ├── 404.html              # Página 404 personalizada de FitLife
│   ├── socios/               # Vistas de socios y confirmación de baja lógica
│   └── clases/               # Vistas de clases, reservas y lista de espera
├── .env.example              # Archivo de variables de entorno de ejemplo
├── .gitignore                # Reglas para no subir .env ni venv a GitHub
├── requirements.txt          # Dependencias del proyecto
├── prompts_ia.md             # Registro y evidencia del uso de IA (Criterio 4)
└── README.md                 # Documentación técnica completa
```
