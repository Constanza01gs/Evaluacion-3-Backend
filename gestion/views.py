"""
Vistas web y endpoints API para FitLife.
Incluye:
- Vistas HTML responsivas con autenticación y redirección por roles (Administrador y Recepción).
- Endpoints REST para AJAX / frontend asíncrono (validación QR, cálculo de aforo, reservas).
- Vista de error 404 personalizada.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.db import transaction
from django.conf import settings

from rest_framework import viewsets, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import (
    Socio, Plan, Membresia, Pago, Profesor,
    Clase, Reserva, RegistroAcceso
)
from .serializers import (
    SocioSerializer, PlanSerializer, MembresiaSerializer,
    PagoSerializer, ProfesorSerializer, ClaseSerializer,
    ReservaSerializer, RegistroAccesoSerializer, ValidarAccesoQRSerializer
)
from .services import (
    validar_y_registrar_acceso,
    gestionar_reserva_clase,
    cancelar_reserva_clase,
    calcular_aforo_actual
)
from .forms import SocioForm, PlanForm, MembresiaForm, PagoForm, ProfesorForm, ClaseForm, AnularPagoForm


# ------------------------------------------------------------------------------
# Helpers y Verificadores de Roles para Vistas Web
# ------------------------------------------------------------------------------
def es_administrador(user):
    """Comprueba si el usuario tiene rol de Administrador o es Superusuario."""
    if not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name='Administrador').exists()


def es_recepcionista_o_admin(user):
    """Comprueba si el usuario pertenece al personal (Recepción o Administrador)."""
    if not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name__in=['Administrador', 'Recepcion']).exists()


# ------------------------------------------------------------------------------
# Vistas de Autenticación y Redirección
# ------------------------------------------------------------------------------
def login_view(request):
    """
    Vista de inicio de sesión con el diseño split visual de la imagen de referencia.
    """
    if request.user.is_authenticated:
        return redirect('dashboard_redirect')

    if request.method == 'POST':
        usuario = request.POST.get('username', '').strip()
        clave = request.POST.get('password', '').strip()
        recordarme = request.POST.get('recordarme')

        user = authenticate(request, username=usuario, password=clave)
        if user is not None:
            if not user.is_active:
                messages.error(request, "Tu cuenta de usuario se encuentra deshabilitada.")
                return render(request, 'login.html')

            login(request, user)
            if not recordarme:
                # La sesión expira al cerrar el navegador
                request.session.set_expiry(0)

            messages.success(request, f"¡Bienvenido/a, {user.first_name or user.username}!")
            return redirect('dashboard_redirect')
        else:
            messages.error(request, "Usuario o contraseña incorrectos. Por favor, reintenta.")

    return render(request, 'login.html')


def logout_view(request):
    """Cierra la sesión del usuario actual."""
    logout(request)
    messages.info(request, "Has cerrado sesión correctamente.")
    return redirect('login')


@login_required
def dashboard_redirect(request):
    """
    Redirige al usuario al panel correspondiente según su rol:
    - Superuser / Administrador -> Panel de Control (/admin-dashboard/)
    - Recepción -> Panel de Recepción (/recepcion-dashboard/)
    """
    if es_administrador(request.user):
        return redirect('admin_dashboard')
    elif request.user.groups.filter(name='Recepcion').exists():
        return redirect('recepcion_dashboard')
    # Por defecto, si tiene acceso al personal
    return redirect('recepcion_dashboard')


# ------------------------------------------------------------------------------
# Paneles Principales (Administrador y Recepción)
# ------------------------------------------------------------------------------
@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
def admin_dashboard(request):
    """
    Panel de Control del Administrador (identidad visual completa según referencia).
    Muestra KPIs globales, evolución, finanzas del día y estado de membresías.
    """
    hoy = timezone.localdate()

    # 1. KPIs principales
    total_socios_activos = Socio.objects.filter(activo=True).count()
    total_membresias_activas = Membresia.objects.filter(activo=True, estado='ACTIVA', fecha_inicio__lte=hoy, fecha_fin__gte=hoy).count()
    clases_hoy = Clase.objects.filter(fecha=hoy, activo=True)
    total_clases_hoy = clases_hoy.count()

    # Ingresos registrados hoy
    ingresos_hoy = Pago.objects.filter(fecha_pago__date=hoy, anulado=False).aggregate(total=Sum('monto'))['total'] or 0

    # 2. Desglose de membresías por plan (para el gráfico donut)
    desglose_planes = Plan.objects.filter(activo=True).annotate(
        total_activas=Count('membresias', filter=Q(membresias__estado='ACTIVA', membresias__activo=True, membresias__fecha_inicio__lte=hoy, membresias__fecha_fin__gte=hoy))
    )

    # 3. Últimos pagos registrados
    ultimos_pagos = Pago.objects.filter(anulado=False).select_related('membresia__socio', 'membresia__plan').order_by('-fecha_pago')[:6]

    # 4. Clases del día con ocupación
    clases_con_datos = []
    for c in clases_hoy[:5]:
        confirmadas = c.total_confirmadas()
        capacidad = c.capacidad_maxima
        porcentaje = int((confirmadas / capacidad) * 100) if capacidad > 0 else 0
        clases_con_datos.append({
            'clase': c,
            'confirmadas': confirmadas,
            'capacidad': capacidad,
            'porcentaje': porcentaje,
        })

    contexto = {
        'rol_usuario': 'Administrador',
        'nombre_usuario': request.user.get_full_name() or request.user.username,
        'iniciales_usuario': (request.user.first_name[:1] + request.user.last_name[:1]).upper() if request.user.first_name and request.user.last_name else request.user.username[:2].upper(),
        'hoy': hoy,
        'socios_activos': total_socios_activos,
        'membresias_activas': total_membresias_activas,
        'clases_hoy_count': total_clases_hoy,
        'ingresos_hoy': ingresos_hoy,
        'aforo': calcular_aforo_actual(),
        'desglose_planes': desglose_planes,
        'ultimos_pagos': ultimos_pagos,
        'clases_hoy': clases_con_datos,
    }
    return render(request, 'admin_dashboard.html', contexto)


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def recepcion_dashboard(request):
    """
    Panel de Recepción (enfoque operativo: QR, Aforo en vivo, Próximas Clases, Últimos Accesos).
    """
    hoy = timezone.localdate()

    # Aforo inicial
    datos_aforo = calcular_aforo_actual()

    # Próximas clases de hoy
    ahora_hora = timezone.now().time()
    proximas_clases = Clase.objects.filter(fecha=hoy, activo=True).order_by('hora_inicio')[:5]
    clases_data = []
    for c in proximas_clases:
        clases_data.append({
            'clase': c,
            'ocupadas': c.total_confirmadas(),
            'capacidad': c.capacidad_maxima,
            'en_curso': c.hora_inicio <= ahora_hora <= c.hora_fin,
        })

    # Últimos accesos de hoy
    ultimos_accesos = RegistroAcceso.objects.select_related('socio').filter(fecha_hora__date=hoy).order_by('-fecha_hora')[:8]

    contexto = {
        'rol_usuario': 'Recepción',
        'nombre_usuario': request.user.get_full_name() or request.user.username,
        'iniciales_usuario': (request.user.first_name[:1] + request.user.last_name[:1]).upper() if request.user.first_name and request.user.last_name else request.user.username[:2].upper(),
        'hoy': hoy,
        'aforo': datos_aforo,
        'proximas_clases': clases_data,
        'ultimos_accesos': ultimos_accesos,
    }
    return render(request, 'recepcion_dashboard.html', contexto)


# ------------------------------------------------------------------------------
# Vistas Web de Gestión (Socios, Clases, Pagos, Aforo)
# ------------------------------------------------------------------------------
@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def socios_list_view(request):
    """Listado y búsqueda de socios con visualización de estado."""
    query = request.GET.get('q', '').strip()
    es_admin = es_administrador(request.user)

    ver_inactivos = es_admin and request.GET.get('estado') == 'inactivos'
    socios = Socio.all_objects.filter(activo=False) if ver_inactivos else Socio.objects.all()
    if query:
        socios = socios.filter(
            Q(nombres__icontains=query) |
            Q(apellidos__icontains=query) |
            Q(rut__icontains=query) |
            Q(email__icontains=query) |
            Q(codigo_qr__icontains=query)
        )

    contexto = {
        'socios': socios,
        'query': query,
        'es_admin': es_admin,
        'ver_inactivos': ver_inactivos,
    }
    return render(request, 'socios/list.html', contexto)


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def socio_form_view(request, socio_id=None):
    socio = get_object_or_404(Socio.objects, pk=socio_id) if socio_id else None
    form = SocioForm(request.POST or None, instance=socio)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Socio guardado correctamente.')
        return redirect('socios_list')
    return render(request, 'socios/form.html', {'form': form, 'socio': socio})


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def gestion_list_view(request, modulo):
    ver_inactivos = es_administrador(request.user) and request.GET.get('estado') == 'inactivos'
    if modulo == 'planes':
        objetos, titulo = (Plan.all_objects.filter(activo=False) if ver_inactivos else Plan.objects.all()), 'Planes'
    elif modulo == 'membresias':
        objetos, titulo = (Membresia.all_objects.filter(activo=False) if ver_inactivos else Membresia.objects.all()).select_related('socio', 'plan'), 'Membresías'
        busqueda = request.GET.get('q', '').strip()
        plan = request.GET.get('plan', '')
        estado = request.GET.get('estado_m', '')
        if busqueda:
            objetos = objetos.filter(Q(socio__nombres__icontains=busqueda) | Q(socio__apellidos__icontains=busqueda) | Q(socio__rut__icontains=busqueda))
        if plan.isdigit():
            objetos = objetos.filter(plan_id=int(plan))
        hoy = timezone.localdate()
        if estado == 'ACTIVA':
            objetos = objetos.filter(estado='ACTIVA', fecha_fin__gte=hoy)
        elif estado == 'VENCIDA':
            objetos = objetos.filter(Q(estado='VENCIDA') | (Q(fecha_fin__lt=hoy) & ~Q(estado='CONGELADA')))
        elif estado == 'CONGELADA':
            objetos = objetos.filter(estado='CONGELADA')
    elif modulo == 'pagos':
        objetos, titulo = Pago.objects.select_related('membresia__socio', 'membresia__plan'), 'Pagos'
    elif modulo == 'profesores':
        objetos, titulo = (Profesor.all_objects.filter(activo=False) if ver_inactivos else Profesor.objects.all()), 'Profesores'
    else:
        from django.http import Http404
        raise Http404
    return render(request, 'gestion/list.html', {'objetos': objetos, 'modulo': modulo, 'titulo': titulo, 'es_admin': es_administrador(request.user), 'ver_inactivos': ver_inactivos, 'planes_filtro': Plan.objects.all() if modulo == 'membresias' else []})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def gestion_estado_view(request, modulo, objeto_id, accion):
    from django.http import Http404
    modelos = {'planes': Plan, 'membresias': Membresia, 'profesores': Profesor, 'clases': Clase}
    if modulo not in modelos or accion not in ('desactivar', 'restaurar'):
        raise Http404
    objetos = modelos[modulo].all_objects
    if request.method == 'POST' and modulo == 'clases':
        objetos = objetos.select_for_update()
    objeto = get_object_or_404(objetos, pk=objeto_id)
    destino = 'clases_list' if modulo == 'clases' else 'gestion_list'
    destino_kwargs = {} if modulo == 'clases' else {'modulo': modulo}
    if request.method == 'POST':
        if accion == 'desactivar':
            if modulo == 'planes' and Membresia.objects.filter(plan=objeto, fecha_fin__gte=timezone.localdate()).exists():
                messages.error(request, 'No se puede desactivar el plan: tiene membresías vigentes o futuras.')
            elif modulo == 'profesores' and Clase.objects.filter(profesor=objeto, fecha__gte=timezone.localdate()).exists():
                messages.error(request, 'No se puede desactivar al profesor: tiene clases programadas.')
            elif modulo == 'clases' and Reserva.objects.filter(clase=objeto, estado__in=['CONFIRMADA', 'LISTA_ESPERA']).exists():
                messages.error(request, 'No se puede desactivar la clase mientras tenga reservas o lista de espera.')
            elif modulo == 'membresias' and Reserva.objects.filter(socio=objeto.socio, clase__fecha__gte=timezone.localdate(), estado__in=['CONFIRMADA', 'LISTA_ESPERA']).exists():
                messages.error(request, 'El socio tiene reservas próximas. Cancélalas antes de desactivar la membresía.')
            else:
                objeto.soft_delete()
                messages.success(request, 'Registro desactivado; se conserva su historial.')
        else:
            objeto.restore()
            messages.success(request, 'Registro restaurado.')
        return redirect(destino, **destino_kwargs)
    return render(request, 'gestion/estado.html', {'objeto': objeto, 'accion': accion, 'modulo': modulo})


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
@transaction.atomic
def gestion_form_view(request, modulo, objeto_id=None):
    opciones = {'planes': (Plan, PlanForm, 'plan'), 'membresias': (Membresia, MembresiaForm, 'membresía'), 'pagos': (Pago, PagoForm, 'pago'), 'profesores': (Profesor, ProfesorForm, 'profesor'), 'clases': (Clase, ClaseForm, 'clase')}
    if modulo not in opciones:
        from django.http import Http404
        raise Http404
    if modulo in ('planes', 'profesores', 'clases') and not es_administrador(request.user):
        return HttpResponseForbidden('Solo administración puede modificar este registro.')
    if modulo == 'pagos' and objeto_id is not None:
        return HttpResponseForbidden('Los pagos registrados no se editan desde esta pantalla.')
    modelo, formulario, titulo = opciones[modulo]
    objetos = modelo.objects
    if modulo == 'clases' and objeto_id and request.method == 'POST':
        objetos = objetos.select_for_update()
    objeto = get_object_or_404(objetos, pk=objeto_id) if objeto_id else None
    form = formulario(request.POST or None, instance=objeto)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'{titulo.capitalize()} guardado correctamente.')
        if modulo == 'clases':
            return redirect('clases_list')
        return redirect('gestion_list', modulo=modulo)
    return render(request, 'gestion/form.html', {'form': form, 'titulo': titulo, 'modulo': modulo, 'objeto': objeto})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
def anular_pago_view(request, pago_id):
    from django.db import transaction
    pago = get_object_or_404(Pago, pk=pago_id)
    form = AnularPagoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            pago = Pago.objects.select_for_update().get(pk=pago_id)
            if pago.anulado:
                messages.warning(request, 'Este pago ya fue anulado.')
            else:
                pago.anulado = True
                pago.motivo_anulacion = form.cleaned_data['motivo']
                pago.fecha_anulacion = timezone.now()
                pago.anulado_por = request.user
                pago.save(update_fields=['anulado', 'motivo_anulacion', 'fecha_anulacion', 'anulado_por'])
                messages.success(request, 'Pago anulado. El registro se conserva en el historial.')
        return redirect('gestion_list', modulo='pagos')
    return render(request, 'gestion/anular_pago.html', {'form': form, 'pago': pago})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
def socio_eliminar_logico_view(request, socio_id):
    """Aplica eliminación lógica a un socio para preservar su historial."""
    socio = get_object_or_404(Socio.all_objects, id=socio_id)
    if request.method == 'POST':
        socio.soft_delete()
        messages.success(request, f"El socio {socio.nombre_completo} ha sido dado de baja lógica. Su historial se preserva.")
        return redirect('socios_list')
    return render(request, 'socios/confirmar_baja.html', {'socio': socio})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
def socio_restaurar_logico_view(request, socio_id):
    """Restaura un socio dado de baja lógica."""
    socio = get_object_or_404(Socio.all_objects, id=socio_id)
    if request.method == 'POST':
        socio.restore()
        messages.success(request, f"El socio {socio.nombre_completo} ha sido reactivado exitosamente.")
        return redirect('socios_list')
    return render(request, 'socios/confirmar_restaurar.html', {'socio': socio})


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def clases_list_view(request):
    """Listado de clases con reserva y lista de espera."""
    hoy = timezone.localdate()
    es_admin = es_administrador(request.user)
    ver_inactivas = es_admin and request.GET.get('estado') == 'inactivas'
    clases = (Clase.all_objects.filter(activo=False) if ver_inactivas else Clase.objects.filter(fecha__gte=hoy)).order_by('fecha', 'hora_inicio')
    socios = Socio.objects.filter(activo=True)

    contexto = {
        'clases': clases,
        'socios': socios,
        'es_admin': es_admin,
        'ver_inactivas': ver_inactivas,
    }
    return render(request, 'clases/list.html', contexto)


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def reservar_clase_web_view(request, clase_id):
    """Procesa una reserva desde la interfaz web."""
    if request.method == 'POST':
        socio_id = request.POST.get('socio_id')
        if not socio_id:
            messages.error(request, "Debe seleccionar un socio válido.")
            return redirect('clases_list')

        exito, mensaje, reserva = gestionar_reserva_clase(int(clase_id), int(socio_id))
        if exito:
            messages.success(request, mensaje)
        else:
            messages.warning(request, mensaje)

    return redirect('clases_list')


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def cancelar_reserva_web_view(request, reserva_id):
    """Cancela una reserva y promueve automáticamente de la lista de espera."""
    if request.method == 'POST':
        exito, mensaje = cancelar_reserva_clase(int(reserva_id))
        if exito:
            messages.success(request, mensaje)
        else:
            messages.error(request, mensaje)
    return redirect('clases_list')


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def control_acceso_view(request):
    """Vista dedicada a la simulación y validación de códigos QR."""
    socios_ejemplo = Socio.objects.filter(activo=True)[:10]
    ultimos_accesos = RegistroAcceso.objects.select_related('socio').order_by('-fecha_hora')[:10]
    contexto = {
        'socios_ejemplo': socios_ejemplo,
        'ultimos_accesos': ultimos_accesos,
    }
    return render(request, 'control_acceso.html', contexto)


# ------------------------------------------------------------------------------
# Endpoints de API para Simulación QR y Sondeo de Aforo
# ------------------------------------------------------------------------------
class PersonalFitLife(permissions.BasePermission):
    """Permite operar únicamente a personal con un rol reconocido."""
    def has_permission(self, request, view):
        return es_recepcionista_o_admin(request.user)


class LecturaPersonalEscrituraAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return es_recepcionista_o_admin(request.user)
        return es_administrador(request.user)


class ValidarAccesoAPIView(APIView):
    """
    Endpoint POST /api/acceso/validar/
    Recibe un código QR o identificador, evalúa las reglas de negocio y registra el acceso.
    Retorna la decisión, detalles del socio y latencia medida en milisegundos.
    """
    permission_classes = [PersonalFitLife]

    def post(self, request):
        serializer = ValidarAccesoQRSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        identificador = serializer.validated_data['identificador']
        tipo = serializer.validated_data['tipo']
        metodo = serializer.validated_data.get('metodo', 'SIMULACION_QR')

        resultado = validar_y_registrar_acceso(identificador, tipo=tipo, metodo=metodo)

        socio_data = None
        if resultado['socio']:
            s = resultado['socio']
            m = s.membresia_vigente()
            socio_data = {
                'id': s.id,
                'rut': s.rut,
                'nombre_completo': s.nombre_completo,
                'codigo_qr': s.codigo_qr,
                'membresia_plan': m.plan.nombre if m else "Sin plan",
                'membresia_valida_hasta': m.fecha_fin.strftime("%d/%m/%Y") if m else "N/A",
                'esta_dentro': s.esta_dentro(),
            }

        response_payload = {
            'autorizado': resultado['autorizado'],
            'motivo': resultado['motivo'],
            'tipo': resultado['tipo'],
            'tiempo_respuesta_ms': resultado['tiempo_respuesta_ms'],
            'registro_id': resultado['registro_id'],
            'socio': socio_data,
        }
        return Response(response_payload, status=status.HTTP_200_OK)


class AforoAPIView(APIView):
    """
    Endpoint GET /api/aforo/
    Retorna las cifras actuales de aforo para el sondeo periódico del frontend.
    """
    permission_classes = [PersonalFitLife]

    def get(self, request):
        datos = calcular_aforo_actual()
        return Response(datos, status=status.HTTP_200_OK)


# ------------------------------------------------------------------------------
# ViewSets de Django REST Framework (CRUD API completo)
# ------------------------------------------------------------------------------
class SocioViewSet(viewsets.ModelViewSet):
    """CRUD REST de Socios."""
    queryset = Socio.objects.all()
    serializer_class = SocioSerializer
    permission_classes = [PersonalFitLife]

    def get_permissions(self):
        if self.action == 'destroy':
            return [LecturaPersonalEscrituraAdmin()]
        return super().get_permissions()

    def perform_destroy(self, instance):
        # Implementa borrado lógico también en la API REST
        instance.soft_delete()


class PlanViewSet(viewsets.ModelViewSet):
    """CRUD REST de Planes."""
    queryset = Plan.objects.all()
    serializer_class = PlanSerializer
    permission_classes = [LecturaPersonalEscrituraAdmin]

    def destroy(self, request, *args, **kwargs):
        return Response({'detail': 'Desactiva planes desde la interfaz de FitLife.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)


class MembresiaViewSet(viewsets.ModelViewSet):
    """CRUD REST de Membresías."""
    queryset = Membresia.objects.all()
    serializer_class = MembresiaSerializer
    permission_classes = [PersonalFitLife]

    def get_permissions(self):
        if self.action == 'destroy':
            return [LecturaPersonalEscrituraAdmin()]
        return super().get_permissions()

    def destroy(self, request, *args, **kwargs):
        return Response({'detail': 'Desactiva membresías desde la interfaz de FitLife.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)


class PagoViewSet(viewsets.ReadOnlyModelViewSet):
    """Consulta de pagos vía REST."""
    queryset = Pago.objects.all()
    serializer_class = PagoSerializer
    permission_classes = [PersonalFitLife]


class ClaseViewSet(viewsets.ReadOnlyModelViewSet):
    """Consulta de clases; cambios disponibles desde FitLife."""
    queryset = Clase.objects.all()
    serializer_class = ClaseSerializer
    permission_classes = [LecturaPersonalEscrituraAdmin]

class ReservaViewSet(viewsets.ReadOnlyModelViewSet):
    """Consulta de reservas; los cambios se gestionan mediante los servicios web."""
    queryset = Reserva.objects.all()
    serializer_class = ReservaSerializer
    permission_classes = [PersonalFitLife]


class RegistroAccesoViewSet(viewsets.ReadOnlyModelViewSet):
    """Consulta de Registros de Acceso vía REST."""
    queryset = RegistroAcceso.objects.all()
    serializer_class = RegistroAccesoSerializer
    permission_classes = [PersonalFitLife]


# ------------------------------------------------------------------------------
# Vista de Error 404 Personalizada
# ------------------------------------------------------------------------------
def custom_404_view(request, exception=None):
    """
    Página 404 personalizada con la estética FitLife.
    Explica el error y brinda un enlace de retorno seguro.
    """
    if settings.APPEND_SLASH and request.method in ('GET', 'HEAD') and not request.path_info.endswith('/'):
        from django.urls import resolve
        from django.http import HttpResponsePermanentRedirect
        match = resolve(request.path_info + '/')
        if match.func is not custom_404_view:
            query = request.META.get('QUERY_STRING', '')
            return HttpResponsePermanentRedirect(request.path + '/' + ('?' + query if query else ''))
    return render(request, '404.html', status=404)
