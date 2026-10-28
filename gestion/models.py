"""
Modelos del dominio para FitLife - Plataforma de Control de Asistencia y Reserva de Clases.
Incluye manejo de socios, planes, membresías, pagos, accesos por QR, clases y reservas.
"""

from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
import uuid


# ------------------------------------------------------------------------------
# Manejador personalizado para Soporte de Eliminación Lógica (Soft Delete)
# ------------------------------------------------------------------------------
class ActiveManager(models.Manager):
    """
    Retorna por defecto solo los registros que NO han sido eliminados lógicamente (activo=True).
    Permite preservar el historial de pagos, reservas y accesos sin romper relaciones de base de datos.
    """
    def get_queryset(self):
        return super().get_queryset().filter(activo=True)


class SoftDeleteModel(models.Model):
    """
    Clase abstracta que provee campos y métodos estándar para eliminación lógica.
    """
    activo = models.BooleanField(
        default=True,
        verbose_name="Activo",
        help_text="Indica si el registro está vigente. Al desactivar, se aplica borrado lógico."
    )
    fecha_eliminacion = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha de eliminación",
        help_text="Fecha y hora en que se ejecutó la baja lógica."
    )

    objects = ActiveManager()  # Filtra activos por defecto en las consultas habituales
    all_objects = models.Manager()  # Permite consultar todos (incluyendo eliminados) desde el Admin

    class Meta:
        abstract = True

    def soft_delete(self):
        """Marca el registro como inactivo y guarda la fecha de baja."""
        self.activo = False
        self.fecha_eliminacion = timezone.now()
        self.save(update_fields=['activo', 'fecha_eliminacion'])

    def restore(self):
        """Restaura un registro previamente dado de baja lógica."""
        self.activo = True
        self.fecha_eliminacion = None
        self.save(update_fields=['activo', 'fecha_eliminacion'])


# ------------------------------------------------------------------------------
# 1. Módulo de Socios, Planes, Membresías y Pagos
# ------------------------------------------------------------------------------

def generar_codigo_qr():
    """Genera un código QR identificador único y legible para el socio."""
    return f"FL-{uuid.uuid4().hex[:8].upper()}"


class Socio(SoftDeleteModel):
    """
    Representa a una persona afiliada al gimnasio FitLife.
    """
    rut = models.CharField(
        max_length=12,
        unique=True,
        verbose_name="RUT",
        help_text="Formato estándar chileno (ej. 12.345.678-9 o 12345678-9)"
    )
    nombres = models.CharField(max_length=100, verbose_name="Nombres")
    apellidos = models.CharField(max_length=100, verbose_name="Apellidos")
    email = models.EmailField(unique=True, verbose_name="Correo electrónico")
    telefono = models.CharField(max_length=20, verbose_name="Teléfono móvil")
    fecha_nacimiento = models.DateField(null=True, blank=True, verbose_name="Fecha de nacimiento")
    fecha_registro = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de inscripción")

    # Identificador único para el código QR físico o virtual
    codigo_qr = models.CharField(
        max_length=30,
        unique=True,
        default=generar_codigo_qr,
        verbose_name="Código QR / Token",
        help_text="Código único asignado para validar acceso en torniquete o mesón."
    )

    class Meta:
        verbose_name = "Socio"
        verbose_name_plural = "Socios"
        ordering = ['apellidos', 'nombres']

    def __str__(self):
        return f"{self.nombres} {self.apellidos} ({self.rut})"

    @property
    def nombre_completo(self):
        return f"{self.nombres} {self.apellidos}"

    @property
    def iniciales(self):
        primera_n = self.nombres[0] if self.nombres else ""
        primera_a = self.apellidos[0] if self.apellidos else ""
        return f"{primera_n}{primera_a}".upper()

    def membresia_vigente(self):
        """Retorna la membresía activa más reciente si existe."""
        hoy = timezone.localdate()
        return self.membresias.filter(
            activo=True,
            plan__activo=True,
            estado='ACTIVA',
            fecha_inicio__lte=hoy,
            fecha_fin__gte=hoy
        ).order_by('-fecha_fin').first()

    def esta_dentro(self):
        """Determina si el socio se encuentra actualmente dentro del gimnasio."""
        ultimo_acceso = self.accesos.filter(
            resultado='PERMITIDO'
        ).order_by('-fecha_hora', '-id').first()
        return bool(ultimo_acceso and ultimo_acceso.tipo == 'ENTRADA')


class Plan(SoftDeleteModel):
    """
    Define los tipos de planes que ofrece el centro deportivo FitLife.
    Ejemplo: Básico, Plus, Premium, Élite.
    """
    nombre = models.CharField(max_length=80, unique=True, verbose_name="Nombre del plan")
    descripcion = models.TextField(blank=True, verbose_name="Descripción")
    duracion_dias = models.PositiveIntegerField(
        default=30,
        verbose_name="Duración en días",
        help_text="30 para mensual, 90 trimestral, 365 anual."
    )
    precio = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        verbose_name="Precio (CLP)",
        help_text="Valor en pesos chilenos."
    )
    permite_clases = models.BooleanField(
        default=True,
        verbose_name="Permite reservar clases",
        help_text="Indica si incluye acceso a clases grupales (Spinning, Yoga, etc.)."
    )

    class Meta:
        verbose_name = "Plan"
        verbose_name_plural = "Planes"
        ordering = ['precio']

    def __str__(self):
        return f"{self.nombre} - ${self.precio:,.0f} CLP ({self.duracion_dias} días)"


class Membresia(SoftDeleteModel):
    """
    Contrato o suscripción de un socio a un plan determinado.
    Maneja los tres estados requeridos: ACTIVA, VENCIDA y CONGELADA.
    """
    ESTADO_CHOICES = [
        ('ACTIVA', 'Activa'),
        ('VENCIDA', 'Vencida'),
        ('CONGELADA', 'Congelada'),
    ]

    socio = models.ForeignKey(
        Socio,
        on_delete=models.PROTECT,
        related_name='membresias',
        verbose_name="Socio"
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name='membresias',
        verbose_name="Plan"
    )
    fecha_inicio = models.DateField(default=timezone.now, verbose_name="Fecha de inicio")
    fecha_fin = models.DateField(verbose_name="Fecha de término")
    estado = models.CharField(
        max_length=15,
        choices=ESTADO_CHOICES,
        default='ACTIVA',
        verbose_name="Estado de membresía"
    )
    motivo_congelacion = models.TextField(
        blank=True,
        null=True,
        verbose_name="Motivo de congelamiento",
        help_text="Razón médica, laboral o de viaje para pausar la membresía."
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de registro")

    class Meta:
        verbose_name = "Membresía"
        verbose_name_plural = "Membresías"
        ordering = ['-fecha_fin']

    def __str__(self):
        return f"Membresía {self.plan.nombre} - {self.socio.nombre_completo} [{self.get_estado_display()}]"

    @property
    def estado_actual_display(self):
        """Estado visible calculado según la fecha, sin modificar el registro al consultarlo."""
        if self.estado != 'CONGELADA' and self.fecha_fin < timezone.localdate():
            return 'Vencida'
        return self.get_estado_display()

    def clean(self):
        """Valida coherencia de fechas."""
        if self.fecha_fin and self.fecha_inicio and self.fecha_fin < self.fecha_inicio:
            raise ValidationError("La fecha de término no puede ser anterior a la fecha de inicio.")
        if self.pk:
            anterior = Membresia.all_objects.filter(pk=self.pk).first()
            if anterior and self.pagos.exists() and (anterior.socio_id != self.socio_id or anterior.plan_id != self.plan_id):
                raise ValidationError('No se puede cambiar el socio o el plan de una membresía con pagos registrados. Crea una nueva membresía para conservar el historial.')
            if anterior and (self.estado != 'ACTIVA' or (self.fecha_fin and self.fecha_fin < anterior.fecha_fin)):
                reservas = Reserva.objects.filter(socio_id=anterior.socio_id, clase__fecha__gte=timezone.localdate(), estado__in=['CONFIRMADA', 'LISTA_ESPERA'])
                if reservas.exists():
                    raise ValidationError('Este socio tiene reservas próximas. Cancélalas antes de congelar, vencer o acortar la membresía.')

    def actualizar_estado(self):
        """
        Aplica la regla de negocio: si la fecha fin expiró y no está congelada,
        se pasa automáticamente a VENCIDA.
        """
        hoy = timezone.localdate()
        if self.estado != 'CONGELADA':
            if self.fecha_fin < hoy:
                if self.estado != 'VENCIDA':
                    self.estado = 'VENCIDA'
                    self.save(update_fields=['estado'])

    def puede_ingresar(self):
        """
        Evalúa si la membresía autoriza el ingreso al recinto.
        Retorna (autorizado: bool, motivo: str).
        """
        self.actualizar_estado()
        if not self.activo:
            return False, "La membresía ha sido dada de baja del sistema."
        if not self.socio.activo:
            return False, "El socio se encuentra desactivado en el sistema."
        if not self.plan.activo:
            return False, "El plan se encuentra desactivado."
        if self.estado == 'CONGELADA':
            return False, f"Membresía congelada temporalmente ({self.motivo_congelacion or 'Sin motivo detallado'})."
        if self.estado == 'VENCIDA':
            return False, f"Membresía vencida el {self.fecha_fin.strftime('%d/%m/%Y')}."
        if self.estado == 'ACTIVA':
            hoy = timezone.localdate()
            if self.fecha_inicio > hoy:
                return False, f"La membresía inicia el {self.fecha_inicio.strftime('%d/%m/%Y')}."
            return True, "Acceso permitido. Membresía activa."
        return False, "Estado de membresía no válido."


class Pago(models.Model):
    """
    Registro inmutable de pagos realizados por los socios.
    No utiliza borrado lógico para asegurar la integridad de la auditoría financiera.
    """
    METODOS_PAGO = [
        ('EFECTIVO', 'Efectivo'),
        ('TRANSFERENCIA', 'Transferencia bancaria'),
        ('TARJETA_DEBITO', 'Tarjeta de Débito'),
        ('TARJETA_CREDITO', 'Tarjeta de Crédito'),
    ]

    membresia = models.ForeignKey(
        Membresia,
        on_delete=models.PROTECT,
        related_name='pagos',
        verbose_name="Membresía asociada"
    )
    monto = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        verbose_name="Monto pagado (CLP)"
    )
    fecha_pago = models.DateTimeField(default=timezone.now, verbose_name="Fecha y hora de pago")
    metodo_pago = models.CharField(
        max_length=20,
        choices=METODOS_PAGO,
        default='TRANSFERENCIA',
        verbose_name="Método de pago"
    )
    numero_comprobante = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="N° Comprobante / Boleta"
    )
    observaciones = models.TextField(blank=True, verbose_name="Observaciones")
    anulado = models.BooleanField(default=False, verbose_name="Anulado")
    motivo_anulacion = models.TextField(blank=True, verbose_name="Motivo de anulación")
    fecha_anulacion = models.DateTimeField(null=True, blank=True, verbose_name="Fecha de anulación")
    anulado_por = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='pagos_anulados', verbose_name='Anulado por')

    class Meta:
        verbose_name = "Pago"
        verbose_name_plural = "Pagos"
        ordering = ['-fecha_pago']

    def __str__(self):
        return f"Pago #{self.id} - ${self.monto:,.0f} CLP ({self.membresia.socio.nombre_completo})"


# ------------------------------------------------------------------------------
# 2. Módulo de Control de Acceso por QR y Aforo
# ------------------------------------------------------------------------------

class RegistroAcceso(models.Model):
    """
    Registro de validación de torniquete/recepción mediante código QR o ID manual.
    Almacena latencia de respuesta en milisegundos y motivo de rechazo en caso denegado.
    Es un registro inmutable de auditoría.
    """
    TIPO_CHOICES = [
        ('ENTRADA', 'Ingreso / Entrada'),
        ('SALIDA', 'Egreso / Salida'),
    ]
    RESULTADO_CHOICES = [
        ('PERMITIDO', 'Permitido'),
        ('DENEGADO', 'Denegado'),
    ]

    socio = models.ForeignKey(
        Socio,
        on_delete=models.PROTECT,
        related_name='accesos',
        verbose_name="Socio"
    )
    tipo = models.CharField(
        max_length=10,
        choices=TIPO_CHOICES,
        default='ENTRADA',
        verbose_name="Tipo de movimiento"
    )
    resultado = models.CharField(
        max_length=15,
        choices=RESULTADO_CHOICES,
        verbose_name="Resultado del acceso"
    )
    motivo = models.CharField(
        max_length=255,
        verbose_name="Motivo o detalle",
        help_text="Explicación si el acceso fue permitido o denegado."
    )
    fecha_hora = models.DateTimeField(
        default=timezone.now,
        verbose_name="Fecha y hora del registro"
    )
    tiempo_respuesta_ms = models.FloatField(
        default=0.0,
        verbose_name="Latencia (ms)",
        help_text="Tiempo de procesamiento en el servidor en milisegundos."
    )
    metodo = models.CharField(
        max_length=20,
        default='SIMULACION_QR',
        verbose_name="Método de lectura",
        help_text="SIMULACION_QR, SCANNER_CAMARA, MANUAL_ID"
    )

    class Meta:
        verbose_name = "Registro de Acceso"
        verbose_name_plural = "Registros de Acceso"
        ordering = ['-fecha_hora']

    def __str__(self):
        return f"{self.fecha_hora.strftime('%H:%M:%S')} - {self.socio.nombre_completo} [{self.tipo}: {self.resultado}]"


# ------------------------------------------------------------------------------
# 3. Módulo de Clases Grupales, Profesores y Reservas con Lista de Espera
# ------------------------------------------------------------------------------

class Profesor(SoftDeleteModel):
    """
    Entrenadores e instructores a cargo de las clases grupales.
    """
    nombres = models.CharField(max_length=100, verbose_name="Nombres")
    apellidos = models.CharField(max_length=100, verbose_name="Apellidos")
    especialidad = models.CharField(
        max_length=100,
        verbose_name="Especialidad",
        help_text="Ej: Spinning, Yoga, Pilates, Crossfit, Zumba"
    )
    email = models.EmailField(verbose_name="Correo electrónico", blank=True)
    telefono = models.CharField(max_length=20, verbose_name="Teléfono", blank=True)

    class Meta:
        verbose_name = "Profesor"
        verbose_name_plural = "Profesores"
        ordering = ['apellidos', 'nombres']

    def __str__(self):
        return f"{self.nombres} {self.apellidos} ({self.especialidad})"


class Clase(SoftDeleteModel):
    """
    Sesión de clase grupal con horario, instructor y capacidad máxima.
    """
    nombre = models.CharField(
        max_length=100,
        verbose_name="Nombre de la clase",
        help_text="Ej: Spinning Matutino, Yoga Restaurativo, Funcional Power"
    )
    profesor = models.ForeignKey(
        Profesor,
        on_delete=models.PROTECT,
        related_name='clases',
        verbose_name="Profesor / Instructor"
    )
    fecha = models.DateField(default=timezone.now, verbose_name="Fecha de la clase")
    hora_inicio = models.TimeField(verbose_name="Hora de inicio")
    hora_fin = models.TimeField(verbose_name="Hora de término")
    capacidad_maxima = models.PositiveIntegerField(
        default=20,
        verbose_name="Capacidad máxima (Cupos)"
    )
    sala = models.CharField(
        max_length=50,
        default="Sala Principal",
        verbose_name="Sala / Ubicación"
    )

    class Meta:
        verbose_name = "Clase grupal"
        verbose_name_plural = "Clases grupales"
        ordering = ['fecha', 'hora_inicio']

    def __str__(self):
        return f"{self.nombre} - {self.fecha.strftime('%d/%m')} {self.hora_inicio.strftime('%H:%M')} ({self.profesor.nombres})"

    @property
    def duracion_minutos(self):
        """Calcula la duración en minutos de la clase."""
        inicio = self.hora_inicio.hour * 60 + self.hora_inicio.minute
        fin = self.hora_fin.hour * 60 + self.hora_fin.minute
        return max(fin - inicio, 0)

    def total_confirmadas(self):
        """Retorna la cantidad de cupos ocupados con reserva confirmada."""
        return self.reservas.filter(estado='CONFIRMADA').count()

    def cupos_disponibles(self):
        """Calcula los cupos libres restantes."""
        libres = self.capacidad_maxima - self.total_confirmadas()
        return max(libres, 0)

    def esta_llena(self):
        """Indica si la clase ha alcanzado su capacidad máxima."""
        return self.cupos_disponibles() == 0

    def total_en_espera(self):
        """Retorna la cantidad de socios en lista de espera."""
        return self.reservas.filter(estado='LISTA_ESPERA').count()


class Reserva(models.Model):
    """
    Reserva de un socio en una clase grupal.
    Soporta confirmación inmediata, lista de espera con orden FIFO y cancelación con promoción automática.
    """
    ESTADO_RESERVA = [
        ('CONFIRMADA', 'Confirmada'),
        ('LISTA_ESPERA', 'Lista de espera'),
        ('CANCELADA', 'Cancelada'),
    ]

    clase = models.ForeignKey(
        Clase,
        on_delete=models.CASCADE,
        related_name='reservas',
        verbose_name="Clase"
    )
    socio = models.ForeignKey(
        Socio,
        on_delete=models.CASCADE,
        related_name='reservas',
        verbose_name="Socio"
    )
    estado = models.CharField(
        max_length=15,
        choices=ESTADO_RESERVA,
        default='CONFIRMADA',
        verbose_name="Estado de reserva"
    )
    fecha_reserva = models.DateTimeField(
        default=timezone.now,
        verbose_name="Fecha y hora de solicitud"
    )
    fecha_actualizacion = models.DateTimeField(
        auto_now=True,
        verbose_name="Última actualización"
    )

    class Meta:
        verbose_name = "Reserva"
        verbose_name_plural = "Reservas"
        ordering = ['fecha_reserva']
        # Evita que un socio reserve dos veces la misma clase si está activa
        constraints = [
            models.UniqueConstraint(
                fields=['clase', 'socio'],
                condition=models.Q(estado__in=['CONFIRMADA', 'LISTA_ESPERA']),
                name='reserva_unica_por_socio_activa'
            )
        ]

    def __str__(self):
        return f"Reserva #{self.id}: {self.socio.nombre_completo} -> {self.clase.nombre} [{self.get_estado_display()}]"
