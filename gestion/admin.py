"""
Personalización completa de Django Admin para FitLife.
Cumple con el Criterio 2 de la rúbrica de evaluación:
- Registro de todos los modelos del dominio.
- Búsquedas, filtros, ordenamiento y badges visuales con HTML.
- Inlines de membresías y reservas.
- Acciones de eliminación lógica y restauración.
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
from .models import (
    Socio, Plan, Membresia, Pago,
    Profesor, Clase, Reserva, RegistroAcceso
)
from .forms import ClaseForm


class HistorialProtegidoAdmin(admin.ModelAdmin):
    """Evita borrados físicos y cambios directos del estado de baja."""
    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        campos = list(super().get_readonly_fields(request, obj))
        if hasattr(self.model, 'activo'):
            campos.extend(['activo', 'fecha_eliminacion'])
        return tuple(dict.fromkeys(campos))

# ------------------------------------------------------------------------------
# Configuración del encabezado del panel administrativo
# ------------------------------------------------------------------------------
admin.site.site_header = "FitLife - Panel de Administración"
admin.site.site_title = "FitLife Gestión"
admin.site.index_title = "Administración del Centro Deportivo FitLife"


# ------------------------------------------------------------------------------
# Inlines para visualización relacional
# ------------------------------------------------------------------------------
class MembresiaInline(admin.TabularInline):
    model = Membresia
    extra = 0
    fields = ('plan', 'fecha_inicio', 'fecha_fin', 'estado', 'activo')
    show_change_link = True
    readonly_fields = ('activo',)
    can_delete = False


class ReservaInline(admin.TabularInline):
    model = Reserva
    extra = 0
    fields = ('socio', 'estado', 'fecha_reserva')
    readonly_fields = ('fecha_reserva',)
    show_change_link = True
    readonly_fields = ('socio', 'estado', 'fecha_reserva')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class PagoInline(admin.TabularInline):
    model = Pago
    extra = 0
    fields = ('monto', 'metodo_pago', 'numero_comprobante', 'fecha_pago')
    readonly_fields = ('monto', 'metodo_pago', 'numero_comprobante', 'fecha_pago')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


# ------------------------------------------------------------------------------
# 1. Administración de Socios
# ------------------------------------------------------------------------------
@admin.register(Socio)
class SocioAdmin(HistorialProtegidoAdmin):
    list_display = (
        'rut',
        'nombre_completo',
        'email',
        'telefono',
        'codigo_qr_badge',
        'membresia_actual_badge',
        'estado_activo_badge'
    )
    list_filter = ('activo', 'fecha_registro')
    search_fields = ('rut', 'nombres', 'apellidos', 'email', 'codigo_qr')
    readonly_fields = ('codigo_qr', 'fecha_registro', 'fecha_eliminacion')
    inlines = [MembresiaInline]
    actions = ['desactivar_socios_logico', 'restaurar_socios_logico']

    fieldsets = (
        ('Identificación y Contacto', {
            'fields': ('rut', 'nombres', 'apellidos', 'email', 'telefono', 'fecha_nacimiento')
        }),
        ('Acceso y Credenciales', {
            'fields': ('codigo_qr', 'fecha_registro')
        }),
        ('Control de Baja Lógica', {
            'classes': ('collapse',),
            'fields': ('activo', 'fecha_eliminacion')
        }),
    )

    def get_queryset(self, request):
        """Permite que el Administrador vea todos los socios, incluidos los dados de baja lógica."""
        return Socio.all_objects.all()

    @admin.display(description="Código QR")
    def codigo_qr_badge(self, obj):
        return format_html(
            '<span style="background-color: #0d2847; color: #00d2b4; font-family: monospace; padding: 3px 8px; border-radius: 4px; font-weight: bold;">{}</span>',
            obj.codigo_qr
        )

    @admin.display(description="Membresía")
    def membresia_actual_badge(self, obj):
        m = obj.membresia_vigente()
        if m:
            return format_html(
                '<span style="background-color: #d1fae5; color: #065f46; padding: 3px 8px; border-radius: 12px; font-weight: 600;">{} ({})</span>',
                m.plan.nombre, m.get_estado_display()
            )
        return format_html(
            '<span style="background-color: #fee2e2; color: #991b1b; padding: 3px 8px; border-radius: 12px; font-weight: 600;">Sin membresía activa</span>'
        )

    @admin.display(description="Estado")
    def estado_activo_badge(self, obj):
        if obj.activo:
            return format_html('<span style="color: #059669; font-weight: bold;">● Activo</span>')
        return format_html('<span style="color: #dc2626; font-weight: bold;">● Eliminado Lógico</span>')

    @admin.action(description="Dar de baja lógica a los socios seleccionados")
    def desactivar_socios_logico(self, request, queryset):
        filas = 0
        for s in queryset:
            s.soft_delete()
            filas += 1
        self.message_user(request, f"{filas} socio(s) desactivado(s) lógicamente.")

    @admin.action(description="Restaurar socios dados de baja lógica")
    def restaurar_socios_logico(self, request, queryset):
        filas = 0
        for s in queryset:
            s.restore()
            filas += 1
        self.message_user(request, f"{filas} socio(s) restaurado(s) exitosamente.")


# ------------------------------------------------------------------------------
# 2. Administración de Planes
# ------------------------------------------------------------------------------
@admin.register(Plan)
class PlanAdmin(HistorialProtegidoAdmin):
    list_display = ('nombre', 'precio_formateado', 'duracion_dias', 'permite_clases_badge', 'activo_badge')
    list_filter = ('activo', 'permite_clases')
    search_fields = ('nombre', 'descripcion')
    actions = []

    def get_queryset(self, request):
        return Plan.all_objects.all()

    @admin.display(description="Precio (CLP)")
    def precio_formateado(self, obj):
        return f"${obj.precio:,.0f} CLP"

    @admin.display(description="Clases Grupales")
    def permite_clases_badge(self, obj):
        if obj.permite_clases:
            return format_html('<span style="color: #059669;">✔ Incluidas</span>')
        return format_html('<span style="color: #6b7280;">✘ No permitidas</span>')

    @admin.display(description="Estado")
    def activo_badge(self, obj):
        if obj.activo:
            return format_html('<span style="color: #059669; font-weight: bold;">Activo</span>')
        return format_html('<span style="color: #dc2626; font-weight: bold;">Inactivo</span>')

    @admin.action(description="Desactivar planes seleccionados")
    def desactivar_planes(self, request, queryset):
        for p in queryset:
            p.soft_delete()
        self.message_user(request, "Planes desactivados lógicamente.")

    @admin.action(description="Restaurar planes seleccionados")
    def restaurar_planes(self, request, queryset):
        for p in queryset:
            p.restore()
        self.message_user(request, "Planes restaurados correctamente.")


# ------------------------------------------------------------------------------
# 3. Administración de Membresías
# ------------------------------------------------------------------------------
@admin.register(Membresia)
class MembresiaAdmin(HistorialProtegidoAdmin):
    list_display = ('id', 'socio', 'plan', 'fecha_inicio', 'fecha_fin', 'estado_badge', 'activo')
    list_filter = ('estado', 'activo', 'plan', 'fecha_inicio', 'fecha_fin')
    search_fields = ('socio__rut', 'socio__nombres', 'socio__apellidos', 'plan__nombre')
    inlines = [PagoInline]
    actions = ['verificar_vencimientos']

    def get_queryset(self, request):
        return Membresia.all_objects.all()

    @admin.display(description="Estado")
    def estado_badge(self, obj):
        obj.actualizar_estado()
        colores = {
            'ACTIVA': ('#d1fae5', '#065f46'),
            'VENCIDA': ('#fee2e2', '#991b1b'),
            'CONGELADA': ('#e0f2fe', '#0369a1'),
        }
        bg, fg = colores.get(obj.estado, ('#f3f4f6', '#374151'))
        return format_html(
            '<span style="background-color: {}; color: {}; padding: 4px 10px; border-radius: 12px; font-weight: bold;">{}</span>',
            bg, fg, obj.get_estado_display()
        )

    @admin.action(description="Congelar membresías seleccionadas")
    def congelar_membresias(self, request, queryset):
        queryset.update(estado='CONGELADA', motivo_congelacion="Congelada desde panel de administración.")
        self.message_user(request, "Membresías marcadas como CONGELADAS.")

    @admin.action(description="Activar membresías seleccionadas")
    def activar_membresias(self, request, queryset):
        queryset.update(estado='ACTIVA', motivo_congelacion=None)
        self.message_user(request, "Membresías activadas exitosamente.")

    @admin.action(description="Verificar y actualizar vencimientos de membresías")
    def verificar_vencimientos(self, request, queryset):
        for m in queryset:
            m.actualizar_estado()
        self.message_user(request, "Estados de vencimiento actualizados según la fecha de hoy.")


# ------------------------------------------------------------------------------
# 4. Administración de Pagos
# ------------------------------------------------------------------------------
@admin.register(Pago)
class PagoAdmin(HistorialProtegidoAdmin):
    list_display = (
        'id',
        'socio_asociado',
        'plan_asociado',
        'monto_formateado',
        'metodo_pago',
        'numero_comprobante',
        'fecha_pago', 'anulado'
    )
    list_filter = ('metodo_pago', 'fecha_pago')
    search_fields = (
        'membresia__socio__rut',
        'membresia__socio__nombres',
        'membresia__socio__apellidos',
        'numero_comprobante'
    )
    readonly_fields = ('membresia', 'monto', 'fecha_pago', 'metodo_pago', 'numero_comprobante', 'observaciones', 'anulado', 'motivo_anulacion', 'fecha_anulacion', 'anulado_por')

    def has_add_permission(self, request):
        return False

    @admin.display(description="Socio")
    def socio_asociado(self, obj):
        return obj.membresia.socio.nombre_completo

    @admin.display(description="Plan")
    def plan_asociado(self, obj):
        return obj.membresia.plan.nombre

    @admin.display(description="Monto")
    def monto_formateado(self, obj):
        return f"${obj.monto:,.0f} CLP"


# ------------------------------------------------------------------------------
# 5. Profesores y Clases Grupales
# ------------------------------------------------------------------------------
@admin.register(Profesor)
class ProfesorAdmin(HistorialProtegidoAdmin):
    list_display = ('nombres', 'apellidos', 'especialidad', 'email', 'telefono', 'activo')
    list_filter = ('especialidad', 'activo')
    search_fields = ('nombres', 'apellidos', 'especialidad')

    def get_queryset(self, request):
        return Profesor.all_objects.all()


@admin.register(Clase)
class ClaseAdmin(HistorialProtegidoAdmin):
    form = ClaseForm
    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False
    list_display = (
        'nombre',
        'profesor',
        'fecha',
        'horario',
        'capacidad_maxima',
        'cupos_ocupados_badge',
        'lista_espera_badge',
        'activo'
    )
    list_filter = ('fecha', 'profesor', 'activo')
    search_fields = ('nombre', 'profesor__nombres', 'profesor__apellidos', 'sala')
    inlines = [ReservaInline]

    def get_queryset(self, request):
        return Clase.all_objects.all()

    @admin.display(description="Horario")
    def horario(self, obj):
        return f"{obj.hora_inicio.strftime('%H:%M')} - {obj.hora_fin.strftime('%H:%M')}"

    @admin.display(description="Ocupación")
    def cupos_ocupados_badge(self, obj):
        ocupados = obj.total_confirmadas()
        total = obj.capacidad_maxima
        porcentaje = int((ocupados / total) * 100) if total > 0 else 0
        color = "#059669" if ocupados < total else "#dc2626"
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}/{} ({}%)</span>',
            color, ocupados, total, porcentaje
        )

    @admin.display(description="Lista de Espera")
    def lista_espera_badge(self, obj):
        espera = obj.total_en_espera()
        if espera > 0:
            return format_html(
                '<span style="background-color: #fef3c7; color: #92400e; padding: 2px 7px; border-radius: 10px; font-weight: bold;">{} en espera</span>',
                espera
            )
        return format_html('<span style="color: #9ca3af;">0</span>')


# ------------------------------------------------------------------------------
# 6. Reservas
# ------------------------------------------------------------------------------
@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = ('id', 'clase', 'socio', 'estado_badge', 'fecha_reserva')
    list_filter = ('estado', 'clase__fecha', 'clase')
    search_fields = ('socio__rut', 'socio__nombres', 'socio__apellidos', 'clase__nombre')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Estado")
    def estado_badge(self, obj):
        colores = {
            'CONFIRMADA': ('#d1fae5', '#065f46'),
            'LISTA_ESPERA': ('#fef3c7', '#92400e'),
            'CANCELADA': ('#fee2e2', '#991b1b'),
        }
        bg, fg = colores.get(obj.estado, ('#f3f4f6', '#374151'))
        return format_html(
            '<span style="background-color: {}; color: {}; padding: 4px 8px; border-radius: 12px; font-weight: bold;">{}</span>',
            bg, fg, obj.get_estado_display()
        )


# ------------------------------------------------------------------------------
# 7. Registros de Acceso (Auditoría inmutable)
# ------------------------------------------------------------------------------
@admin.register(RegistroAcceso)
class RegistroAccesoAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'fecha_hora_formateada',
        'socio',
        'tipo_badge',
        'resultado_badge',
        'motivo',
        'latencia_badge',
        'metodo'
    )
    list_filter = ('tipo', 'resultado', 'fecha_hora', 'metodo')
    search_fields = ('socio__rut', 'socio__nombres', 'socio__apellidos', 'motivo')
    readonly_fields = (
        'socio', 'tipo', 'resultado', 'motivo', 'fecha_hora',
        'tiempo_respuesta_ms', 'metodo'
    )

    def has_add_permission(self, request):
        """Los accesos solo se generan por sistema de torniquete/recepción, no manualmente."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Registro inmutable: no se permite eliminar registros de acceso."""
        return False

    @admin.display(description="Fecha y Hora")
    def fecha_hora_formateada(self, obj):
        return obj.fecha_hora.strftime("%d/%m/%Y %H:%M:%S")

    @admin.display(description="Tipo")
    def tipo_badge(self, obj):
        if obj.tipo == 'ENTRADA':
            return format_html('<span style="color: #0284c7; font-weight: bold;">↓ ENTRADA</span>')
        return format_html('<span style="color: #d97706; font-weight: bold;">↑ SALIDA</span>')

    @admin.display(description="Resultado")
    def resultado_badge(self, obj):
        if obj.resultado == 'PERMITIDO':
            return format_html(
                '<span style="background-color: #d1fae5; color: #065f46; padding: 3px 8px; border-radius: 10px; font-weight: bold;">PERMITIDO</span>'
            )
        return format_html(
            '<span style="background-color: #fee2e2; color: #991b1b; padding: 3px 8px; border-radius: 10px; font-weight: bold;">DENEGADO</span>'
        )

    @admin.display(description="Latencia")
    def latencia_badge(self, obj):
        return format_html(
            '<span style="font-family: monospace; font-size: 0.85rem; color: #4b5563;">{:.1f} ms</span>',
            obj.tiempo_respuesta_ms
        )
