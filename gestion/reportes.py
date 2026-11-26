from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Sum
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .models import Clase, Membresia, Pago, RegistroAcceso, Reserva, Socio
from django.db.models.functions import TruncMonth
from .views import es_administrador


def datos_reporte(params):
    hoy = timezone.localdate()
    try:
        desde = date.fromisoformat(params.get('desde') or hoy.replace(day=1).isoformat())
        hasta = date.fromisoformat(params.get('hasta') or hoy.isoformat())
    except (ValueError, TypeError):
        raise ValueError('Ingresa fechas válidas.')
    if desde > hasta:
        raise ValueError('La fecha inicial debe ser anterior o igual a la fecha final.')
    if (hasta - desde).days > 3660:
        raise ValueError('El período no puede superar diez años.')

    pagos = Pago.objects.filter(fecha_pago__date__range=(desde, hasta), anulado=False)
    membresias = Membresia.all_objects.filter(fecha_creacion__date__range=(desde, hasta))
    accesos = RegistroAcceso.objects.filter(fecha_hora__date__range=(desde, hasta), tipo='ENTRADA', resultado='PERMITIDO')
    asistencias = Reserva.objects.filter(clase__fecha__range=(desde, hasta), asistencia='ASISTIO')
    planes = list(pagos.values('membresia__plan__nombre').annotate(total=Sum('monto'), cantidad=Count('pk')).order_by('-total', 'membresia__plan__nombre'))
    clases = list(asistencias.values('clase__nombre').annotate(total=Count('pk')).order_by('-total', 'clase__nombre')[:10])
    max_planes = max((p['total'] for p in planes), default=Decimal(0))
    max_clases = max((c['total'] for c in clases), default=0)
    for p in planes:
        p['porcentaje'] = round(float(p['total'] / max_planes) * 100, 2) if max_planes else 0
    for c in clases:
        c['porcentaje'] = round(c['total'] / max_clases * 100, 2) if max_clases else 0
    pagos_anulados = Pago.objects.filter(fecha_pago__date__range=(desde, hasta), anulado=True)
    reservas = Reserva.objects.filter(clase__fecha__range=(desde, hasta))
    sesiones = Clase.all_objects.filter(fecha__range=(desde, hasta))
    metodos = list(pagos.values('metodo_pago').annotate(total=Sum('monto'), cantidad=Count('pk')).order_by('-total'))
    nombres_metodos = dict(Pago.METODOS_PAGO)
    for metodo in metodos:
        metodo['nombre'] = nombres_metodos.get(metodo['metodo_pago'], metodo['metodo_pago'])
    mensual = list(pagos.annotate(mes=TruncMonth('fecha_pago')).values('mes').annotate(total=Sum('monto'), cantidad=Count('pk')).order_by('mes'))
    return {'desde': desde, 'hasta': hasta, 'ingresos': pagos.aggregate(total=Sum('monto'))['total'] or 0,
            'pagos_count': pagos.count(), 'membresias_count': membresias.count(), 'accesos_count': accesos.count(),
            'asistencias_count': asistencias.count(), 'planes': planes, 'clases': clases,
            'socios_nuevos': Socio.all_objects.filter(fecha_registro__date__range=(desde, hasta)).count(),
            'socios_activos': Socio.objects.count(),
            'membresias_vigentes': Membresia.objects.filter(estado__in=['ACTIVA', 'CONGELADA'], fecha_inicio__lte=hoy, fecha_fin__gte=hoy).count(),
            'pagos_anulados': pagos_anulados.count(), 'monto_anulado': pagos_anulados.aggregate(total=Sum('monto'))['total'] or 0,
            'metodos': metodos, 'mensual': mensual,
            'accesos_denegados': RegistroAcceso.objects.filter(fecha_hora__date__range=(desde, hasta), tipo='ENTRADA', resultado='DENEGADO').count(),
            'clases_periodo': sesiones.filter(activo=True).count(), 'clases_inactivas': sesiones.filter(activo=False).count(),
            'reservas_confirmadas': reservas.filter(estado='CONFIRMADA').count(), 'reservas_canceladas': reservas.filter(estado='CANCELADA').count(),
            'inasistencias': reservas.filter(asistencia='NO_ASISTIO').count()}


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@never_cache
@require_GET
def reportes_view(request):
    try:
        datos = datos_reporte(request.GET)
    except ValueError as error:
        datos = datos_reporte({})
        datos['error_filtro'] = str(error)
    return render(request, 'gestion/reportes.html', datos)


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@never_cache
@require_GET
def reportes_pdf_view(request):
    try:
        datos = datos_reporte(request.GET)
    except ValueError as error:
        return HttpResponseBadRequest(str(error))
    from .reporte_pdf import generar_pdf
    contenido = generar_pdf(datos)
    response = HttpResponse(contenido, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="fitlife-reporte-{}-{}.pdf"'.format(datos['desde'], datos['hasta'])
    return response
