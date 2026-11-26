"""
Capa de Servicios y Lógica de Negocio para FitLife.
Contiene las reglas de negocio aisladas y comprobables para:
1. Control de acceso QR con medición de latencia en milisegundos y consistencia de movimientos.
2. Gestión concurrente de reservas y lista de espera FIFO con promoción automática.
3. Cálculo en tiempo real de aforo y ocupación.
"""

import time
from datetime import datetime, timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ValidationError
from .models import Socio, Membresia, RegistroAcceso, Clase, Reserva, NotificacionSocio, HistorialAsistencia


@transaction.atomic
def solicitar_congelamiento_socio(membresia_id, usuario, inicio, fin, motivo=""):
    """Registra una solicitud del titular o del personal autorizado."""
    from .models import SolicitudCongelamiento
    membresia = Membresia.all_objects.select_for_update().select_related('socio').get(pk=membresia_id)
    hoy = timezone.localdate()
    personal = usuario.is_authenticated and usuario.is_active and (usuario.is_superuser or usuario.groups.filter(name__in=['Administrador', 'Recepcion']).exists())
    titular = usuario.is_authenticated and usuario.is_active and membresia.socio.usuario_id == usuario.pk
    if not membresia.socio.activo or not (titular or personal):
        raise ValidationError('Solo el titular activo o el personal autorizado pueden solicitar esta pausa.')
    if not membresia.activo or membresia.estado != 'ACTIVA' or not (membresia.fecha_inicio <= hoy <= membresia.fecha_fin):
        raise ValidationError('La membresía debe estar vigente para solicitar una pausa.')
    if inicio < hoy or fin < inicio or inicio > membresia.fecha_fin:
        raise ValidationError('Revisa las fechas: el inicio debe ser futuro o de hoy y estar dentro de la vigencia.')
    if membresia.dias_congelamiento_contratados is None:
        raise ValidationError('Las condiciones de congelamiento de esta membresía no están registradas.')
    aprobadas = membresia.solicitudes_congelamiento.filter(estado='APROBADA')
    utilizados = sum(pausa.dias_comprometidos for pausa in aprobadas)
    if aprobadas.filter(fecha_inicio__lte=fin, fecha_fin__gte=inicio).filter(Q(fecha_reactivacion__isnull=True) | Q(fecha_reactivacion__gt=inicio)).exists():
        raise ValidationError('Las fechas se superponen con otra pausa aprobada.')
    if (fin - inicio).days + 1 > membresia.dias_congelamiento_contratados - utilizados:
        raise ValidationError('La pausa supera los días de congelamiento contratados.')
    if membresia.solicitudes_congelamiento.filter(estado='PENDIENTE').exists():
        raise ValidationError('Ya tienes una solicitud de congelamiento pendiente.')
    solicitud = SolicitudCongelamiento.objects.create(membresia=membresia, solicitante=usuario, fecha_inicio=inicio, fecha_fin=fin, motivo=motivo.strip())
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    from .models import NotificacionRecepcion
    admins = get_user_model().objects.filter(Q(is_superuser=True) | Q(groups__name='Administrador'), is_active=True).distinct()
    NotificacionRecepcion.objects.bulk_create([NotificacionRecepcion(destinatario=admin, destino=reverse('solicitudes_congelamiento') + f'#congelamiento-{solicitud.pk}', mensaje=f'Nueva solicitud de congelamiento de {membresia.socio.nombre_completo}.') for admin in admins])
    if personal and not titular:
        NotificacionSocio.objects.create(socio=membresia.socio, tipo='CONGELAMIENTO_SOLICITADO', mensaje=f'Recepción registró una solicitud de congelamiento del {inicio:%d/%m/%Y} al {fin:%d/%m/%Y}. Quedó pendiente de revisión.')
    return solicitud


@transaction.atomic
def aprobar_congelamiento_sin_conflictos(solicitud_id, administrador, reservas_esperadas=None, vencimiento_esperado=None, renovaciones_esperadas=None, reservas_renovacion_esperadas=None):
    """Aprueba la pausa y cancela las reservas que fueron revisadas expresamente."""
    from .models import SolicitudCongelamiento
    referencia = SolicitudCongelamiento.objects.select_related('membresia').get(pk=solicitud_id)
    clases_revisadas = sorted({r[2] for r in (reservas_esperadas or []) + (reservas_renovacion_esperadas or [])})
    list(Clase.all_objects.select_for_update().filter(pk__in=clases_revisadas).order_by('pk'))
    socio = Socio.all_objects.select_for_update().get(pk=referencia.membresia.socio_id)
    membresia = Membresia.all_objects.select_for_update().get(pk=referencia.membresia_id)
    solicitud = SolicitudCongelamiento.objects.select_for_update().get(pk=solicitud_id)
    if vencimiento_esperado and membresia.fecha_fin.isoformat() != vencimiento_esperado:
        raise ValidationError('Las condiciones cambiaron; revisa nuevamente la solicitud.')
    hoy = timezone.localdate()
    if solicitud.estado != 'PENDIENTE':
        raise ValidationError('La solicitud ya no está pendiente.')
    if not socio.activo or not membresia.activo or membresia.estado != 'ACTIVA' or not membresia.fecha_inicio <= hoy <= membresia.fecha_fin:
        raise ValidationError('La membresía debe estar vigente y activa para aprobar la solicitud.')
    if solicitud.fecha_inicio < hoy or solicitud.fecha_inicio > membresia.fecha_fin:
        raise ValidationError('Las fechas ya no son válidas. Solicita nuevas fechas.')
    aprobadas = membresia.solicitudes_congelamiento.filter(estado='APROBADA')
    usados = sum(p.dias_comprometidos for p in aprobadas)
    if membresia.dias_congelamiento_contratados is None or usados + solicitud.dias_solicitados > membresia.dias_congelamiento_contratados:
        raise ValidationError('La solicitud supera los días de congelamiento disponibles.')
    if aprobadas.filter(fecha_inicio__lte=solicitud.fecha_fin, fecha_fin__gte=solicitud.fecha_inicio).filter(Q(fecha_reactivacion__isnull=True) | Q(fecha_reactivacion__gt=solicitud.fecha_inicio)).exists():
        raise ValidationError('La pausa se superpone con otra ya aprobada.')
    reservas = list(Reserva.objects.filter(socio=socio, estado__in=['CONFIRMADA', 'LISTA_ESPERA'], clase__fecha__range=(solicitud.fecha_inicio, solicitud.fecha_fin)).select_related('clase').order_by('pk'))
    impacto_actual = [[r.pk, r.estado, r.clase_id] for r in reservas]
    if impacto_actual != (reservas_esperadas or []):
        raise ValidationError('Las reservas afectadas cambiaron. Revisa nuevamente la solicitud antes de aprobar.')
    ahora = timezone.localtime()
    if any(r.clase.asistencia_cerrada_en or inicio_clase_local(r.clase) <= ahora for r in reservas):
        raise ValidationError('Una clase afectada ya comenzó o tiene la asistencia cerrada. Revisa la solicitud manualmente.')
    nuevo_fin = membresia.fecha_fin + timedelta(days=solicitud.dias_solicitados)
    conflictos = list(Membresia.objects.select_for_update().filter(socio=socio, estado__in=['ACTIVA', 'CONGELADA'], fecha_inicio__lte=nuevo_fin, fecha_fin__gte=membresia.fecha_inicio).exclude(pk=membresia.pk).order_by('fecha_inicio', 'pk'))
    actual = [[m.pk, m.fecha_inicio.isoformat(), m.fecha_fin.isoformat()] for m in conflictos]
    if actual != (renovaciones_esperadas or []):
        raise ValidationError('Las renovaciones afectadas cambiaron. Revisa nuevamente la solicitud.')
    if len(conflictos) > 1 or any(m.fecha_inicio <= membresia.fecha_fin or m.estado != 'ACTIVA' for m in conflictos):
        raise ValidationError('Hay membresías superpuestas que requieren revisión manual.')
    if conflictos:
        futura = conflictos[0]
        delta = nuevo_fin + timedelta(days=1) - futura.fecha_inicio
        inicio_nuevo, fin_nuevo = futura.fecha_inicio + delta, futura.fecha_fin + delta
        reservas_futuras = list(reservas_fuera_de_renovacion(futura, inicio_nuevo, fin_nuevo))
        if [[r.pk, r.estado, r.clase_id] for r in reservas_futuras] != (reservas_renovacion_esperadas or []):
            raise ValidationError('Las reservas de la renovación cambiaron. Revisa nuevamente la solicitud.')
        if futura.solicitudes_congelamiento.filter(estado__in=['PENDIENTE', 'APROBADA']).exists():
            raise ValidationError('La renovación tiene pausas que requieren revisión.')
        if any(r.clase.asistencia_cerrada_en or inicio_clase_local(r.clase) <= ahora for r in reservas_futuras):
            raise ValidationError('Una clase de la renovación ya comenzó o tiene la asistencia cerrada.')
        if Membresia.objects.filter(socio=socio, estado__in=['ACTIVA', 'CONGELADA'], fecha_inicio__lte=fin_nuevo, fecha_fin__gte=inicio_nuevo).exclude(pk__in=[membresia.pk, futura.pk]).exists():
            raise ValidationError('La nueva fecha de la renovación se superpone con otra membresía.')
        solicitud.renovacion_ajustada = futura
        solicitud.renovacion_inicio_anterior, solicitud.renovacion_fin_anterior = futura.fecha_inicio, futura.fecha_fin
        futura.fecha_inicio, futura.fecha_fin = inicio_nuevo, fin_nuevo
        futura.save(update_fields=['fecha_inicio', 'fecha_fin'])
    elif reservas_renovacion_esperadas:
        raise ValidationError('Las condiciones de la renovación cambiaron. Revisa nuevamente la solicitud.')
    membresia.fecha_fin = nuevo_fin
    membresia.save(update_fields=['fecha_fin'])
    solicitud.estado = 'APROBADA'
    solicitud.revisado_por = administrador
    solicitud.revisada_en = timezone.now()
    solicitud.save(update_fields=['estado', 'revisado_por', 'revisada_en', 'renovacion_ajustada', 'renovacion_inicio_anterior', 'renovacion_fin_anterior'])
    for reserva in reservas:
        exito, mensaje = cancelar_reserva_clase(reserva.pk)
        if not exito:
            raise ValidationError(f'No se pudo cancelar una reserva afectada: {mensaje}')
        reserva.refresh_from_db()
        if reserva.consume_clase:
            reserva.consume_clase = False
            reserva.save(update_fields=['consume_clase', 'fecha_actualizacion'])
        notificar_reserva(reserva, 'PAUSA_CANCELA_RESERVA', f'Se canceló tu reserva o espera de {reserva.clase.nombre} del {reserva.clase.fecha:%d/%m/%Y} por el congelamiento aprobado. No se descontó una clase.')
    if conflictos:
        for reserva in reservas_futuras:
            exito, mensaje = cancelar_reserva_clase(reserva.pk)
            if not exito:
                raise ValidationError(f'No se pudo cancelar una reserva de la renovación: {mensaje}')
            reserva.refresh_from_db()
            if reserva.consume_clase:
                reserva.consume_clase = False
                reserva.save(update_fields=['consume_clase', 'fecha_actualizacion'])
            notificar_reserva(reserva, 'PAUSA_RENUEVA_CANCELA', f'Se canceló tu reserva o espera de {reserva.clase.nombre} del {reserva.clase.fecha:%d/%m/%Y} porque quedó fuera de la renovación ajustada. No se descontó una clase.')
    NotificacionSocio.objects.create(socio=socio, tipo='CONGELAMIENTO_APROBADO', mensaje=f'Tu congelamiento fue aprobado del {solicitud.fecha_inicio:%d/%m/%Y} al {solicitud.fecha_fin:%d/%m/%Y}. Tu membresía vence el {nuevo_fin:%d/%m/%Y}.')
    if conflictos:
        NotificacionSocio.objects.create(socio=socio, tipo='RENOVACION_AJUSTADA', mensaje=f'Tu renovación {futura.plan.nombre} se ajustó: comenzará el {futura.fecha_inicio:%d/%m/%Y} y terminará el {futura.fecha_fin:%d/%m/%Y}.')
    return solicitud


def impacto_cancelar_pausa(solicitud):
    membresia = solicitud.membresia
    nuevo_fin = membresia.fecha_fin - timedelta(days=solicitud.dias_solicitados)
    reservas = Reserva.objects.filter(socio_id=membresia.socio_id, estado__in=['CONFIRMADA', 'LISTA_ESPERA'], clase__fecha__gt=nuevo_fin, clase__fecha__lte=membresia.fecha_fin).filter(Q(membresia=membresia) | Q(membresia__isnull=True)).select_related('clase').order_by('pk')
    otras_pausas = membresia.solicitudes_congelamiento.filter(estado='APROBADA', fecha_inicio__gt=nuevo_fin).exclude(pk=solicitud.pk)
    return nuevo_fin, reservas, otras_pausas.exists()


@transaction.atomic
def solicitar_reactivacion(solicitud_id, usuario):
    from .models import SolicitudCongelamiento
    referencia = SolicitudCongelamiento.objects.select_related('membresia').get(pk=solicitud_id)
    socio = Socio.all_objects.select_for_update().get(pk=referencia.membresia.socio_id)
    membresia = Membresia.all_objects.select_for_update().get(pk=referencia.membresia_id)
    pausa = SolicitudCongelamiento.objects.select_for_update().get(pk=solicitud_id)
    hoy = timezone.localdate()
    if not usuario.is_authenticated or socio.usuario_id != usuario.pk or not socio.activo:
        raise ValidationError('Solo el socio titular activo puede solicitar una reactivación.')
    if not membresia.activo or membresia.estado != 'ACTIVA' or pausa.estado != 'APROBADA' or pausa.fecha_reactivacion or not pausa.fecha_inicio <= hoy <= pausa.fecha_fin:
        raise ValidationError('Solo puedes solicitar reactivación durante una pausa vigente.')
    if pausa.reactivacion_pendiente:
        raise ValidationError('Ya tienes una solicitud de reactivación pendiente.')
    pausa.reactivacion_pendiente = True
    pausa.reactivacion_solicitada_en = timezone.now()
    pausa.reactivacion_expirada_en = None
    pausa.save(update_fields=['reactivacion_pendiente', 'reactivacion_solicitada_en', 'reactivacion_expirada_en'])
    return pausa


def procesar_reactivaciones_vencidas(socio_id=None):
    """Cierra solicitudes no atendidas después del último día de la pausa."""
    from .models import SolicitudCongelamiento
    hoy = timezone.localdate()
    candidatas = SolicitudCongelamiento.objects.filter(estado='APROBADA', reactivacion_pendiente=True, fecha_reactivacion__isnull=True, fecha_fin__lt=hoy)
    if socio_id is not None:
        candidatas = candidatas.filter(membresia__socio_id=socio_id)
    ids = list(candidatas.values_list('pk', flat=True))
    procesadas = 0
    for pk in ids:
        with transaction.atomic():
            pausa = SolicitudCongelamiento.objects.select_for_update().select_related('membresia').get(pk=pk)
            if pausa.estado != 'APROBADA' or not pausa.reactivacion_pendiente or pausa.fecha_reactivacion or pausa.fecha_fin >= hoy:
                continue
            pausa.reactivacion_pendiente = False
            pausa.reactivacion_expirada_en = timezone.now()
            pausa.save(update_fields=['reactivacion_pendiente', 'reactivacion_expirada_en'])
            NotificacionSocio.objects.create(socio_id=pausa.membresia.socio_id, tipo='REACTIVACION_EXPIRADA', mensaje=f'Tu solicitud de reactivación no se revisó antes de terminar la pausa del {pausa.fecha_fin:%d/%m/%Y}. La pausa terminó en su fecha programada; revisa el estado de tu membresía.')
            procesadas += 1
    return procesadas


def impacto_reactivacion(pausa, hoy):
    membresia = pausa.membresia
    sin_usar = max(0, (pausa.fecha_fin - hoy).days + 1)
    nuevo_fin = membresia.fecha_fin - timedelta(days=sin_usar)
    reservas = Reserva.objects.filter(socio_id=membresia.socio_id, estado__in=['CONFIRMADA', 'LISTA_ESPERA'], clase__fecha__gt=nuevo_fin, clase__fecha__lte=membresia.fecha_fin).filter(Q(membresia=membresia) | Q(membresia__isnull=True)).select_related('clase').order_by('pk')
    otras_pausas = membresia.solicitudes_congelamiento.filter(estado__in=['PENDIENTE', 'APROBADA'], fecha_inicio__gt=nuevo_fin).exclude(pk=pausa.pk).exists()
    return nuevo_fin, reservas, otras_pausas


def propuesta_renovacion_reactivacion(pausa, nuevo_fin):
    futura = pausa.renovacion_ajustada
    if not futura or not pausa.renovacion_inicio_anterior:
        return None
    inicio = max(pausa.renovacion_inicio_anterior, nuevo_fin + timedelta(days=1))
    fin = futura.fecha_fin + (inicio - futura.fecha_inicio)
    return [futura.pk, futura.fecha_inicio.isoformat(), futura.fecha_fin.isoformat(), inicio.isoformat(), fin.isoformat()]


def reservas_fuera_de_renovacion(futura, inicio_nuevo, fin_nuevo):
    """Reservas activas de la renovación que dejarían de estar cubiertas."""
    return Reserva.objects.filter(socio_id=futura.socio_id, estado__in=['CONFIRMADA', 'LISTA_ESPERA']).filter(
        Q(membresia=futura) | Q(membresia__isnull=True, clase__fecha__range=(futura.fecha_inicio, futura.fecha_fin))
    ).filter(Q(clase__fecha__lt=inicio_nuevo) | Q(clase__fecha__gt=fin_nuevo)).select_related('clase').order_by('pk')


@transaction.atomic
def aprobar_reactivacion(solicitud_id, administrador, fecha_esperada, fin_esperado, renovacion_esperada=None, reservas_esperadas=None, reservas_actuales_esperadas=None):
    from .models import SolicitudCongelamiento
    referencia = SolicitudCongelamiento.objects.select_related('membresia').get(pk=solicitud_id)
    clases_revisadas = sorted({item[2] for item in (reservas_esperadas or []) + (reservas_actuales_esperadas or [])})
    list(Clase.all_objects.select_for_update().filter(pk__in=clases_revisadas).order_by('pk'))
    socio = Socio.all_objects.select_for_update().get(pk=referencia.membresia.socio_id)
    membresia = Membresia.all_objects.select_for_update().get(pk=referencia.membresia_id)
    pausa = SolicitudCongelamiento.objects.select_for_update().get(pk=solicitud_id)
    hoy = timezone.localdate()
    if fecha_esperada != hoy.isoformat():
        raise ValidationError('Cambió el día. Revisa nuevamente la propuesta.')
    if not socio.activo or not membresia.activo or membresia.estado != 'ACTIVA' or pausa.estado != 'APROBADA' or pausa.fecha_reactivacion or not pausa.reactivacion_pendiente or not pausa.fecha_inicio <= hoy <= pausa.fecha_fin:
        raise ValidationError('La solicitud ya no está pendiente o la pausa no está vigente.')
    pausa.membresia = membresia
    nuevo_fin, reservas, otras_pausas = impacto_reactivacion(pausa, hoy)
    if nuevo_fin.isoformat() != fin_esperado:
        raise ValidationError('Las fechas cambiaron. Revisa nuevamente la propuesta.')
    reservas_actuales = list(reservas)
    if [[r.pk, r.estado, r.clase_id] for r in reservas_actuales] != (reservas_actuales_esperadas or []):
        raise ValidationError('Las reservas de la membresía cambiaron. Revisa nuevamente la propuesta.')
    if otras_pausas:
        raise ValidationError('Hay otras pausas afectadas. Su ajuste para reactivación aún no está disponible.')
    ahora = timezone.localtime()
    if any(r.clase.asistencia_cerrada_en or inicio_clase_local(r.clase) <= ahora for r in reservas_actuales):
        raise ValidationError('Una clase afectada ya comenzó o tiene la asistencia cerrada. Revisa la solicitud manualmente.')
    futura = None
    if pausa.renovacion_ajustada_id:
        futura = Membresia.all_objects.select_for_update().get(pk=pausa.renovacion_ajustada_id)
        pausa.renovacion_ajustada = futura
        propuesta = propuesta_renovacion_reactivacion(pausa, nuevo_fin)
        if not propuesta or propuesta != renovacion_esperada:
            raise ValidationError('La renovación cambió. Revisa nuevamente las fechas propuestas.')
        inicio_nuevo = max(pausa.renovacion_inicio_anterior, nuevo_fin + timedelta(days=1))
        fin_nuevo = futura.fecha_fin + (inicio_nuevo - futura.fecha_inicio)
        if not futura.activo or futura.estado != 'ACTIVA' or futura.fecha_inicio != membresia.fecha_fin + timedelta(days=1) or inicio_nuevo > futura.fecha_inicio or inicio_nuevo <= hoy:
            raise ValidationError('Las fechas de la renovación requieren revisión manual.')
        reservas_futuras = list(reservas_fuera_de_renovacion(futura, inicio_nuevo, fin_nuevo))
        if [[r.pk, r.estado, r.clase_id] for r in reservas_futuras] != (reservas_esperadas or []):
            raise ValidationError('Las reservas de la renovación cambiaron. Revisa nuevamente la propuesta.')
        if futura.solicitudes_congelamiento.filter(estado__in=['PENDIENTE', 'APROBADA']).exists():
            raise ValidationError('La renovación tiene pausas que requieren revisión.')
        ahora = timezone.localtime()
        if any(r.clase.asistencia_cerrada_en or inicio_clase_local(r.clase) <= ahora for r in reservas_futuras):
            raise ValidationError('Una clase afectada ya comenzó o tiene la asistencia cerrada. Revisa la solicitud manualmente.')
        if Membresia.objects.filter(socio=socio, estado__in=['ACTIVA', 'CONGELADA'], fecha_inicio__lte=fin_nuevo, fecha_fin__gte=inicio_nuevo).exclude(pk__in=[membresia.pk, futura.pk]).exists():
            raise ValidationError('Las nuevas fechas se superponen con otra membresía.')
        futura.fecha_inicio, futura.fecha_fin = inicio_nuevo, fin_nuevo
        futura.save(update_fields=['fecha_inicio', 'fecha_fin'])
    elif renovacion_esperada is not None or reservas_esperadas:
        raise ValidationError('Las condiciones cambiaron. Revisa nuevamente la propuesta.')
    membresia.fecha_fin = nuevo_fin
    membresia.save(update_fields=['fecha_fin'])
    pausa.fecha_reactivacion = hoy
    pausa.reactivacion_pendiente = False
    pausa.reactivada_por = administrador
    pausa.save(update_fields=['fecha_reactivacion', 'reactivacion_pendiente', 'reactivada_por'])
    for reserva in reservas_actuales:
        exito, mensaje = cancelar_reserva_clase(reserva.pk)
        if not exito:
            raise ValidationError(f'No se pudo cancelar una reserva afectada: {mensaje}')
        reserva.refresh_from_db()
        if reserva.consume_clase:
            reserva.consume_clase = False
            reserva.save(update_fields=['consume_clase', 'fecha_actualizacion'])
        notificar_reserva(reserva, 'REACTIVA_VIGENCIA_CANCELA', f'Se canceló tu reserva o espera de {reserva.clase.nombre} del {reserva.clase.fecha:%d/%m/%Y} porque quedó fuera de la vigencia ajustada. No se descontó una clase.')
    if futura:
        for reserva in reservas_futuras:
            exito, mensaje = cancelar_reserva_clase(reserva.pk)
            if not exito:
                raise ValidationError(f'No se pudo cancelar una reserva afectada: {mensaje}')
            reserva.refresh_from_db()
            if reserva.consume_clase:
                reserva.consume_clase = False
                reserva.save(update_fields=['consume_clase', 'fecha_actualizacion'])
            notificar_reserva(reserva, 'REACTIVA_CANCELA_RESERVA', f'Se canceló tu reserva o espera de {reserva.clase.nombre} del {reserva.clase.fecha:%d/%m/%Y} porque quedó fuera de la renovación ajustada. No se descontó una clase.')
    NotificacionSocio.objects.create(socio=socio, tipo='REACTIVACION_APROBADA', mensaje=f'Tu membresía volvió a estar activa el {hoy:%d/%m/%Y}. Su vencimiento es el {nuevo_fin:%d/%m/%Y}.')
    if futura:
        NotificacionSocio.objects.create(socio=socio, tipo='RENOVACION_REACTIVADA', mensaje=f'Tu renovación {futura.plan.nombre} cambia: del {futura.fecha_inicio:%d/%m/%Y} al {futura.fecha_fin:%d/%m/%Y}.')
    return pausa


@transaction.atomic
def cancelar_pausa_aprobada(solicitud_id, usuario, fin_esperado, renovacion_esperada=None, reservas_renovacion_esperadas=None, reservas_actuales_esperadas=None):
    from .models import SolicitudCongelamiento
    referencia = SolicitudCongelamiento.objects.select_related('membresia').get(pk=solicitud_id)
    clases_revisadas = sorted({item[2] for item in (reservas_renovacion_esperadas or []) + (reservas_actuales_esperadas or [])})
    list(Clase.all_objects.select_for_update().filter(pk__in=clases_revisadas).order_by('pk'))
    socio = Socio.all_objects.select_for_update().get(pk=referencia.membresia.socio_id)
    membresia = Membresia.all_objects.select_for_update().get(pk=referencia.membresia_id)
    solicitud = SolicitudCongelamiento.objects.select_for_update().get(pk=solicitud_id)
    if not usuario.is_authenticated or socio.usuario_id != usuario.pk or not socio.activo:
        raise ValidationError('Solo el socio titular activo puede cancelar esta pausa.')
    if solicitud.estado != 'APROBADA' or solicitud.fecha_inicio <= timezone.localdate():
        raise ValidationError('Solo puedes cancelar una pausa aprobada antes de su fecha de inicio.')
    if not membresia.activo or membresia.estado != 'ACTIVA':
        raise ValidationError('La membresía ya no permite cancelar esta pausa; contacta a administración.')
    solicitud.membresia = membresia
    nuevo_fin, reservas, otras_pausas = impacto_cancelar_pausa(solicitud)
    if nuevo_fin.isoformat() != fin_esperado:
        raise ValidationError('Las condiciones cambiaron. Revisa nuevamente los efectos de la cancelación.')
    reservas_actuales = list(reservas)
    if [[r.pk, r.estado, r.clase_id] for r in reservas_actuales] != (reservas_actuales_esperadas or []):
        raise ValidationError('Las reservas de la membresía cambiaron. Revisa nuevamente la cancelación.')
    if otras_pausas:
        raise ValidationError('Hay otras pausas afectadas. El ajuste de esos casos aún no está disponible.')
    ahora = timezone.localtime()
    if any(r.clase.asistencia_cerrada_en or inicio_clase_local(r.clase) <= ahora for r in reservas_actuales):
        raise ValidationError('Una clase afectada ya comenzó o tiene la asistencia cerrada. Revisa la solicitud manualmente.')
    futura = None
    if solicitud.renovacion_ajustada_id:
        futura = Membresia.all_objects.select_for_update().get(pk=solicitud.renovacion_ajustada_id)
        actual = [futura.pk, futura.fecha_inicio.isoformat(), futura.fecha_fin.isoformat(), solicitud.renovacion_inicio_anterior.isoformat() if solicitud.renovacion_inicio_anterior else None, solicitud.renovacion_fin_anterior.isoformat() if solicitud.renovacion_fin_anterior else None]
        if actual != renovacion_esperada:
            raise ValidationError('La renovación cambió. Revisa nuevamente sus fechas.')
        anterior_inicio, anterior_fin = solicitud.renovacion_inicio_anterior, solicitud.renovacion_fin_anterior
        if not futura.activo or futura.estado != 'ACTIVA' or not anterior_inicio or not anterior_fin or anterior_inicio <= nuevo_fin or anterior_inicio <= timezone.localdate():
            raise ValidationError('La renovación necesita revisión antes de restaurar sus fechas.')
        reservas_futuras = list(reservas_fuera_de_renovacion(futura, anterior_inicio, anterior_fin))
        if [[r.pk, r.estado, r.clase_id] for r in reservas_futuras] != (reservas_renovacion_esperadas or []):
            raise ValidationError('Las reservas de la renovación cambiaron. Revisa nuevamente la cancelación.')
        if futura.solicitudes_congelamiento.filter(estado__in=['PENDIENTE', 'APROBADA']).exists():
            raise ValidationError('La renovación tiene pausas que requieren revisión.')
        ahora = timezone.localtime()
        if any(r.clase.asistencia_cerrada_en or inicio_clase_local(r.clase) <= ahora for r in reservas_futuras):
            raise ValidationError('Una clase afectada ya comenzó o tiene la asistencia cerrada. Revisa la solicitud manualmente.')
        if Membresia.objects.filter(socio=socio, estado__in=['ACTIVA', 'CONGELADA'], fecha_inicio__lte=anterior_fin, fecha_fin__gte=anterior_inicio).exclude(pk__in=[membresia.pk, futura.pk]).exists():
            raise ValidationError('Las fechas anteriores se superponen con otra membresía.')
        futura.fecha_inicio, futura.fecha_fin = anterior_inicio, anterior_fin
        futura.save(update_fields=['fecha_inicio', 'fecha_fin'])
    elif renovacion_esperada is not None or reservas_renovacion_esperadas:
        raise ValidationError('Las condiciones cambiaron. Revisa nuevamente la cancelación.')
    membresia.fecha_fin = nuevo_fin
    membresia.save(update_fields=['fecha_fin'])
    solicitud.estado = 'CANCELADA'
    solicitud.cancelada_por = usuario
    solicitud.cancelada_en = timezone.now()
    solicitud.save(update_fields=['estado', 'cancelada_por', 'cancelada_en'])
    for reserva in reservas_actuales:
        exito, mensaje = cancelar_reserva_clase(reserva.pk)
        if not exito:
            raise ValidationError(f'No se pudo cancelar una reserva afectada: {mensaje}')
        reserva.refresh_from_db()
        if reserva.consume_clase:
            reserva.consume_clase = False
            reserva.save(update_fields=['consume_clase', 'fecha_actualizacion'])
        notificar_reserva(reserva, 'CANCELA_PAUSA_VIGENCIA', f'Se canceló tu reserva o espera de {reserva.clase.nombre} del {reserva.clase.fecha:%d/%m/%Y} porque quedó fuera de la vigencia ajustada. No se descontó una clase.')
    if futura:
        for reserva in reservas_futuras:
            exito, mensaje = cancelar_reserva_clase(reserva.pk)
            if not exito:
                raise ValidationError(f'No se pudo cancelar una reserva afectada: {mensaje}')
            reserva.refresh_from_db()
            if reserva.consume_clase:
                reserva.consume_clase = False
                reserva.save(update_fields=['consume_clase', 'fecha_actualizacion'])
            notificar_reserva(reserva, 'CANCELA_PAUSA_RESERVA', f'Se canceló tu reserva o espera de {reserva.clase.nombre} del {reserva.clase.fecha:%d/%m/%Y} porque quedó fuera de la renovación ajustada. No se descontó una clase.')
    NotificacionSocio.objects.create(socio=socio, tipo='CONGELAMIENTO_CANCELADO', mensaje=f'Cancelaste tu pausa programada. Tu membresía vence el {nuevo_fin:%d/%m/%Y}.')
    if futura:
        NotificacionSocio.objects.create(socio=socio, tipo='RENOVACION_RESTAURADA', mensaje=f'Tu renovación {futura.plan.nombre} vuelve a sus fechas anteriores: del {futura.fecha_inicio:%d/%m/%Y} al {futura.fecha_fin:%d/%m/%Y}.')
    return solicitud


# ------------------------------------------------------------------------------
# 1. Servicio de Control de Acceso mediante Código QR
# ------------------------------------------------------------------------------
@transaction.atomic
def validar_y_registrar_acceso(identificador: str, tipo: str = 'ENTRADA', metodo: str = 'SIMULACION_QR') -> dict:
    """
    Valida y registra el acceso de un socio mediante simulación de código QR o ID manual.
    Mide la latencia de respuesta del servidor en milisegundos.
    Aplica controles de consistencia (evita doble entrada consecutiva) y chequeo de membresía.
    """
    t_inicio = time.perf_counter()
    hoy = timezone.localdate()
    identificador_limpio = (identificador or '').strip()

    # El código personal es el único identificador admitido para el acceso.
    socio = Socio.all_objects.select_for_update().filter(codigo_qr__iexact=identificador_limpio).first()

    if not socio:
        t_fin = time.perf_counter()
        latencia_ms = round((t_fin - t_inicio) * 1000.0, 2)
        return {
            'autorizado': False,
            'motivo': f"Código '{identificador_limpio}' no encontrado o no corresponde a un socio activo.",
            'socio': None,
            'tipo': tipo,
            'tiempo_respuesta_ms': latencia_ms,
            'registro_id': None,
        }

    # 2. Verificar si el socio está activo (borrado lógico)
    if not socio.activo and tipo == 'ENTRADA':
        t_fin = time.perf_counter()
        latencia_ms = round((t_fin - t_inicio) * 1000.0, 2)
        registro = RegistroAcceso.objects.create(
            socio=socio,
            tipo=tipo,
            resultado='DENEGADO',
            motivo="El socio ha sido desactivado del sistema (baja lógica).",
            tiempo_respuesta_ms=latencia_ms,
            metodo=metodo
        )
        return {
            'autorizado': False,
            'motivo': "El socio ha sido desactivado del sistema.",
            'socio': socio,
            'tipo': tipo,
            'tiempo_respuesta_ms': latencia_ms,
            'registro_id': registro.id,
        }

    # 3. Control de consistencia de movimientos (Entrada / Salida)
    esta_dentro = socio.esta_dentro()

    if tipo == 'ENTRADA' and esta_dentro:
        t_fin = time.perf_counter()
        latencia_ms = round((t_fin - t_inicio) * 1000.0, 2)
        registro = RegistroAcceso.objects.create(
            socio=socio,
            tipo='ENTRADA',
            resultado='DENEGADO',
            motivo="El socio ya se encuentra dentro del gimnasio. Debe registrar salida antes de volver a ingresar.",
            tiempo_respuesta_ms=latencia_ms,
            metodo=metodo
        )
        return {
            'autorizado': False,
            'motivo': "Acceso denegado: El socio ya registra una entrada activa sin salida previa.",
            'socio': socio,
            'tipo': tipo,
            'tiempo_respuesta_ms': latencia_ms,
            'registro_id': registro.id,
        }

    # 4. Solo se registra una salida cuando existe una entrada previa sin salida.
    if tipo == 'SALIDA':
        t_fin = time.perf_counter()
        latencia_ms = round((t_fin - t_inicio) * 1000.0, 2)
        motivo_salida = "Salida registrada con éxito." if esta_dentro else "No se puede registrar una salida sin una entrada previa."
        registro = RegistroAcceso.objects.create(
            socio=socio,
            tipo='SALIDA',
            resultado='PERMITIDO' if esta_dentro else 'DENEGADO',
            motivo=motivo_salida,
            tiempo_respuesta_ms=latencia_ms,
            metodo=metodo
        )
        return {
            'autorizado': esta_dentro,
            'motivo': motivo_salida,
            'socio': socio,
            'tipo': tipo,
            'tiempo_respuesta_ms': latencia_ms,
            'registro_id': registro.id,
        }

    # 5. En caso de Entrada: validar estado de la membresía
    membresia = socio.membresia_vigente()
    if not membresia:
        # Buscar si tiene membresías vencidas o congeladas para dar un mensaje explicativo exacto
        ultima_m = socio.membresias.order_by('-fecha_fin').first()
        if ultima_m:
            ultima_m.actualizar_estado()
            if ultima_m.estado_actual_display == 'Congelada':
                motivo = f"Membresía CONGELADA temporalmente. Motivo: {ultima_m.motivo_congelacion or 'Pausa solicitada'}."
            elif ultima_m.estado == 'VENCIDA' or ultima_m.fecha_fin < hoy:
                motivo = f"Membresía VENCIDA el {ultima_m.fecha_fin.strftime('%d/%m/%Y')}. Debe renovar su plan."
            else:
                motivo = f"Membresía en estado {ultima_m.get_estado_display()} no autoriza ingreso."
        else:
            motivo = "El socio no cuenta con ninguna membresía contratada."

        t_fin = time.perf_counter()
        latencia_ms = round((t_fin - t_inicio) * 1000.0, 2)
        registro = RegistroAcceso.objects.create(
            socio=socio,
            tipo='ENTRADA',
            resultado='DENEGADO',
            motivo=motivo,
            tiempo_respuesta_ms=latencia_ms,
            metodo=metodo
        )
        return {
            'autorizado': False,
            'motivo': motivo,
            'socio': socio,
            'tipo': tipo,
            'tiempo_respuesta_ms': latencia_ms,
            'registro_id': registro.id,
        }

    # Evaluar si la membresía activa permite ingresar
    autorizado, motivo = membresia.puede_ingresar()
    resultado = 'PERMITIDO' if autorizado else 'DENEGADO'

    t_fin = time.perf_counter()
    latencia_ms = round((t_fin - t_inicio) * 1000.0, 2)

    registro = RegistroAcceso.objects.create(
        socio=socio,
        tipo='ENTRADA',
        resultado=resultado,
        motivo=motivo,
        tiempo_respuesta_ms=latencia_ms,
        metodo=metodo
    )

    return {
        'autorizado': autorizado,
        'motivo': motivo,
        'socio': socio,
        'tipo': tipo,
        'membresia': membresia,
        'tiempo_respuesta_ms': latencia_ms,
        'registro_id': registro.id,
    }


# ------------------------------------------------------------------------------
# 2. Servicio de Gestión de Reservas y Lista de Espera FIFO
# ------------------------------------------------------------------------------
def inicio_clase_local(clase):
    """Convierte el horario de la clase a la zona horaria configurada."""
    return timezone.make_aware(datetime.combine(clase.fecha, clase.hora_inicio), timezone.get_current_timezone())


def notificar_reserva(reserva, tipo, mensaje):
    NotificacionSocio.objects.get_or_create(
        reserva=reserva, tipo=tipo,
        defaults={'socio': reserva.socio, 'mensaje': mensaje},
    )


def cerrar_esperas_clase(clase, ahora):
    """Cierra las esperas y registra un aviso por cada solicitud."""
    reservas = list(Reserva.objects.select_for_update().filter(clase=clase, estado='LISTA_ESPERA'))
    for reserva in reservas:
        reserva.estado = 'SIN_CUPO'
        reserva.save(update_fields=['estado', 'fecha_actualizacion'])
        notificar_reserva(reserva, 'SIN_CUPO', f'No obtuviste un cupo para {clase.nombre} del {clase.fecha:%d/%m/%Y}.')
    return len(reservas)


def cerrar_listas_iniciadas(momento=None):
    """Cierra esperas de clases iniciadas; se puede ejecutar más de una vez."""
    ahora = momento or timezone.localtime()
    ahora = timezone.localtime(ahora)
    ids = list(Clase.all_objects.filter(
        Q(fecha__lt=ahora.date()) | Q(fecha=ahora.date(), hora_inicio__lte=ahora.time()),
        reservas__estado='LISTA_ESPERA',
    ).values_list('id', flat=True).distinct())
    total = 0
    for clase_id in ids:
        with transaction.atomic():
            clase = Clase.all_objects.select_for_update().get(pk=clase_id)
            if inicio_clase_local(clase) <= ahora:
                total += cerrar_esperas_clase(clase, ahora)
    return total


def saldo_disponible(membresia):
    """None representa ilimitado o condiciones antiguas sin dato histórico."""
    if not membresia.limite_clases_registrado or membresia.limite_clases_contratado is None:
        return None
    vinculadas = Reserva.objects.filter(membresia=membresia, consume_clase=True).count()
    anteriores = Reserva.objects.filter(
        socio=membresia.socio, membresia__isnull=True, estado='CONFIRMADA',
        clase__fecha__gte=membresia.fecha_inicio, clase__fecha__lte=membresia.fecha_fin,
    ).count()
    return max(membresia.limite_clases_contratado - vinculadas - anteriores, 0)


def tiene_reserva_superpuesta(socio, clase):
    """Detecta clases confirmadas que comparten parte de su horario."""
    return Reserva.objects.filter(
        socio=socio, estado='CONFIRMADA', clase__fecha=clase.fecha,
        clase__hora_inicio__lt=clase.hora_fin,
        clase__hora_fin__gt=clase.hora_inicio,
    ).exclude(clase=clase).exists()


@transaction.atomic
def confirmar_asistencia_recepcion(reserva_id, usuario):
    """Confirma asistencia tras una entrada reciente; no sustituye la decisión del socio."""
    clase_id = Reserva.objects.filter(pk=reserva_id).values_list('clase_id', flat=True).first()
    if not clase_id:
        return False, 'Se necesita una reserva confirmada para registrar asistencia.'
    clase = Clase.all_objects.select_for_update().get(pk=clase_id)
    reserva = Reserva.objects.select_for_update().select_related('clase', 'socio').filter(pk=reserva_id).first()
    if clase.asistencia_cerrada_en:
        return False, 'La lista de asistencia ya fue cerrada; solicita una revisión a administración.'
    if not reserva or reserva.estado != 'CONFIRMADA':
        return False, 'Se necesita una reserva confirmada para registrar asistencia.'
    if reserva.asistencia != 'PENDIENTE':
        return False, 'La asistencia ya fue registrada; solicita una revisión al profesor.'
    ahora = timezone.now()
    inicio = inicio_clase_local(reserva.clase)
    if not (inicio - timedelta(minutes=30) <= ahora <= inicio + timedelta(minutes=5)):
        return False, 'Recepción puede confirmar la asistencia desde 30 minutos antes hasta 5 minutos después del inicio de la clase.'
    ultimo = reserva.socio.accesos.filter(resultado='PERMITIDO').order_by('-fecha_hora', '-id').first()
    if not ultimo or ultimo.tipo != 'ENTRADA' or not (inicio - timedelta(minutes=30) <= ultimo.fecha_hora <= ahora):
        return False, 'Se necesita una entrada permitida al gimnasio dentro del período de confirmación de la clase.'
    HistorialAsistencia.objects.create(reserva=reserva, estado_anterior=reserva.asistencia, estado_nuevo='ASISTIO', usuario=usuario, origen='RECEPCION')
    reserva.asistencia = 'ASISTIO'
    reserva.asistencia_registrada_por = usuario
    reserva.fecha_asistencia = ahora
    reserva.save(update_fields=['asistencia', 'asistencia_registrada_por', 'fecha_asistencia', 'fecha_actualizacion'])
    return True, 'Asistencia confirmada para la clase.'


def gestionar_reserva_clase(clase_id: int, socio_id: int) -> tuple[bool, str, Reserva | None]:
    """
    Registra una reserva para una clase grupal.
    Usa transacciones atómicas (select_for_update) para prevenir condiciones de carrera y sobrecupos.
    Si la clase está llena, posiciona al socio en LISTA_ESPERA (FIFO).
    """
    try:
        with transaction.atomic():
            clase = Clase.objects.select_for_update().get(id=clase_id)
            socio = Socio.objects.select_for_update().get(id=socio_id)

            if not clase.activo:
                return False, "La clase grupal ha sido cancelada o dada de baja.", None

            ahora = timezone.localtime()
            if clase.fecha < ahora.date() or (clase.fecha == ahora.date() and clase.hora_fin <= ahora.time()):
                return False, "No se puede reservar una clase que ya terminó o de una fecha pasada.", None

            if not socio.activo:
                return False, "El socio está desactivado en el sistema.", None

            # Validar que el socio tenga membresía activa que permita clases
            membresia = socio.membresia_para_fecha(clase.fecha)
            if not membresia:
                return False, "El socio debe tener una membresía activa que cubra la fecha de la clase.", None

            if not membresia.incluye_clases:
                return False, f"El plan '{membresia.plan.nombre}' no incluye acceso a clases grupales.", None

            saldo = saldo_disponible(membresia)
            if saldo == 0:
                return False, 'El socio no tiene clases disponibles en la membresía correspondiente.', None

            inicio = inicio_clase_local(clase)
            if ahora < inicio - timedelta(hours=72):
                return False, "Las reservas se abren 72 horas antes de la clase.", None
            if ahora >= inicio - timedelta(minutes=15):
                return False, "Las reservas y la lista de espera cierran 15 minutos antes de la clase.", None

            # Validar que no tenga ya una reserva activa para esta clase
            reserva_existente = Reserva.objects.filter(
                clase=clase,
                socio=socio,
                estado__in=['CONFIRMADA', 'LISTA_ESPERA']
            ).first()

            if reserva_existente:
                estado_texto = "confirmada" if reserva_existente.estado == 'CONFIRMADA' else "en lista de espera"
                return False, f"El socio ya cuenta con una solicitud {estado_texto} para esta clase.", reserva_existente

            # Determinar cupos confirmados
            confirmadas = clase.reservas.filter(estado='CONFIRMADA').count()

            if confirmadas < clase.capacidad_maxima:
                if tiene_reserva_superpuesta(socio, clase):
                    return False, 'Ya existe una reserva confirmada en un horario que se cruza con esta clase.', None
                # Hay cupo disponible -> CONFIRMADA
                reserva = Reserva.objects.create(
                    clase=clase,
                    socio=socio,
                    membresia=membresia,
                    consume_clase=True,
                    estado='CONFIRMADA'
                )
                return True, f"Reserva CONFIRMADA para {clase.nombre}.", reserva
            else:
                # Sin cupos -> LISTA_ESPERA (FIFO)
                total_en_espera = clase.reservas.filter(estado='LISTA_ESPERA').count()
                posicion = total_en_espera + 1
                reserva = Reserva.objects.create(
                    clase=clase,
                    socio=socio,
                    membresia=membresia,
                    estado='LISTA_ESPERA'
                )
                return True, f"Clase llena ({clase.capacidad_maxima}/{clase.capacidad_maxima}). Socio asignado a la LISTA DE ESPERA en la posición #{posicion}.", reserva

    except Clase.DoesNotExist:
        return False, "La clase especificada no existe.", None
    except Socio.DoesNotExist:
        return False, "El socio especificado no existe.", None
    except Exception as e:
        return False, f"Error al procesar la reserva: {str(e)}", None


def cancelar_reserva_clase(reserva_id: int) -> tuple[bool, str]:
    """
    Cancela una reserva.
    Si la reserva era CONFIRMADA y existen personas en LISTA_ESPERA,
    promueve automáticamente a la primera persona en espera (FIFO) a CONFIRMADA.
    """
    try:
        with transaction.atomic():
            clase_id = Reserva.objects.values_list('clase_id', flat=True).get(id=reserva_id)
            clase = Clase.all_objects.select_for_update().get(id=clase_id)
            reserva = Reserva.objects.select_for_update().get(id=reserva_id)

            if clase.asistencia_cerrada_en:
                return False, 'La asistencia está cerrada; solicita una revisión a administración.'
            if reserva.estado not in ('CONFIRMADA', 'LISTA_ESPERA'):
                return False, 'Esta reserva ya no está activa.'

            if reserva.estado == 'CANCELADA':
                return False, "Esta reserva ya se encuentra cancelada."

            era_confirmada = (reserva.estado == 'CONFIRMADA')
            nombre_socio = reserva.socio.nombre_completo

            # Hasta dos horas antes se devuelve la clase; también en promociones tardías.
            ahora = timezone.localtime()
            inicio = inicio_clase_local(clase)
            if era_confirmada and reserva.membresia_id is None:
                reserva.membresia = reserva.socio.membresia_para_fecha(clase.fecha)
                reserva.consume_clase = bool(reserva.membresia)
            if era_confirmada and (ahora <= inicio - timedelta(hours=2) or (reserva.promocion_tardia and ahora < inicio)):
                reserva.consume_clase = False
            reserva.estado = 'CANCELADA'
            reserva.save(update_fields=['estado', 'membresia', 'consume_clase', 'fecha_actualizacion'])

            mensaje = f"Reserva de {nombre_socio} para {clase.nombre} cancelada exitosamente."

            # Si se liberó un cupo confirmado, promover al siguiente en lista de espera (FIFO)
            ahora = timezone.localtime()
            inicio = inicio_clase_local(clase)
            if ahora >= inicio:
                cerrar_esperas_clase(clase, ahora)
            if era_confirmada and ahora < inicio:
                personas_en_espera = (
                    Reserva.objects.select_for_update()
                    .filter(clase=clase, estado='LISTA_ESPERA').select_related('socio')
                    .order_by('fecha_reserva', 'id')
                )
                for candidato in personas_en_espera:
                    socio = Socio.all_objects.select_for_update().get(pk=candidato.socio_id)
                    if ahora >= inicio - timedelta(minutes=15) and not socio.esta_dentro():
                        continue
                    membresia = socio.membresia_para_fecha(clase.fecha) if socio.activo else None
                    if not membresia or not membresia.incluye_clases:
                        candidato.estado = 'CANCELADA'
                        candidato.save(update_fields=['estado', 'fecha_actualizacion'])
                        mensaje += f" Solicitud de {socio.nombre_completo} cancelada por membresía no habilitada."
                        continue
                    if saldo_disponible(membresia) == 0:
                        candidato.estado = 'SIN_SALDO'
                        candidato.save(update_fields=['estado', 'fecha_actualizacion'])
                        notificar_reserva(candidato, 'SIN_SALDO', f'No pudimos confirmar {clase.nombre}: no tienes clases disponibles en esa membresía.')
                        mensaje += f' Solicitud de {socio.nombre_completo} no confirmada por falta de saldo.'
                        continue
                    if tiene_reserva_superpuesta(socio, clase):
                        candidato.estado = 'CRUCE_HORARIO'
                        candidato.save(update_fields=['estado', 'fecha_actualizacion'])
                        notificar_reserva(candidato, 'CRUCE_HORARIO', f'No pudimos confirmar {clase.nombre}: tienes otra clase en ese horario.')
                        mensaje += f' Solicitud de {socio.nombre_completo} no confirmada por cruce de horario.'
                        continue
                    candidato.estado = 'CONFIRMADA'
                    candidato.membresia = membresia
                    candidato.consume_clase = True
                    candidato.promocion_tardia = ahora > inicio - timedelta(hours=2)
                    candidato.save(update_fields=['estado', 'membresia', 'consume_clase', 'promocion_tardia', 'fecha_actualizacion'])
                    notificar_reserva(candidato, 'CUPO_ASIGNADO', f'Se confirmó tu cupo para {clase.nombre} del {clase.fecha:%d/%m/%Y}.')
                    mensaje += f" ¡Cupo asignado automáticamente a {socio.nombre_completo} desde la lista de espera!"
                    break

            return True, mensaje

    except Reserva.DoesNotExist:
        return False, "La reserva no existe."
    except Exception as e:
        return False, f"Error al cancelar la reserva: {str(e)}"


# ------------------------------------------------------------------------------
# 3. Servicio de Cálculo de Aforo en Tiempo Real
# ------------------------------------------------------------------------------
def calcular_aforo_actual() -> dict:
    """
    Calcula la cantidad de personas actualmente dentro de las instalaciones de FitLife.
    Un socio se considera dentro si su último acceso permitido registrado fue una ENTRADA.
    """
    capacidad_maxima = getattr(settings, 'AFORO_MAXIMO', 200)

    # Obtenemos los socios que registran accesos permitidos
    socios_con_movimiento = Socio.all_objects.filter(
        accesos__resultado='PERMITIDO'
    ).distinct()

    personas_dentro = 0
    for socio in socios_con_movimiento:
        if socio.esta_dentro():
            personas_dentro += 1

    disponible = max(capacidad_maxima - personas_dentro, 0)
    porcentaje = round((personas_dentro / capacidad_maxima) * 100, 1) if capacidad_maxima > 0 else 0.0

    return {
        'personas_dentro': personas_dentro,
        'capacidad_maxima': capacidad_maxima,
        'capacidad_disponible': disponible,
        'porcentaje_ocupacion': porcentaje,
        'fecha_hora_actual': timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M:%S"),
        'frecuencia_sondeo_seg': getattr(settings, 'POLLING_INTERVAL_SECONDS', 5),
    }
