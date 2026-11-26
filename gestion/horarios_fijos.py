from datetime import timedelta, datetime
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import ProgramacionClase, Clase, Reserva, Profesor, Sala, Actividad

HORIZONTE_DIAS = 90

def fechas_reglas(desde, hasta, reglas):
    fecha = desde
    while fecha <= hasta:
        regla = next((r for r in reglas if r['dia'] == fecha.weekday()), None)
        if regla:
            yield fecha, regla
        fecha += timedelta(days=1)

def conflicto(fecha, regla, profesor_id, sala_id, excluir=(), sala_nombre=None):
    clases = Clase.objects.filter(fecha=fecha, hora_inicio__lt=regla['fin'], hora_fin__gt=regla['inicio']).exclude(pk__in=excluir)
    filtro = Q(profesor_id=profesor_id) | Q(sala_asignada_id=sala_id)
    if sala_nombre:
        filtro |= Q(sala_asignada__isnull=True, sala__iexact=sala_nombre)
    return clases.filter(filtro).exists()

def asegurar_horarios():
    hoy=timezone.localdate(); limite=hoy+timedelta(days=HORIZONTE_DIAS)
    ids=list(ProgramacionClase.objects.filter(activa=True).filter(Q(generado_hasta__isnull=True)|Q(generado_hasta__lt=limite)).values_list('pk',flat=True))
    for pk in ids:
        with transaction.atomic():
            serie=ProgramacionClase.objects.select_for_update().select_related('profesor','sala','actividad').get(pk=pk)
            Profesor.all_objects.select_for_update().filter(pk=serie.profesor_id).first()
            Sala.all_objects.select_for_update().filter(pk=serie.sala_id).first()
            Actividad.all_objects.select_for_update().filter(pk=serie.actividad_id).first()
            if not serie.activa: continue
            fin=min(limite,serie.hasta) if serie.hasta else limite
            inicio=max(hoy,serie.desde,(serie.generado_hasta+timedelta(days=1)) if serie.generado_hasta else serie.desde)
            if fin < inicio: continue
            reglas=[{'dia':h.dia,'inicio':h.inicio,'fin':h.fin,'obj':h} for h in serie.horarios.all()]
            aviso=''
            for fecha,r in fechas_reglas(inicio,fin,reglas):
                if fecha == hoy and r['inicio'] <= timezone.localtime().time().replace(tzinfo=None): continue
                if Clase.all_objects.filter(horario_programado=r['obj'],fecha=fecha).exists(): continue
                if not (serie.profesor.activo and serie.sala.activo and serie.actividad.activo) or conflicto(fecha,r,serie.profesor_id,serie.sala_id,sala_nombre=serie.sala.nombre):
                    aviso=f'No se pudo programar la clase del {fecha:%d/%m/%Y}: revisa la disponibilidad del profesor y la sala.'
                    fin=fecha-timedelta(days=1)
                    break
                Clase.objects.create(programacion=serie,horario_programado=r['obj'],nombre=serie.nombre,actividad=serie.actividad,profesor=serie.profesor,sala_asignada=serie.sala,sala=serie.sala.nombre[:50],fecha=fecha,hora_inicio=r['inicio'],hora_fin=r['fin'],capacidad_maxima=serie.capacidad)
            serie.generado_hasta=fin
            serie.aviso_conflicto=aviso
            serie.save(update_fields=['generado_hasta','aviso_conflicto'])
