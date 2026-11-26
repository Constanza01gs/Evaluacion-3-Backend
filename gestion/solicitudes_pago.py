from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from .models import Membresia, Pago, Reserva, Socio, SolicitudAnulacionPago, NotificacionSocio, NotificacionRecepcion
from .views import es_administrador, es_profesor, es_recepcionista_o_admin, es_socio


@login_required
@never_cache
@transaction.atomic
def solicitar_anulacion_pago(request, pago_id):
    recepcion = es_recepcionista_o_admin(request.user) and not es_profesor(request.user)
    socio = es_socio(request.user) and not es_recepcionista_o_admin(request.user) and not es_profesor(request.user)
    if not recepcion and not socio:
        return HttpResponseForbidden('No tienes permiso para solicitar esta anulación.')
    pagos = Pago.objects.select_for_update().select_related('membresia__socio', 'membresia__plan')
    if socio:
        pagos = pagos.filter(membresia__socio__usuario=request.user)
    pago = get_object_or_404(pagos, pk=pago_id)
    destino = 'socio_pagos' if socio else 'socio_ficha'
    kwargs = {} if socio else {'socio_id': pago.membresia.socio_id}
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    motivo = request.POST.get('motivo', '').strip()
    if pago.anulado:
        messages.warning(request, 'Este pago ya está anulado.')
    elif not motivo or len(motivo) > 500:
        messages.error(request, 'Escribe un motivo de hasta 500 caracteres.')
    elif SolicitudAnulacionPago.objects.filter(pago=pago, estado='PENDIENTE').exists():
        messages.warning(request, 'Ya existe una solicitud de anulación pendiente para este pago.')
    else:
        solicitud = SolicitudAnulacionPago.objects.create(pago=pago, solicitante=request.user, origen='SOCIO' if socio else 'RECEPCION', motivo=motivo)
        admins = get_user_model().objects.filter(Q(is_superuser=True) | Q(groups__name='Administrador'), is_active=True).distinct()
        NotificacionRecepcion.objects.bulk_create([NotificacionRecepcion(destinatario=admin, destino=reverse('solicitudes_anulacion_pago') + f'#pago-{solicitud.pk}', mensaje=f'Nueva solicitud de anulación del pago de {pago.membresia.socio.nombre_completo}.') for admin in admins])
        messages.success(request, 'Solicitud de anulación enviada a administración.')
    return redirect(destino, **kwargs)


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@never_cache
def solicitudes_anulacion_pago(request):
    solicitudes = SolicitudAnulacionPago.objects.filter(estado='PENDIENTE').select_related('pago__membresia__socio', 'pago__membresia__plan', 'solicitante')
    return render(request, 'gestion/solicitudes_pagos.html', {'solicitudes': solicitudes})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@never_cache
@transaction.atomic
def resolver_anulacion_pago(request, solicitud_id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    referencia = get_object_or_404(SolicitudAnulacionPago.objects.only('pago_id'), pk=solicitud_id)
    pago = get_object_or_404(Pago.objects.select_for_update().select_related('membresia__socio'), pk=referencia.pago_id)
    solicitud = get_object_or_404(SolicitudAnulacionPago.objects.select_for_update(), pk=solicitud_id, pago=pago)
    decision = request.POST.get('decision', '')
    comentario = request.POST.get('comentario', '').strip()
    if decision not in ('APROBADA', 'RECHAZADA') or len(comentario) > 500:
        messages.error(request, 'Revisa la decisión y el comentario (máximo 500 caracteres).')
    elif solicitud.estado != 'PENDIENTE':
        messages.warning(request, 'Esta solicitud ya fue revisada.')
    elif pago.anulado:
        messages.warning(request, 'El pago ya está anulado; revisa el historial.')
    else:
        if decision == 'APROBADA':
            from .services import cancelar_reserva_clase, inicio_clase_local
            membresia = Membresia.all_objects.select_for_update().get(pk=pago.membresia_id)
            ahora = timezone.now()
            # Las reservas antiguas pueden no tener membresía asociada; en ese
            # caso solo se consideran las fechas cubiertas por este contrato.
            reservas = Reserva.objects.filter(
                socio_id=membresia.socio_id, estado__in=['CONFIRMADA', 'LISTA_ESPERA'],
                clase__fecha__gte=timezone.localdate(),
            ).filter(Q(membresia=membresia) | Q(membresia__isnull=True, clase__fecha__gte=membresia.fecha_inicio, clase__fecha__lte=membresia.fecha_fin)).select_related('clase').order_by('clase_id', 'pk')
            for reserva in reservas:
                if inicio_clase_local(reserva.clase) <= ahora:
                    continue
                exito, mensaje = cancelar_reserva_clase(reserva.pk)
                if not exito:
                    transaction.set_rollback(True)
                    messages.error(request, 'No se pudo completar la anulación porque hay una reserva que requiere revisión: ' + mensaje)
                    return redirect('solicitudes_anulacion_pago')
                Reserva.objects.filter(pk=reserva.pk).update(consume_clase=False)
            membresia.solicitudes_congelamiento.filter(estado='PENDIENTE').update(
                estado='CANCELADA', cancelada_por=request.user, cancelada_en=ahora)
            membresia.solicitudes_congelamiento.filter(reactivacion_pendiente=True).update(reactivacion_pendiente=False)
            if membresia.activo:
                membresia.soft_delete()
            pago.anulado = True
            pago.motivo_anulacion = solicitud.motivo
            pago.fecha_anulacion = timezone.now()
            pago.anulado_por = request.user
            pago.save(update_fields=['anulado', 'motivo_anulacion', 'fecha_anulacion', 'anulado_por'])
        solicitud.estado = decision
        solicitud.comentario = comentario
        solicitud.revisada = timezone.now()
        solicitud.revisado_por = request.user
        solicitud.save(update_fields=['estado', 'comentario', 'revisada', 'revisado_por'])
        texto = f'Tu solicitud de anulación del pago de {pago.fecha_pago:%d/%m/%Y} fue {solicitud.get_estado_display().lower()}.'
        if decision == 'APROBADA':
            texto += ' La membresía asociada fue desactivada y sus reservas futuras canceladas.'
        if comentario:
            texto += f' Comentario de administración: {comentario}'
        if solicitud.origen == 'SOCIO' or decision == 'APROBADA':
            NotificacionSocio.objects.create(socio=pago.membresia.socio, tipo='ANULACION_PAGO', mensaje=texto)
        if solicitud.origen != 'SOCIO':
            NotificacionRecepcion.objects.create(destinatario=solicitud.solicitante, mensaje=texto)
        messages.success(request, 'Solicitud revisada correctamente.')
    return redirect('solicitudes_anulacion_pago')
