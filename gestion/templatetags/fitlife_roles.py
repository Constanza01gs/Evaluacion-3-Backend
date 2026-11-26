from django import template

register = template.Library()


@register.filter
def es_administrador(user):
    return bool(user.is_authenticated and (user.is_superuser or user.groups.filter(name='Administrador').exists()))


@register.filter
def es_profesor(user):
    from gestion.models import Profesor
    return bool(user.is_authenticated and user.groups.filter(name='Profesor').exists() and Profesor.objects.filter(usuario=user).exists()
                and not user.is_superuser and not user.groups.filter(name__in=['Administrador', 'Recepcion']).exists())


@register.filter
def avisos_profesor_sin_leer(user):
    return bool(user.is_authenticated and user.notificaciones_profesor.filter(leida=False, oculta=False).exists())


@register.filter
def avisos_indisponibilidad_pendientes(user):
    if not es_administrador(user):
        return False
    from gestion.models import AvisoIndisponibilidad
    return AvisoIndisponibilidad.objects.filter(revisado_en__isnull=True).exists()


@register.simple_tag
def solicitudes_pendientes(user):
    if not es_administrador(user):
        return {}
    from django.db.models import Q
    from gestion.models import SolicitudCongelamiento, SolicitudCorreccionAsistencia, AvisoIndisponibilidad, SolicitudAnulacionPago
    return {
        'congelamiento': SolicitudCongelamiento.objects.filter(Q(estado='PENDIENTE') | Q(reactivacion_pendiente=True)).count(),
        'asistencia': SolicitudCorreccionAsistencia.objects.filter(estado='PENDIENTE').count(),
        'avisos': AvisoIndisponibilidad.objects.filter(revisado_en__isnull=True).count(),
        'pagos': SolicitudAnulacionPago.objects.filter(estado='PENDIENTE').count(),
    }
