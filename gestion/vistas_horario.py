from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .formularios_horario import HorarioFijoForm, DIAS
from .horarios_fijos import HORIZONTE_DIAS, fechas_reglas, conflicto, asegurar_horarios
from .models import ProgramacionClase, HorarioProgramacion, Clase, Reserva, Profesor, Sala, Actividad
from .views import es_administrador


def form_inicial(programacion=None, clase=None):
    datos = {'desde': timezone.localdate()}
    if clase and not programacion:
        datos.update(nombre=clase.nombre, actividad=clase.actividad_id, profesor=clase.profesor_id, sala=clase.sala_asignada_id, capacidad=clase.capacidad_maxima, desde=max(timezone.localdate(), clase.fecha)+timedelta(days=1), **{f'dia_{clase.fecha.weekday()}': True, f'inicio_{clase.fecha.weekday()}': clase.hora_inicio.strftime('%H:%M'), f'fin_{clase.fecha.weekday()}': clase.hora_fin.strftime('%H:%M')})
    if programacion:
        datos.update(nombre=programacion.nombre, actividad=programacion.actividad_id, profesor=programacion.profesor_id, sala=programacion.sala_id, capacidad=programacion.capacidad, hasta=programacion.hasta)
        if clase:
            datos['desde'] = max(timezone.localdate(), clase.fecha)
        for h in programacion.horarios.all():
            datos.update({f'dia_{h.dia}': True, f'inicio_{h.dia}': h.inicio.strftime('%H:%M'), f'fin_{h.dia}': h.fin.strftime('%H:%M')})
    return HorarioFijoForm(initial=datos)


def horarios(form):
    d=form.cleaned_data
    return [{'dia':i,'nombre':DIAS[i], 'inicio':d[f'inicio_{i}'], 'fin':d[f'fin_{i}']} for i in range(7) if d.get(f'dia_{i}')]

@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
def horario_fijo_guardar(request, clase_id=None):
    clase=get_object_or_404(Clase.objects.select_related('programacion'),pk=clase_id) if clase_id else None
    serie=clase.programacion if clase else None
    if serie and not serie.activa:
        messages.error(request,'Este horario fijo ya no está activo.')
        return redirect('gestion_editar',modulo='clases',objeto_id=clase.pk)
    if request.method != 'POST':
        return redirect('gestion_editar',modulo='clases',objeto_id=clase.pk) if clase else redirect('gestion_nuevo',modulo='clases')
    firmado = None
    if request.POST.get('confirmacion'):
        try:
            firmado=signing.loads(request.POST['confirmacion'], salt='horario-fijo', max_age=900)
            if firmado['usuario'] != request.user.pk or firmado['clase'] != clase_id: raise signing.BadSignature
            datos=firmado['datos']
        except (signing.BadSignature, KeyError):
            messages.error(request,'La revisión venció. Vuelve a revisar los cambios.')
            return redirect('gestion_editar',modulo='clases',objeto_id=clase.pk) if clase else redirect('gestion_nuevo',modulo='clases')
    else:
        datos=request.POST.dict()
    form=HorarioFijoForm(datos)
    if not form.is_valid():
        return render(request,'gestion/horario_fijo_revision.html',{'form':form,'clase':clase,'errores':True})
    d=form.cleaned_data; reglas=horarios(form); fin=min(d['hasta'],d['desde']+timedelta(days=HORIZONTE_DIAS)) if d['hasta'] else d['desde']+timedelta(days=HORIZONTE_DIAS)
    with transaction.atomic():
        Profesor.all_objects.select_for_update().get(pk=d['profesor'].pk)
        Sala.all_objects.select_for_update().get(pk=d['sala'].pk)
        Actividad.all_objects.select_for_update().get(pk=d['actividad'].pk)
        if serie:
            serie=ProgramacionClase.objects.select_for_update().get(pk=serie.pk)
            if not serie.activa:
                messages.error(request,'El horario ya fue modificado. Vuelve a revisarlo.')
                return redirect('clases_list')
        ahora_local = timezone.localtime()
        futuras = Q(fecha__gt=ahora_local.date()) | Q(fecha=ahora_local.date(), hora_inicio__gt=ahora_local.time().replace(tzinfo=None))
        afectadas=list(Clase.objects.select_for_update().filter(futuras, programacion=serie, fecha__gte=d['desde'], excepcion_programacion=False).order_by('fecha','hora_inicio')) if serie else []
        reservas=list(Reserva.objects.filter(clase__in=afectadas,estado__in=['CONFIRMADA','LISTA_ESPERA']).select_related('socio','clase'))
        excluir=[c.pk for c in afectadas]
        conflictos=[fecha for fecha,r in fechas_reglas(d['desde'],fin,reglas) if conflicto(fecha,r,d['profesor'].pk,d['sala'].pk,excluir,sala_nombre=d['sala'].nombre)]
        revision_actual = [[c.pk, c.fecha.isoformat(), c.hora_inicio.isoformat(), c.hora_fin.isoformat()] for c in afectadas]
        cambio_desde_revision = firmado is not None and firmado.get('sesiones') != revision_actual
        if cambio_desde_revision:
            messages.warning(request, 'Las clases afectadas cambiaron desde la revisión anterior. Comprueba la lista nuevamente.')
        if reservas or conflictos or not request.POST.get('confirmacion') or cambio_desde_revision:
            token=signing.dumps({'usuario':request.user.pk,'clase':clase_id,'datos':datos,'sesiones':revision_actual},salt='horario-fijo')
            return render(request,'gestion/horario_fijo_revision.html',{'form':form,'clase':clase,'reglas':reglas,'afectadas':afectadas,'reservas':reservas,'conflictos':conflictos,'confirmacion':token,'fin':fin})
        if serie:
            serie.activa=False; serie.hasta=d['desde']-timedelta(days=1); serie.save(update_fields=['activa','hasta'])
            Clase.objects.filter(pk__in=excluir).update(activo=False,fecha_eliminacion=timezone.now())
        nuevo=ProgramacionClase.objects.create(nombre=d['nombre'],actividad=d['actividad'],profesor=d['profesor'],sala=d['sala'],capacidad=d['capacidad'],desde=d['desde'],hasta=d['hasta'])
        HorarioProgramacion.objects.bulk_create([HorarioProgramacion(programacion=nuevo,dia=r['dia'],inicio=r['inicio'],fin=r['fin']) for r in reglas])
        asegurar_horarios()
        nuevo.refresh_from_db()
        if nuevo.aviso_conflicto:
            transaction.set_rollback(True)
            messages.error(request,'No se pudo guardar el horario por un conflicto. Vuelve a revisar la disponibilidad.')
            return redirect('gestion_editar',modulo='clases',objeto_id=clase.pk) if clase else redirect('gestion_nuevo',modulo='clases')
    messages.success(request,'Horario fijo guardado. Las próximas sesiones estarán disponibles automáticamente.')
    return redirect('clases_list')
@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def horario_fijo_detener(request, clase_id):
    from django.http import HttpResponseNotAllowed
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    clase=get_object_or_404(Clase.objects.select_related('programacion'),pk=clase_id)
    if not clase.programacion_id:
        messages.warning(request,'Esta clase no tiene un horario fijo.')
        return redirect('gestion_editar',modulo='clases',objeto_id=clase.pk)
    serie=ProgramacionClase.objects.select_for_update().get(pk=clase.programacion_id)
    if not serie.activa:
        messages.warning(request,'Este horario fijo ya está detenido.')
        return redirect('clases_list')
    ahora=timezone.localtime()
    futuras=Q(fecha__gt=ahora.date()) | Q(fecha=ahora.date(),hora_inicio__gt=ahora.time().replace(tzinfo=None))
    afectadas=list(Clase.objects.select_for_update().filter(futuras,programacion=serie,excepcion_programacion=False).order_by('fecha','hora_inicio'))
    reservas=list(Reserva.objects.filter(clase__in=afectadas,estado__in=['CONFIRMADA','LISTA_ESPERA']).select_related('socio','clase'))
    confirmar=request.POST.get('confirmar') == 'si'
    if not confirmar or reservas:
        return render(request,'gestion/horario_fijo_detener.html',{'clase':clase,'serie':serie,'afectadas':afectadas,'reservas':reservas})
    serie.activa=False
    serie.hasta=timezone.localdate()
    serie.save(update_fields=['activa','hasta'])
    Clase.objects.filter(pk__in=[c.pk for c in afectadas]).update(activo=False,fecha_eliminacion=timezone.now())
    messages.success(request,'Horario fijo detenido. Las sesiones futuras sin reservas se han retirado de la programación.')
    return redirect('clases_list')
