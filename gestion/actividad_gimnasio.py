from datetime import date
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from .models import Clase, RegistroAcceso, Sala
from .services import calcular_aforo_actual
from .views import es_administrador


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@never_cache
def actividad_gimnasio(request):
    ahora = timezone.localtime()
    clases = list(Clase.objects.filter(fecha=ahora.date()).select_related('sala_asignada', 'profesor').annotate(
        confirmadas=Count('reservas', filter=Q(reservas__estado='CONFIRMADA')),
        asistencias=Count('reservas', filter=Q(reservas__estado='CONFIRMADA', reservas__asistencia='ASISTIO')),
    ).order_by('hora_inicio', 'pk'))
    salas = list(Sala.objects.all())
    filas = []
    sala_nombres = {s.nombre.strip().casefold() for s in salas}
    for sala in salas:
        sesiones = [c for c in clases if c.sala_asignada_id == sala.pk or (not c.sala_asignada_id and c.sala.strip().casefold() == sala.nombre.strip().casefold())]
        en_curso = next((c for c in sesiones if c.hora_inicio <= ahora.time() < c.hora_fin), None)
        proxima = next((c for c in sesiones if c.hora_inicio > ahora.time()), None)
        filas.append({'nombre': sala.nombre, 'clase': en_curso or proxima, 'en_curso': bool(en_curso)})
    nombres_extra = sorted({c.sala for c in clases if not c.sala_asignada_id and c.sala.strip().casefold() not in sala_nombres})
    for nombre in nombres_extra:
        sesiones = [c for c in clases if not c.sala_asignada_id and c.sala == nombre]
        en_curso = next((c for c in sesiones if c.hora_inicio <= ahora.time() < c.hora_fin), None)
        proxima = next((c for c in sesiones if c.hora_inicio > ahora.time()), None)
        filas.append({'nombre': nombre or 'Sala sin especificar', 'clase': en_curso or proxima, 'en_curso': bool(en_curso)})
    en_curso = [c for c in clases if c.hora_inicio <= ahora.time() < c.hora_fin]
    registros = RegistroAcceso.objects.select_related('socio').order_by('-fecha_hora', '-pk')
    fecha = request.GET.get('fecha', '').strip()
    consulta = request.GET.get('q', '').strip()
    resultado = request.GET.get('resultado', '').strip()
    error_fecha = False
    if fecha:
        try:
            fecha_valida = date.fromisoformat(fecha)
            registros = registros.filter(fecha_hora__date=fecha_valida)
        except ValueError:
            error_fecha = True
            registros = registros.none()
    if consulta:
        from django.db.models import Value
        from django.db.models.functions import Concat, Replace
        limpio = consulta.replace('.', '').replace('-', '').replace(' ', '')
        registros = registros.annotate(nombre_completo_busqueda=Concat('socio__nombres', Value(' '), 'socio__apellidos'), rut_busqueda=Replace(Replace(Replace('socio__rut', Value('.'), Value('')), Value('-'), Value('')), Value(' '), Value('')))
        filtro = Q(nombre_completo_busqueda__icontains=consulta)
        if limpio:
            filtro |= Q(rut_busqueda__icontains=limpio)
        registros = registros.filter(filtro)
    if resultado in ('PERMITIDO', 'DENEGADO'):
        registros = registros.filter(resultado=resultado)
    else:
        resultado = ''
    pagina = Paginator(registros, 20).get_page(request.GET.get('page'))
    filtros = request.GET.copy()
    filtros.pop('page', None)
    return render(request, 'gestion/actividad_gimnasio.html', {'aforo': calcular_aforo_actual(), 'clases_en_curso': len(en_curso), 'salas_en_curso': sum(f['en_curso'] for f in filas), 'salas': filas, 'registros': pagina, 'fecha_filtro': fecha, 'consulta': consulta, 'resultado_filtro': resultado, 'error_fecha': error_fecha, 'filtros_query': filtros.urlencode()})
