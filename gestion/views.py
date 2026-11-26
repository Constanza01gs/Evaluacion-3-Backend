from django.urls import reverse
"""
Vistas web y endpoints API para FitLife.
Incluye:
- Vistas HTML responsivas con autenticación y redirección por roles (Administrador y Recepción).
- Endpoints REST para AJAX / frontend asíncrono (validación QR, cálculo de aforo, reservas).
- Vista de error 404 personalizada.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, get_user_model, password_validation, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.views.decorators.cache import never_cache
from datetime import timedelta
import secrets
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
    Clase, Reserva, RegistroAcceso, Sala, Actividad, HistorialAsistencia, SolicitudCorreccionAsistencia, NotificacionProfesor, ActivacionProfesor, AvisoIndisponibilidad, NotificacionSocio, NotificacionRecepcion, CambioCodigoAcceso, ActivacionSocio, RecuperacionAsistidaSocio
)
from .serializers import (
    SocioSerializer, PlanSerializer, MembresiaSerializer,
    PagoSerializer, ProfesorSerializer, ClaseSerializer,
    ReservaSerializer, RegistroAccesoSerializer, ValidarAccesoQRSerializer
)
from .services import (
    validar_y_registrar_acceso,
    cerrar_listas_iniciadas,
    confirmar_asistencia_recepcion,
    inicio_clase_local,
    gestionar_reserva_clase,
    cancelar_reserva_clase,
    calcular_aforo_actual
)
from .forms import SocioForm, PlanForm, MembresiaForm, PagoForm, ProfesorForm, ClaseForm, SalaForm, ActividadForm, AnularPagoForm, CrearCuentaProfesorForm, CrearCuentaRecepcionForm, ActivarCuentaProfesorForm, ContactoProfesorForm, RegistroSocioForm, normalizar_rut, ContactoSocioForm, SeguridadSocioForm, ActivarSocioForm, CorreoRecuperacionForm, RespuestaRecuperacionForm, NuevaClaveSocioForm, ContratarMembresiaSocioForm
from .models import ActivacionRecepcion, PerfilPersonal
from .forms import ActivarCuentaPersonalForm, validar_rut_personal


def validar_rut_personal_seguro(valor):
    try:
        return validar_rut_personal(valor)
    except ValidationError:
        return None


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


def es_profesor(user):
    return bool(user.is_authenticated and user.groups.filter(name='Profesor').exists()
                and Profesor.objects.filter(usuario=user).exists())


def es_socio(user):
    return bool(user.is_authenticated and user.groups.filter(name='Socio').exists()
                and Socio.objects.filter(usuario=user).exists())


# ------------------------------------------------------------------------------
# Vistas de Autenticación y Redirección
# ------------------------------------------------------------------------------
def pagina_publica_view(request):
    from .horarios_fijos import asegurar_horarios
    asegurar_horarios()
    hoy = timezone.localdate()
    actividades = list(Actividad.objects.all().order_by('nombre'))
    sesiones = Clase.objects.filter(activo=True, actividad__activo=True, fecha__gte=hoy, fecha__lt=hoy + timedelta(days=7)).select_related('actividad').order_by('fecha', 'hora_inicio')
    dias = ('Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo')
    por_actividad = {}
    for clase in sesiones:
        if inicio_clase_local(clase) <= timezone.now():
            continue
        horario = (dias[clase.fecha.weekday()], clase.hora_inicio, clase.hora_fin)
        horarios = por_actividad.setdefault(clase.actividad_id, [])
        if horario not in horarios:
            horarios.append(horario)
    oferta = [{'actividad': actividad, 'horarios': por_actividad.get(actividad.pk, [])} for actividad in actividades if por_actividad.get(actividad.pk) or actividad.visible_sin_horario]
    return render(request, 'publico/inicio.html', {'planes': Plan.objects.all(), 'oferta': oferta})


@never_cache
def registrar_socio_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard_redirect')
    form = RegistroSocioForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        from django.db import connection, IntegrityError
        try:
            with transaction.atomic():
                # Serializa registros públicos simultáneos también si el RUT aún no existe.
                if connection.vendor == 'postgresql':
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [607315771])
                data = form.cleaned_data
                rut = normalizar_rut(data['rut'])
                if any(normalizar_rut(valor) == rut for valor in Socio.all_objects.values_list('rut', flat=True).iterator()):
                    form.add_error('rut', 'Ya existe una ficha con este RUT. Si fue creada en recepción, solicita la activación de tu cuenta; si ya tienes cuenta, inicia sesión.')
                elif Socio.all_objects.filter(email__iexact=data['email']).exists() or get_user_model().objects.filter(username__iexact=data['email']).exists():
                    form.add_error('email', 'Ya existe una cuenta o ficha con este correo. Contacta a recepción si necesitas ayuda.')
                else:
                    usuario = get_user_model().objects.create_user(username=data['email'], email=data['email'], password=data['clave'], first_name=data['nombres'], last_name=data['apellidos'])
                    grupo, _ = Group.objects.get_or_create(name='Socio')
                    usuario.groups.add(grupo)
                    Socio.objects.create(usuario=usuario, rut=data['rut'], nombres=data['nombres'], apellidos=data['apellidos'], email=data['email'], telefono=data['telefono'], fecha_nacimiento=data['fecha_nacimiento'], pregunta_seguridad=data['pregunta'], respuesta_seguridad_hash=make_password(data['respuesta'].strip().casefold()))
                    login(request, usuario)
                    return redirect('socio_portal')
        except IntegrityError:
            form.add_error(None, 'No se pudo completar el registro porque algún dato ya está registrado. Contacta a recepción.')
    return render(request, 'socio/registro.html', {'form': form})


def _perfil_recuperacion_habilitado(perfil):
    if not perfil or not perfil.usuario.is_active:
        return False
    if isinstance(perfil, Socio):
        return perfil.activo and es_socio(perfil.usuario)
    usuario = perfil.usuario
    return (usuario.groups.filter(name='Recepcion').exists() or (usuario.groups.filter(name='Profesor').exists() and Profesor.objects.filter(usuario=usuario).exists())) and not es_administrador(usuario)


def _huella_recuperacion(socio):
    from django.utils.crypto import salted_hmac
    datos = f'{socio.usuario.password}:{socio.pregunta_seguridad}:{socio.respuesta_seguridad_hash}'
    return salted_hmac('fitlife.recuperacion.socio', datos).hexdigest()


@never_cache
def recuperar_socio_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard_redirect')
    form = CorreoRecuperacionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        request.session.pop('recuperacion_socio', None)
        correo = form.cleaned_data['email'].strip()
        socios = list(Socio.objects.filter(email__iexact=correo, usuario__isnull=False).select_related('usuario')[:2])
        personal = list(PerfilPersonal.objects.filter(usuario__email__iexact=correo).select_related('usuario')[:2])
        candidatos = [perfil for perfil in socios + personal if _perfil_recuperacion_habilitado(perfil) and perfil.pregunta_seguridad and perfil.respuesta_seguridad_hash]
        socio = candidatos[0] if len(candidatos) == 1 else None
        if socio:
            request.session['recuperacion_socio'] = {'id': socio.pk, 'tipo': 'personal' if isinstance(socio, PerfilPersonal) else 'socio', 'iniciada': timezone.now().timestamp()}
            return redirect('recuperar_socio_pregunta')
        form.add_error(None, 'No se pudo iniciar la recuperación. Revisa tus datos o contacta a recepción.')
    return render(request, 'socio/recuperar_correo.html', {'form': form})


@never_cache
@transaction.atomic
def recuperar_socio_pregunta_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard_redirect')
    estado = request.session.get('recuperacion_socio', {})
    ahora = timezone.now()
    if not estado.get('id') or ahora.timestamp() - estado.get('iniciada', 0) > 900:
        request.session.pop('recuperacion_socio', None)
        return redirect('recuperar_socio')
    socio = (PerfilPersonal.objects.select_for_update().select_related('usuario').filter(pk=estado['id']).first() if estado.get('tipo') == 'personal' else Socio.objects.select_for_update().filter(pk=estado['id'], usuario__isnull=False).first())
    if not _perfil_recuperacion_habilitado(socio) or not socio.pregunta_seguridad or not socio.respuesta_seguridad_hash:
        request.session.pop('recuperacion_socio', None)
        return redirect('recuperar_socio')
    form = RespuestaRecuperacionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        if socio.recuperacion_bloqueada_hasta and socio.recuperacion_bloqueada_hasta > ahora:
            form.add_error(None, 'Demasiados intentos. Espera 15 minutos antes de volver a intentarlo.')
        elif not check_password(form.cleaned_data['respuesta'].strip().casefold(), socio.respuesta_seguridad_hash):
            if socio.recuperacion_bloqueada_hasta:
                socio.recuperacion_intentos = 0
                socio.recuperacion_bloqueada_hasta = None
            socio.recuperacion_intentos += 1
            if socio.recuperacion_intentos >= 3:
                socio.recuperacion_bloqueada_hasta = ahora + timedelta(minutes=15)
            socio.save(update_fields=['recuperacion_intentos', 'recuperacion_bloqueada_hasta'])
            form.add_error(None, 'No se pudo validar la respuesta. Inténtalo nuevamente o contacta a recepción.')
        else:
            socio.recuperacion_intentos = 0
            socio.recuperacion_bloqueada_hasta = None
            socio.save(update_fields=['recuperacion_intentos', 'recuperacion_bloqueada_hasta'])
            request.session['recuperacion_socio'] = {'id': socio.pk, 'tipo': estado.get('tipo', 'socio'), 'iniciada': estado['iniciada'], 'verificada': ahora.timestamp(), 'huella': _huella_recuperacion(socio)}
            return redirect('recuperar_socio_clave')
    return render(request, 'socio/recuperar_pregunta.html', {'form': form, 'pregunta': socio.get_pregunta_seguridad_display()})


@never_cache
@transaction.atomic
def recuperar_socio_clave_view(request):
    from django.utils.crypto import constant_time_compare
    if request.user.is_authenticated:
        return redirect('dashboard_redirect')
    estado = request.session.get('recuperacion_socio', {})
    verificada = estado.get('verificada')
    if not estado.get('id') or not verificada or timezone.now().timestamp() - verificada > 600:
        request.session.pop('recuperacion_socio', None)
        return redirect('recuperar_socio')
    socio = (PerfilPersonal.objects.select_for_update().select_related('usuario').filter(pk=estado['id']).first() if estado.get('tipo') == 'personal' else Socio.objects.select_for_update().filter(pk=estado['id'], usuario__isnull=False).first())
    if not _perfil_recuperacion_habilitado(socio) or not constant_time_compare(estado.get('huella', ''), _huella_recuperacion(socio)):
        request.session.pop('recuperacion_socio', None)
        return redirect('recuperar_socio')
    form = NuevaClaveSocioForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            password_validation.validate_password(form.cleaned_data['clave'], socio.usuario)
        except ValidationError as error:
            form.add_error('clave', error)
        else:
            usuario = socio.usuario
            usuario.set_password(form.cleaned_data['clave'])
            usuario.save(update_fields=['password'])
            request.session.pop('recuperacion_socio', None)
            messages.success(request, 'Tu contraseña se actualizó. Ya puedes iniciar sesión.')
            return redirect('login')
    return render(request, 'socio/recuperar_clave.html', {'form': form})


@never_cache
@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
@transaction.atomic
def generar_recuperacion_socio_view(request, socio_id):
    socio = get_object_or_404(Socio.objects.select_for_update(), pk=socio_id)
    usuario = socio.usuario if socio.usuario_id else None
    if not usuario or not usuario.is_active or not es_socio(usuario) or es_recepcionista_o_admin(usuario) or es_profesor(usuario):
        messages.warning(request, 'Esta ficha no tiene una cuenta de socio habilitada para recuperar.')
        return redirect('socios_list')
    if request.method == 'POST':
        if request.POST.get('identidad_verificada') != 'si':
            messages.warning(request, 'Confirma que verificaste presencialmente la identidad del socio.')
        else:
            codigo = secrets.token_urlsafe(16)
            RecuperacionAsistidaSocio.objects.update_or_create(socio=socio, defaults={'codigo_hash': make_password(codigo), 'vence_en': timezone.now() + timedelta(minutes=30), 'usado_en': None, 'intentos_fallidos': 0, 'bloqueado_hasta': None, 'generado_por': request.user, 'generado_en': timezone.now()})
            return render(request, 'socios/codigo_recuperacion.html', {'socio': socio, 'codigo': codigo})
    return render(request, 'socios/generar_recuperacion.html', {'socio': socio})


@never_cache
@transaction.atomic
def recuperar_socio_codigo_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard_redirect')
    form = ActivarSocioForm(request.POST or None)
    form.fields['codigo'].label = 'Código de recuperación'
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        rut = normalizar_rut(data['rut'])
        ids = [pk for pk, valor in Socio.all_objects.values_list('pk', 'rut').iterator() if normalizar_rut(valor) == rut]
        socio = Socio.objects.select_for_update().filter(pk=ids[0], usuario__isnull=False).first() if len(ids) == 1 else None
        recuperacion = RecuperacionAsistidaSocio.objects.select_for_update().filter(socio=socio).first() if socio else None
        ahora = timezone.now()
        usuario = socio.usuario if socio else None
        if not socio or not usuario or not usuario.is_active or not es_socio(usuario) or es_recepcionista_o_admin(usuario) or es_profesor(usuario) or not recuperacion or recuperacion.usado_en or recuperacion.vence_en <= ahora:
            form.add_error(None, 'No se pudo recuperar la cuenta. Revisa los datos o contacta a recepción.')
        elif recuperacion.bloqueado_hasta and recuperacion.bloqueado_hasta > ahora:
            form.add_error(None, 'Demasiados intentos. Espera 15 minutos antes de volver a intentarlo.')
        elif not check_password(data['codigo'], recuperacion.codigo_hash):
            if recuperacion.bloqueado_hasta:
                recuperacion.intentos_fallidos = 0
                recuperacion.bloqueado_hasta = None
            recuperacion.intentos_fallidos += 1
            if recuperacion.intentos_fallidos >= 3:
                recuperacion.bloqueado_hasta = ahora + timedelta(minutes=15)
            recuperacion.save(update_fields=['intentos_fallidos', 'bloqueado_hasta'])
            form.add_error(None, 'No se pudo recuperar la cuenta. Revisa los datos o contacta a recepción.')
        else:
            try:
                password_validation.validate_password(data['clave'], usuario)
            except ValidationError as error:
                form.add_error('clave', error)
            else:
                usuario.set_password(data['clave'])
                usuario.save(update_fields=['password'])
                socio.pregunta_seguridad = data['pregunta']
                socio.respuesta_seguridad_hash = make_password(data['respuesta'].strip().casefold())
                socio.recuperacion_intentos = 0
                socio.recuperacion_bloqueada_hasta = None
                socio.save(update_fields=['pregunta_seguridad', 'respuesta_seguridad_hash', 'recuperacion_intentos', 'recuperacion_bloqueada_hasta'])
                recuperacion.usado_en = ahora
                recuperacion.save(update_fields=['usado_en'])
                request.session.pop('recuperacion_socio', None)
                messages.success(request, 'Cuenta recuperada. Ya puedes iniciar sesión con tu nueva contraseña.')
                return redirect('login')
    return render(request, 'socio/recuperar_codigo.html', {'form': form})


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
        if user is None and '@' in usuario:
            candidatos = list(get_user_model().objects.filter(email__iexact=usuario).order_by('pk')[:2])
            if len(candidatos) == 1:
                user = authenticate(request, username=candidatos[0].get_username(), password=clave)
        if user is not None:
            if not user.is_active:
                messages.error(request, "Tu cuenta de usuario se encuentra deshabilitada.")
                return render(request, 'login.html')

            # El perfil desactivado conserva su historial, pero no debe iniciar sesión.
            if (not es_recepcionista_o_admin(user)
                    and Profesor.all_objects.filter(usuario=user, activo=False).exists()):
                messages.error(request, 'Tu acceso de profesor está desactivado. Contacta a administración.')
                return render(request, 'login.html')

            if (not es_recepcionista_o_admin(user) and not es_profesor(user)
                    and Socio.all_objects.filter(usuario=user, activo=False).exists()):
                messages.error(request, 'Tu acceso de socio está desactivado. Contacta a recepción.')
                return render(request, 'login.html')

            login(request, user)
            if not recordarme:
                # La sesión expira al cerrar el navegador
                request.session.set_expiry(0)

            if not es_socio(user) or es_recepcionista_o_admin(user) or es_profesor(user):
                messages.success(request, f"¡Bienvenido/a, {user.first_name or user.username}!")
            return redirect('dashboard_redirect')
        else:
            messages.error(request, "Correo o usuario y contraseña incorrectos. Por favor, reintenta.")

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
    elif es_profesor(request.user):
        return redirect('profesor_clases')
    elif es_socio(request.user):
        return redirect('socio_portal')
    return HttpResponseForbidden('Tu cuenta no tiene un rol de acceso configurado.')


@login_required
def socio_portal_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    from .services import procesar_reactivaciones_vencidas
    procesar_reactivaciones_vencidas(socio.pk)
    avisos_nuevos = socio.notificaciones.filter(leida=False, oculta=False).exists()
    from calendar import Calendar
    from datetime import date
    from django.db.models.functions import TruncDate
    hoy = timezone.localdate()
    futura = Membresia.objects.filter(socio=socio, estado='ACTIVA', fecha_inicio__gt=hoy).order_by('fecha_inicio').first()
    membresia = socio.membresia_vigente()
    pausada = socio.membresia_en_pausa()
    dias_restantes = porcentaje_restante = None
    if membresia and not pausada:
        duracion = (membresia.fecha_fin - membresia.fecha_inicio).days + 1
        dias_restantes = max(0, (membresia.fecha_fin - hoy).days + 1)
        porcentaje_restante = min(100, max(0, round(dias_restantes * 100 / max(1, duracion))))

    try:
        texto_mes = request.GET.get('mes', hoy.strftime('%Y-%m'))
        if len(texto_mes) != 7 or texto_mes[4] != '-':
            raise ValueError
        seleccionado = date(int(texto_mes[:4]), int(texto_mes[5:]), 1)
        if seleccionado > hoy.replace(day=1) or seleccionado.year < 1900:
            raise ValueError
    except (ValueError, TypeError):
        seleccionado = hoy.replace(day=1)
    meses = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre')
    def desplazar_mes(fecha, delta):
        numero = fecha.year * 12 + fecha.month - 1 + delta
        return date(numero // 12, numero % 12 + 1, 1)
    inicio_grafico = desplazar_mes(seleccionado, -5)
    siguiente = desplazar_mes(seleccionado, 1)
    dias_ingreso = set(RegistroAcceso.objects.filter(socio=socio, tipo='ENTRADA', resultado='PERMITIDO', fecha_hora__date__gte=inicio_grafico, fecha_hora__date__lt=siguiente).annotate(dia_local=TruncDate('fecha_hora', tzinfo=timezone.get_current_timezone())).values_list('dia_local', flat=True).distinct())
    semanas = [[{'dia': dia, 'asistio': bool(dia and date(seleccionado.year, seleccionado.month, dia) in dias_ingreso), 'hoy': bool(dia and date(seleccionado.year, seleccionado.month, dia) == hoy)} for dia in semana] for semana in Calendar(firstweekday=0).monthdayscalendar(seleccionado.year, seleccionado.month)]
    grafico = []
    for delta in range(-5, 1):
        mes = desplazar_mes(seleccionado, delta)
        cantidad = sum(d.year == mes.year and d.month == mes.month for d in dias_ingreso)
        grafico.append({'nombre': meses[mes.month - 1][:3].capitalize(), 'anio': mes.year, 'cantidad': cantidad})
    maximo = max([item['cantidad'] for item in grafico] + [1])
    for item in grafico:
        item['alto'] = round(item['cantidad'] * 100 / maximo)
    anterior = desplazar_mes(seleccionado, -1) if seleccionado.year > 1900 or seleccionado.month > 6 else None
    return render(request, 'socio/portal.html', {'socio': socio, 'membresia': membresia, 'membresia_pausada': pausada, 'membresia_futura': futura, 'avisos_nuevos': avisos_nuevos, 'dias_restantes': dias_restantes, 'porcentaje_restante': porcentaje_restante, 'mes_nombre': f'{meses[seleccionado.month - 1].capitalize()} {seleccionado.year}', 'mes_anterior': anterior.strftime('%Y-%m') if anterior else None, 'mes_siguiente': siguiente.strftime('%Y-%m') if siguiente <= hoy.replace(day=1) else None, 'semanas': semanas, 'grafico_visitas': grafico, 'hay_visitas_grafico': bool(dias_ingreso), 'visitas_mes': sum(d.year == seleccionado.year and d.month == seleccionado.month for d in dias_ingreso)})


@login_required
@never_cache
def socio_membresias_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    from .services import procesar_reactivaciones_vencidas
    procesar_reactivaciones_vencidas(socio.pk)
    membresias = Membresia.all_objects.filter(socio=socio).select_related('plan').prefetch_related('pagos', 'solicitudes_congelamiento').order_by('-fecha_inicio', '-pk')
    hoy = timezone.localdate()
    for membresia in membresias:
        membresia.puede_solicitar_pausa = (membresia.activo and membresia.estado == 'ACTIVA' and membresia.fecha_inicio <= hoy <= membresia.fecha_fin and (membresia.dias_congelamiento_disponibles or 0) > 0 and not any(s.estado == 'PENDIENTE' for s in membresia.solicitudes_congelamiento.all()))
        for solicitud in membresia.solicitudes_congelamiento.all():
            solicitud.puede_cancelar_pausa = solicitud.estado == 'APROBADA' and solicitud.fecha_inicio > hoy and membresia.activo and membresia.estado == 'ACTIVA'
            solicitud.puede_solicitar_reactivacion = solicitud.estado == 'APROBADA' and not solicitud.fecha_reactivacion and not solicitud.reactivacion_pendiente and solicitud.fecha_inicio <= hoy <= solicitud.fecha_fin and membresia.activo and membresia.estado == 'ACTIVA'
    actual = socio.membresia_vigente() or socio.membresia_en_pausa()
    actual = next((m for m in membresias if actual and m.pk == actual.pk), None)
    if actual:
        from .services import saldo_disponible
        actual.esta_pausada = actual.estado_actual_display == 'Congelada'
        if actual.esta_pausada:
            actual.puede_solicitar_pausa = False
        actual.dias_restantes = max((actual.fecha_fin - hoy).days + 1, 0)
        duracion = max((actual.fecha_fin - actual.fecha_inicio).days + 1, 1)
        actual.porcentaje_dias = min(100, round(actual.dias_restantes * 100 / duracion))
        actual.saldo_clases = None
        actual.porcentaje_clases = None
        limite = actual.limite_clases_contratado
        if actual.incluye_clases and actual.limite_clases_registrado and limite is not None:
            actual.saldo_clases = saldo_disponible(actual)
            if limite > 0:
                actual.porcentaje_clases = min(100, max(0, round(actual.saldo_clases * 100 / limite)))
        actual.saldo_congelamiento = actual.dias_congelamiento_disponibles
        actual.porcentaje_congelamiento = None
        if actual.dias_congelamiento_contratados:
            actual.porcentaje_congelamiento = min(100, round(actual.saldo_congelamiento * 100 / actual.dias_congelamiento_contratados))
    return render(request, 'socio/membresias.html', {
        'socio': socio, 'membresias': membresias, 'actual': actual,
        'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists(),
    })


@login_required
@never_cache
def socio_solicitar_congelamiento_view(request, membresia_id):
    from .forms import SolicitudCongelamientoForm
    from .services import solicitar_congelamiento_socio
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    membresia = get_object_or_404(Membresia.all_objects.select_related('plan'), pk=membresia_id, socio__usuario=request.user)
    form = SolicitudCongelamientoForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            solicitar_congelamiento_socio(membresia.pk, request.user, form.cleaned_data['fecha_inicio'], form.cleaned_data['fecha_fin'], form.cleaned_data['motivo'])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Tu solicitud fue enviada y está pendiente de revisión. Tu membresía sigue funcionando normalmente.')
            return redirect('socio_solicitudes_congelamiento')
    socio = membresia.socio
    solicitudes = membresia.solicitudes_congelamiento.all()
    return render(request, 'socio/solicitar_congelamiento.html', {'form': form, 'membresia': membresia, 'socio': socio, 'solicitudes': solicitudes, 'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists()})


@login_required
@never_cache
def socio_solicitudes_congelamiento_view(request):
    from .models import SolicitudCongelamiento
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    solicitudes = SolicitudCongelamiento.objects.filter(membresia__socio=socio).select_related('membresia__plan').order_by('-creada_en', '-pk')
    hoy = timezone.localdate()
    for solicitud in solicitudes:
        membresia = solicitud.membresia
        solicitud.puede_cancelar_pausa = solicitud.estado == 'APROBADA' and solicitud.fecha_inicio > hoy and membresia.activo and membresia.estado == 'ACTIVA'
        solicitud.puede_solicitar_reactivacion = solicitud.estado == 'APROBADA' and not solicitud.fecha_reactivacion and not solicitud.reactivacion_pendiente and solicitud.fecha_inicio <= hoy <= solicitud.fecha_fin and membresia.activo and membresia.estado == 'ACTIVA'
    return render(request, 'socio/solicitudes_congelamiento.html', {'socio': socio, 'solicitudes': solicitudes, 'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists()})


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='dashboard_redirect')
@never_cache
def personal_solicitar_congelamiento_view(request, membresia_id):
    from .forms import SolicitudCongelamientoPersonalForm
    from .services import solicitar_congelamiento_socio
    membresia = get_object_or_404(Membresia.objects.select_related('plan', 'socio'), pk=membresia_id)
    form = SolicitudCongelamientoPersonalForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            solicitar_congelamiento_socio(membresia.pk, request.user, form.cleaned_data['fecha_inicio'], form.cleaned_data['fecha_fin'], form.cleaned_data['motivo'])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Solicitud registrada para revisión de administración. La membresía continúa vigente.')
            return redirect('gestion_list', modulo='membresias')
    return render(request, 'gestion/solicitar_congelamiento.html', {'form': form, 'membresia': membresia, 'solicitudes': membresia.solicitudes_congelamiento.all()})


@login_required
@never_cache
def socio_contratar_view(request):
    from django.core import signing
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    hoy = timezone.localdate()
    vigente = Membresia.objects.filter(socio=socio, estado__in=['ACTIVA', 'CONGELADA'], fecha_inicio__lte=hoy, fecha_fin__gte=hoy).order_by('-fecha_fin').first()
    inicio_sugerido = vigente.fecha_fin + timedelta(days=1) if vigente and vigente.estado_actual_display != 'Congelada' else hoy
    form = ContratarMembresiaSocioForm(request.POST or None, initial={'fecha_inicio': inicio_sugerido, 'plan': vigente.plan_id if vigente else None})
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        if normalizar_rut(data['rut']) != normalizar_rut(socio.rut):
            form.add_error('rut', 'El RUT no coincide con el de tu cuenta.')
        else:
            try:
                fin = _validar_contratacion_socio(socio, data['plan'], data['fecha_inicio'])
            except ValidationError as error:
                form.add_error(None, error)
            else:
                propuesta = _propuesta_contratacion(socio, data['plan'], data['fecha_inicio'], data['metodo_pago'])
                token = signing.dumps(propuesta, salt='fitlife.contratacion')
                return render(request, 'socio/confirmar_contratacion.html', {'socio': socio, 'plan': data['plan'], 'inicio': data['fecha_inicio'], 'fin': fin, 'metodo': data['metodo_pago'], 'token': token, 'form': form, 'vigente': vigente, 'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists()})
    return render(request, 'socio/contratar.html', {'form': form, 'vigente': vigente, 'inicio_sugerido': inicio_sugerido, 'socio': socio, 'planes': form.fields['plan'].queryset, 'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists()})


def _validar_contratacion_socio(socio, plan, inicio):
    hoy = timezone.localdate()
    if plan.dias_congelamiento is None:
        raise ValidationError('Este plan aún no tiene definidos sus días de congelamiento. Consulta en recepción.')
    if not plan.activo or plan.precio <= 0 or plan.duracion_dias < 1:
        raise ValidationError('Este plan no está disponible para contratar.')
    existentes = Membresia.objects.filter(socio=socio, estado__in=['ACTIVA', 'CONGELADA'])
    if existentes.filter(fecha_inicio__gt=hoy).exists():
        raise ValidationError('Ya tienes una membresía programada. Revisa tus membresías antes de contratar otra.')
    vigentes = list(existentes.filter(fecha_inicio__lte=hoy, fecha_fin__gte=hoy).order_by('-fecha_fin')[:2])
    if len(vigentes) > 1:
        raise ValidationError('Tu cuenta tiene membresías superpuestas. Contacta a recepción para revisarlas.')
    if vigentes and vigentes[0].estado_actual_display == 'Congelada':
        raise ValidationError('Tu membresía está congelada. Contacta a recepción antes de contratar.')
    if vigentes and inicio != vigentes[0].fecha_fin + timedelta(days=1):
        raise ValidationError('La renovación debe comenzar al día siguiente del vencimiento de tu membresía actual.')
    if not vigentes and not (hoy <= inicio <= hoy + timedelta(days=30)):
        raise ValidationError('Elige hoy o una fecha dentro de los próximos 30 días.')
    return inicio + timedelta(days=plan.duracion_dias - 1)


def _propuesta_contratacion(socio, plan, inicio, metodo):
    return {'socio': socio.pk, 'plan': plan.pk, 'nombre': plan.nombre, 'descripcion': plan.descripcion, 'duracion': plan.duracion_dias, 'precio': str(plan.precio), 'permite_clases': plan.permite_clases, 'limite_clases': plan.limite_clases, 'dias_congelamiento': plan.dias_congelamiento, 'inicio': inicio.isoformat(), 'metodo': metodo}


@login_required
@never_cache
@transaction.atomic
def socio_confirmar_contratacion_view(request):
    from datetime import date
    from django.core import signing
    from django.http import HttpResponseNotAllowed
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    socio = get_object_or_404(Socio.objects.select_for_update(), usuario=request.user)
    try:
        propuesta = signing.loads(request.POST.get('propuesta', ''), salt='fitlife.contratacion', max_age=900)
        if not isinstance(propuesta, dict) or propuesta.get('socio') != socio.pk or propuesta.get('metodo') not in ('TARJETA_DEBITO', 'TARJETA_CREDITO'):
            raise ValueError
        inicio = date.fromisoformat(propuesta['inicio'])
        plan = Plan.objects.select_for_update().filter(pk=propuesta['plan']).first()
        if not plan or propuesta != _propuesta_contratacion(socio, plan, inicio, propuesta['metodo']):
            raise ValueError
        fin = _validar_contratacion_socio(socio, plan, inicio)
    except (signing.BadSignature, ValueError, TypeError, KeyError, ValidationError):
        messages.warning(request, 'No se pudo confirmar: revisa nuevamente las fechas y condiciones del plan.')
        return redirect('socio_contratar')
    membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=inicio, fecha_fin=fin, estado='ACTIVA')
    pago = Pago.objects.create(membresia=membresia, monto=membresia.precio_contratado, metodo_pago=propuesta['metodo'], observaciones='Pago de prueba desde el portal del socio.')
    if request.POST.get('flujo') == 'portal':
        return redirect('socio_comprobante', pago_id=pago.pk)
    messages.success(request, 'Tu membresía y pago de prueba se registraron correctamente.')
    return redirect('socio_portal')


@login_required
@never_cache
def socio_comprobante_view(request, pago_id):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    pago = get_object_or_404(Pago.objects.select_related('membresia__plan'), pk=pago_id, membresia__socio=socio)
    return render(request, 'socio/comprobante.html', {'socio': socio, 'pago': pago, 'desde_pagos': request.GET.get('origen') == 'pagos', 'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists()})


@login_required
@never_cache
def socio_avisos_resumen_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    avisos = socio.notificaciones.filter(oculta=False)
    return JsonResponse({'sin_leer': avisos.filter(leida=False).exists(), 'avisos': [{'mensaje': a.mensaje, 'fecha': timezone.localtime(a.creada).strftime('%d/%m/%Y %H:%M'), 'leida': a.leida, 'url': reverse('socio_reservas') if a.reserva_id else (reverse('socio_pagos') if 'PAGO' in a.tipo else reverse('socio_membresias') if ('CONGELAMIENTO' in a.tipo or 'REACTIVACION' in a.tipo or 'RENOVACION' in a.tipo) else None)} for a in avisos[:5]]})


@login_required
@never_cache
def socio_pagos_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    from django.db.models import Exists, OuterRef
    from .models import SolicitudAnulacionPago
    pendientes = SolicitudAnulacionPago.objects.filter(pago_id=OuterRef('pk'), estado='PENDIENTE')
    pagos = Pago.objects.filter(membresia__socio=socio).select_related('membresia__plan').annotate(anulacion_pendiente=Exists(pendientes)).order_by('-fecha_pago', '-pk')
    from django.db.models import Prefetch
    pagos = pagos.prefetch_related(Prefetch('solicitudes_anulacion', queryset=SolicitudAnulacionPago.objects.order_by('-creada', '-pk'), to_attr='solicitudes_ordenadas'))
    for pago in pagos:
        pago.ultima_solicitud = pago.solicitudes_ordenadas[0] if pago.solicitudes_ordenadas else None
    return render(request, 'socio/pagos.html', {
        'socio': socio, 'pagos': pagos,
        'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists(),
    })


@login_required
@never_cache
def socio_codigo_acceso_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    return render(request, 'socio/codigo_acceso.html', {'socio': socio, 'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists()})


@login_required
@never_cache
def socio_codigo_qr_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    from io import BytesIO
    import qrcode
    import qrcode.image.svg
    from django.http import HttpResponse
    socio = get_object_or_404(Socio, usuario=request.user)
    imagen = qrcode.make(socio.codigo_qr, image_factory=qrcode.image.svg.SvgPathImage)
    archivo = BytesIO()
    imagen.save(archivo)
    return HttpResponse(archivo.getvalue(), content_type='image/svg+xml')


def imagen_tipo_clase(clase):
    nombre = (clase.nombre + ' ' + (clase.actividad.nombre if clase.actividad_id else '')).lower()
    if 'yoga' in nombre or 'pilates' in nombre:
        return 'yoga'
    if any(palabra in nombre for palabra in ('spinning', 'cycling', 'ciclismo', 'bicicleta')):
        return 'spinning'
    if any(palabra in nombre for palabra in ('hiit', 'interval', 'crossfit')):
        return 'hiit'
    return 'funcional'


@login_required
@never_cache
def socio_clases_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    from .horarios_fijos import asegurar_horarios
    asegurar_horarios()
    ahora = timezone.now()
    clases = Clase.objects.filter(fecha__gte=timezone.localdate(), fecha__lt=timezone.localdate() + timedelta(days=30)).select_related('profesor', 'actividad', 'sala_asignada').order_by('fecha', 'hora_inicio')
    clases = [clase for clase in clases if inicio_clase_local(clase) > ahora]
    actividades = sorted({clase.actividad.nombre if clase.actividad_id else clase.nombre for clase in clases}, key=str.casefold)
    fecha = request.GET.get('fecha', '').strip()
    actividad = request.GET.get('actividad', '').strip()
    from django.utils.dateparse import parse_date
    try:
        fecha_valida = parse_date(fecha) if fecha else None
    except ValueError:
        fecha_valida = None
    if fecha_valida:
        clases = [clase for clase in clases if clase.fecha == fecha_valida]
    else:
        fecha = ''
    if actividad in actividades:
        clases = [clase for clase in clases if (clase.actividad.nombre if clase.actividad_id else clase.nombre) == actividad]
    else:
        actividad = ''
    reservas = dict(Reserva.objects.filter(socio=socio, clase__in=clases, estado__in=['CONFIRMADA', 'LISTA_ESPERA']).values_list('clase_id', 'estado'))
    from .services import saldo_disponible
    membresia_actual = socio.membresia_vigente()
    for clase in clases:
        clase.imagen_tipo = imagen_tipo_clase(clase)
        clase.cupos_mostrar = clase.cupos_disponibles()
        inicio = inicio_clase_local(clase)
        clase.estado_reserva_socio = reservas.get(clase.pk)
        clase.reserva_aun_no_abre = ahora < inicio - timedelta(hours=72)
        clase.apertura_reserva = inicio - timedelta(hours=72)
        clase.reserva_cerrada = ahora >= inicio - timedelta(minutes=15)
        plan_fecha = socio.membresia_para_fecha(clase.fecha)
        clase.aviso_membresia = ''
        if not plan_fecha:
            clase.aviso_membresia = 'No tienes una membresía vigente para la fecha de esta clase.'
        elif not plan_fecha.incluye_clases:
            clase.aviso_membresia = 'Tu plan para esta fecha no incluye clases grupales.'
        elif saldo_disponible(plan_fecha) == 0:
            clase.aviso_membresia = 'No tienes clases disponibles para esta fecha.'
    return render(request, 'socio/clases.html', {
        'socio': socio, 'clases': clases, 'actividades': actividades, 'membresia_actual': membresia_actual,
        'fecha_seleccionada': fecha, 'actividad_seleccionada': actividad,
        'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists(),
    })


@login_required
def socio_reservar_view(request, clase_id):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    socio = get_object_or_404(Socio, usuario=request.user)
    exito, mensaje, reserva = gestionar_reserva_clase(clase_id, socio.pk)
    if exito:
        messages.success(request, 'Tu reserva quedó confirmada.' if reserva.estado == 'CONFIRMADA' else 'Quedaste en la lista de espera.')
    else:
        messages.warning(request, mensaje if not mensaje.startswith('Error al procesar') else 'No se pudo completar la reserva. Inténtalo de nuevo.')
    return redirect('socio_reservas' if exito else 'socio_clases')


def efecto_cancelacion_socio(reserva, ahora):
    if reserva.estado == 'LISTA_ESPERA':
        return 'espera'
    inicio = inicio_clase_local(reserva.clase)
    if ahora <= inicio - timedelta(hours=2) or (reserva.promocion_tardia and ahora < inicio):
        return 'devuelve'
    consume = reserva.consume_clase or (not reserva.membresia_id and bool(reserva.socio.membresia_para_fecha(reserva.clase.fecha)))
    return 'descuenta' if consume else 'sin_descuento'


@login_required
@never_cache
def socio_reservas_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    reservas = Reserva.objects.filter(socio=socio).select_related('clase__profesor', 'clase__sala_asignada', 'clase__actividad').order_by('-clase__fecha', '-clase__hora_inicio', '-id')
    ahora = timezone.now()
    proximas, historial = [], []
    for reserva in reservas:
        inicio = inicio_clase_local(reserva.clase)
        puede_cancelar = reserva.estado in ('CONFIRMADA', 'LISTA_ESPERA') and reserva.clase.activo and inicio > ahora
        fila = {'reserva': reserva, 'puede_cancelar': puede_cancelar, 'efecto_cancelacion': efecto_cancelacion_socio(reserva, ahora) if puede_cancelar else None,
                'imagen_tipo': imagen_tipo_clase(reserva.clase),
                'finalizada': inicio + timedelta(minutes=reserva.clase.duracion_minutos) <= ahora and reserva.estado == 'CONFIRMADA',
                'en_curso': inicio <= ahora < inicio + timedelta(minutes=reserva.clase.duracion_minutos) and reserva.estado == 'CONFIRMADA'}
        (proximas if puede_cancelar else historial).append(fila)
    proximas.reverse()
    return render(request, 'socio/reservas.html', {
        'socio': socio, 'proximas': proximas, 'historial': historial,
        'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists(),
    })


@login_required
@never_cache
@transaction.atomic
def socio_cancelar_reserva_view(request, reserva_id):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    referencia = get_object_or_404(Reserva.objects.only('clase_id'), pk=reserva_id, socio=socio)
    clase = get_object_or_404(Clase.all_objects.select_for_update(), pk=referencia.clase_id)
    reserva = get_object_or_404(Reserva.objects.select_for_update(), pk=reserva_id, socio=socio, clase=clase)
    if not clase.activo or reserva.estado not in ('CONFIRMADA', 'LISTA_ESPERA') or inicio_clase_local(clase) <= timezone.now():
        messages.warning(request, 'Esta solicitud ya no se puede cancelar desde tu cuenta.')
        return redirect('socio_reservas')
    if request.method == 'POST':
        efecto_actual = efecto_cancelacion_socio(reserva, timezone.now())
        if request.POST.get('efecto_esperado') and request.POST.get('efecto_esperado') != efecto_actual:
            messages.warning(request, 'El efecto de la cancelación cambió por el plazo. Revisa nuevamente antes de confirmar.')
            return redirect('socio_reservas')
        exito, _ = cancelar_reserva_clase(reserva.pk)
        (messages.success if exito else messages.warning)(request, 'Tu solicitud fue cancelada.' if exito else 'No se pudo cancelar la solicitud. Inténtalo nuevamente.')
        return redirect('socio_reservas')
    return render(request, 'socio/cancelar_reserva.html', {'reserva': reserva, 'efecto_cancelacion': efecto_cancelacion_socio(reserva, timezone.now())})


@login_required
@never_cache
def socio_notificaciones_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    from .services import procesar_reactivaciones_vencidas
    procesar_reactivaciones_vencidas(socio.pk)
    avisos = list(socio.notificaciones.filter(oculta=False))
    socio.notificaciones.filter(pk__in=[aviso.pk for aviso in avisos], leida=False).update(leida=True)
    return render(request, 'socio/notificaciones.html', {'avisos': avisos, 'socio': socio})


@login_required
@never_cache
def socio_limpiar_notificaciones_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    if request.method == 'POST':
        socio.notificaciones.filter(oculta=False).update(oculta=True, leida=True)
        messages.success(request, 'Se limpió tu historial de notificaciones.')
        return redirect('socio_notificaciones')
    return render(request, 'socio/confirmar_limpiar_avisos.html', {'socio': socio, 'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists()})


@login_required
@never_cache
def socio_perfil_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    email_anterior = socio.email
    form = ContactoSocioForm(request.POST or None, instance=socio)
    if request.method == 'POST' and form.is_valid():
        from django.db import IntegrityError
        try:
            with transaction.atomic():
                socio = form.save()
                usuario = request.user
                usuario.email = socio.email
                campos = ['email']
                if usuario.username.casefold() == email_anterior.casefold():
                    usuario.username = socio.email
                    campos.append('username')
                usuario.save(update_fields=campos)
        except IntegrityError:
            form.add_error('email', 'Este correo ya está asociado a otra cuenta o ficha.')
        else:
            messages.success(request, 'Tus datos de contacto se actualizaron correctamente.')
            return redirect('socio_perfil')
    return render(request, 'socio/perfil.html', {'socio': socio, 'form': form, 'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists()})


@login_required
@never_cache
def socio_seguridad_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    socio = get_object_or_404(Socio, usuario=request.user)
    form = SeguridadSocioForm(request.user, request.POST or None, initial={'pregunta': socio.pregunta_seguridad})
    if request.method == 'POST' and form.is_valid():
        socio.pregunta_seguridad = form.cleaned_data['pregunta']
        socio.respuesta_seguridad_hash = make_password(form.cleaned_data['respuesta'].strip().casefold())
        socio.save(update_fields=['pregunta_seguridad', 'respuesta_seguridad_hash'])
        messages.success(request, 'Tu pregunta de seguridad se actualizó correctamente.')
        return redirect('socio_perfil')
    return render(request, 'socio/seguridad.html', {'form': form, 'socio': socio, 'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists()})


@login_required
@never_cache
def socio_cambiar_clave_view(request):
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        usuario = form.save()
        update_session_auth_hash(request, usuario)
        messages.success(request, 'Tu contraseña se actualizó correctamente.')
        return redirect('socio_perfil')
    socio = get_object_or_404(Socio, usuario=request.user)
    return render(request, 'socio/cambiar_clave.html', {'form': form, 'socio': socio, 'cambiar_clave': True, 'avisos_nuevos': socio.notificaciones.filter(leida=False, oculta=False).exists()})


@never_cache
@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
@transaction.atomic
def generar_activacion_socio_view(request, socio_id):
    socio = get_object_or_404(Socio.objects.select_for_update(), pk=socio_id)
    if socio.usuario_id:
        messages.warning(request, 'Este socio ya tiene una cuenta vinculada.')
        return redirect('socios_list')
    if request.method == 'POST':
        if request.POST.get('identidad_verificada') != 'si':
            messages.warning(request, 'Confirma que verificaste la identidad del socio.')
        elif len(socio.email) > 150 or get_user_model().objects.filter(username__iexact=socio.email.strip()).exists():
            messages.warning(request, 'No se puede generar el acceso: revisa el correo asociado a esta ficha.')
        else:
            codigo = secrets.token_urlsafe(16)
            ActivacionSocio.objects.update_or_create(socio=socio, defaults={'codigo_hash': make_password(codigo), 'vence_en': timezone.now() + timedelta(hours=24), 'usado_en': None, 'intentos_fallidos': 0, 'bloqueado_hasta': None, 'generado_por': request.user, 'generado_en': timezone.now()})
            return render(request, 'socios/codigo_activacion.html', {'socio': socio, 'codigo': codigo})
    return render(request, 'socios/generar_activacion.html', {'socio': socio})


@never_cache
@transaction.atomic
def activar_cuenta_socio_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard_redirect')
    form = ActivarSocioForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        rut = normalizar_rut(data['rut'])
        ids = [pk for pk, valor in Socio.all_objects.values_list('pk', 'rut').iterator() if normalizar_rut(valor) == rut]
        socio = Socio.all_objects.select_for_update().filter(pk=ids[0]).first() if len(ids) == 1 else None
        activacion = ActivacionSocio.objects.select_for_update().filter(socio=socio).first() if socio else None
        ahora = timezone.now()
        if not socio or not socio.activo or socio.usuario_id or not activacion or activacion.usado_en or activacion.vence_en <= ahora:
            form.add_error(None, 'No se pudo activar la cuenta. Revisa los datos o contacta a recepción.')
        elif activacion.bloqueado_hasta and activacion.bloqueado_hasta > ahora:
            form.add_error(None, 'Demasiados intentos. Espera 15 minutos antes de volver a intentarlo.')
        elif not check_password(data['codigo'], activacion.codigo_hash):
            if activacion.bloqueado_hasta:
                activacion.intentos_fallidos = 0
                activacion.bloqueado_hasta = None
            activacion.intentos_fallidos += 1
            if activacion.intentos_fallidos >= 3:
                activacion.bloqueado_hasta = ahora + timedelta(minutes=15)
            activacion.save(update_fields=['intentos_fallidos', 'bloqueado_hasta'])
            form.add_error(None, 'No se pudo activar la cuenta. Revisa los datos o contacta a recepción.')
        else:
            correo = socio.email.strip().lower()
            usuario = get_user_model()(username=correo, email=correo, first_name=socio.nombres, last_name=socio.apellidos)
            try:
                password_validation.validate_password(data['clave'], usuario)
            except ValidationError as error:
                form.add_error('clave', error)
            else:
                if len(correo) > 150 or get_user_model().objects.filter(username__iexact=correo).exists():
                    form.add_error(None, 'No se pudo activar la cuenta. Contacta a recepción para revisar tus datos.')
                else:
                    from django.db import IntegrityError
                    try:
                        with transaction.atomic():
                            usuario.set_password(data['clave'])
                            usuario.save()
                            grupo, _ = Group.objects.get_or_create(name='Socio')
                            usuario.groups.add(grupo)
                            socio.usuario = usuario
                            socio.pregunta_seguridad = data['pregunta']
                            socio.respuesta_seguridad_hash = make_password(data['respuesta'].strip().casefold())
                            socio.save(update_fields=['usuario', 'pregunta_seguridad', 'respuesta_seguridad_hash'])
                            activacion.usado_en = ahora
                            activacion.save(update_fields=['usado_en'])
                    except IntegrityError:
                        form.add_error(None, 'No se pudo activar la cuenta. Contacta a recepción para revisar tus datos.')
                    else:
                        messages.success(request, 'Cuenta activada. Ya puedes iniciar sesión con tu correo y contraseña.')
                        return redirect('login')
    return render(request, 'socio/activar_cuenta.html', {'form': form})


@never_cache
@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
def recepcionistas_view(request):
    ver_inactivos = request.GET.get('estado') == 'inactivos'
    usuarios = get_user_model().objects.filter(groups__name='Recepcion', is_active=not ver_inactivos).select_related('activacion_recepcion', 'perfil_personal').order_by('first_name', 'last_name', 'username').distinct()
    return render(request, 'recepcion/personal.html', {'usuarios': usuarios, 'editables': set(_cuentas_recepcion_editables().values_list('pk', flat=True)), 'ver_inactivos': ver_inactivos})


def _cuentas_recepcion_editables():
    return get_user_model().objects.filter(groups__name='Recepcion', is_staff=False, is_superuser=False).exclude(groups__name__in=['Administrador', 'Profesor']).distinct()


@never_cache
@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def editar_recepcionista_view(request, usuario_id):
    from .forms import EditarRecepcionistaForm
    usuario = get_object_or_404(_cuentas_recepcion_editables(), pk=usuario_id)
    form = EditarRecepcionistaForm(request.POST or None, usuario=usuario)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Datos de recepción actualizados correctamente.')
        return redirect('recepcionistas')
    return render(request, 'recepcion/editar_personal.html', {'persona': usuario, 'form': form})


@never_cache
@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def estado_recepcionista_view(request, usuario_id, accion):
    from django.http import Http404
    if accion not in ('desactivar', 'reactivar'):
        raise Http404
    usuario = get_object_or_404(_cuentas_recepcion_editables(), pk=usuario_id)
    if request.method == 'POST':
        usuario.is_active = accion == 'reactivar'
        usuario.save(update_fields=['is_active'])
        messages.success(request, 'La cuenta fue reactivada.' if usuario.is_active else 'La cuenta fue desactivada.')
        return redirect('recepcionistas')
    return render(request, 'recepcion/estado_personal.html', {'persona': usuario, 'accion': accion})


@never_cache
@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def crear_recepcionista_view(request):
    form = CrearCuentaRecepcionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        datos = form.cleaned_data
        usuario = get_user_model().objects.create_user(username=datos['usuario'], password=None, first_name=datos['nombres'], last_name=datos['apellidos'], email=datos['correo'])
        PerfilPersonal.objects.create(usuario=usuario, rut=datos['rut'], telefono=datos['telefono'])
        grupo, _ = Group.objects.get_or_create(name='Recepcion')
        usuario.groups.add(grupo)
        codigo = secrets.token_urlsafe(16)
        ActivacionRecepcion.objects.create(usuario=usuario, codigo_hash=make_password(codigo), vence_en=timezone.now() + timedelta(hours=24))
        return render(request, 'recepcion/codigo.html', {'usuario_creado': usuario, 'codigo': codigo})
    return render(request, 'recepcion/crear_personal.html', {'form': form})


@never_cache
@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def regenerar_codigo_recepcion_view(request, usuario_id):
    usuario = get_object_or_404(get_user_model().objects.select_for_update().filter(pk__in=_cuentas_recepcion_editables().values('pk')), pk=usuario_id)
    activacion = ActivacionRecepcion.objects.select_for_update().filter(usuario=usuario).first()
    if not hasattr(usuario, 'perfil_personal'):
        messages.warning(request, 'Completa los datos personales antes de generar un código de activación.')
        return redirect('recepcionista_ficha', usuario_id=usuario.pk)
    if (activacion and activacion.usado_en) or usuario.has_usable_password() or not usuario.is_active:
        messages.warning(request, 'No hay una activación pendiente para esta cuenta.')
        return redirect('recepcionistas')
    if request.method == 'POST':
        codigo = secrets.token_urlsafe(16)
        activacion, _ = ActivacionRecepcion.objects.update_or_create(usuario=usuario, defaults={
            'codigo_hash': make_password(codigo), 'vence_en': timezone.now() + timedelta(hours=24),
            'intentos_fallidos': 0, 'bloqueado_hasta': None,
        })
        return render(request, 'recepcion/codigo.html', {'usuario_creado': usuario, 'codigo': codigo})
    return render(request, 'recepcion/regenerar.html', {'persona': usuario})


@never_cache
@transaction.atomic
def activar_cuenta_recepcion_view(request):
    form = ActivarCuentaPersonalForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        rut = validar_rut_personal_seguro(form.cleaned_data['rut'])
        activacion = ActivacionRecepcion.objects.select_for_update().select_related('usuario', 'usuario__perfil_personal').filter(usuario__perfil_personal__rut=rut, usuario__groups__name='Recepcion').first() if rut else None
        ahora = timezone.now()
        if not activacion or not activacion.usuario.is_active or activacion.usado_en or activacion.usuario.has_usable_password() or activacion.vence_en <= ahora:
            form.add_error(None, 'No se pudo activar la cuenta. Revisa los datos o contacta a administración.')
        elif activacion.bloqueado_hasta and activacion.bloqueado_hasta > ahora:
            form.add_error(None, 'Demasiados intentos. Espera 15 minutos antes de volver a intentarlo.')
        else:
            if activacion.bloqueado_hasta:
                activacion.intentos_fallidos = 0
                activacion.bloqueado_hasta = None
            if not check_password(form.cleaned_data['codigo'], activacion.codigo_hash):
                activacion.intentos_fallidos += 1
                if activacion.intentos_fallidos >= 3:
                    activacion.bloqueado_hasta = ahora + timedelta(minutes=15)
                activacion.save(update_fields=['intentos_fallidos', 'bloqueado_hasta'])
                form.add_error(None, 'No se pudo activar la cuenta. Revisa los datos o contacta a administración.')
            else:
                try:
                    password_validation.validate_password(form.cleaned_data['clave'], activacion.usuario)
                except ValidationError as exc:
                    form.add_error('clave', exc)
                if not form.errors:
                    activacion.usuario.set_password(form.cleaned_data['clave'])
                    activacion.usuario.save(update_fields=['password'])
                    perfil = activacion.usuario.perfil_personal
                    perfil.pregunta_seguridad = form.cleaned_data['pregunta']
                    perfil.respuesta_seguridad_hash = make_password(form.cleaned_data['respuesta'].strip().casefold())
                    perfil.save(update_fields=['pregunta_seguridad', 'respuesta_seguridad_hash'])
                    activacion.usado_en = ahora
                    activacion.save(update_fields=['usado_en', 'intentos_fallidos', 'bloqueado_hasta'])
                    messages.success(request, 'Cuenta activada. Ya puedes iniciar sesión.')
                    return redirect('login')
    return render(request, 'recepcion/activar.html', {'form': form})


@never_cache
@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def crear_cuenta_profesor_view(request, profesor_id):
    profesor = get_object_or_404(Profesor.objects.select_for_update(), pk=profesor_id)
    if profesor.usuario_id:
        messages.warning(request, 'Este profesor ya tiene una cuenta vinculada.')
        return redirect('gestion_list', modulo='profesores')
    if not profesor.rut or not profesor.email:
        messages.warning(request, 'Completa el RUT y el correo en la ficha del profesor antes de crear su acceso.')
        return redirect('gestion_list', modulo='profesores')
    if PerfilPersonal.objects.filter(rut=profesor.rut).exists() or get_user_model().objects.filter(email__iexact=profesor.email).exists():
        messages.warning(request, 'El RUT o correo ya pertenece a una cuenta de personal. Revisa la ficha.')
        return redirect('gestion_list', modulo='profesores')
    form = CrearCuentaProfesorForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        usuario = get_user_model().objects.create_user(username=form.cleaned_data['usuario'], password=None, first_name=profesor.nombres, last_name=profesor.apellidos, email=profesor.email)
        PerfilPersonal.objects.create(usuario=usuario, rut=profesor.rut, telefono=profesor.telefono)
        grupo, _ = Group.objects.get_or_create(name='Profesor')
        usuario.groups.add(grupo)
        profesor.usuario = usuario
        profesor.save(update_fields=['usuario'])
        codigo = secrets.token_urlsafe(16)
        ActivacionProfesor.objects.create(profesor=profesor, codigo_hash=make_password(codigo), vence_en=timezone.now()+timedelta(hours=24))
        return render(request, 'profesor/codigo_activacion.html', {'profesor': profesor, 'usuario_creado': usuario.username, 'codigo': codigo})
    return render(request, 'profesor/crear_cuenta.html', {'profesor': profesor, 'form': form})


@never_cache
@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def regenerar_codigo_profesor_view(request, profesor_id):
    profesor = get_object_or_404(Profesor, pk=profesor_id)
    activacion = ActivacionProfesor.objects.select_for_update().filter(profesor=profesor).first()
    usuario = profesor.usuario if profesor.usuario_id else None
    if not profesor.activo or not usuario or not usuario.is_active or (activacion and activacion.usado_en) or usuario.has_usable_password():
        messages.warning(request, 'No hay una activación pendiente para este profesor.')
        return redirect('gestion_list', modulo='profesores')
    if not hasattr(usuario, 'perfil_personal') and (not profesor.rut or PerfilPersonal.objects.filter(rut=profesor.rut).exists()):
        messages.warning(request, 'Completa un RUT válido y disponible en la ficha antes de activar la cuenta.')
        return redirect('gimnasio_ficha', modulo='profesores', objeto_id=profesor.pk)
    if request.method == 'POST':
        if not hasattr(usuario, 'perfil_personal'):
            PerfilPersonal.objects.create(usuario=usuario, rut=profesor.rut, telefono=profesor.telefono)
        codigo = secrets.token_urlsafe(16)
        activacion, _ = ActivacionProfesor.objects.update_or_create(profesor=profesor, defaults={
            'codigo_hash': make_password(codigo), 'vence_en': timezone.now() + timedelta(hours=24),
            'intentos_fallidos': 0, 'bloqueado_hasta': None,
        })
        return render(request, 'profesor/codigo_activacion.html', {'profesor': profesor, 'usuario_creado': usuario.username, 'codigo': codigo, 'regenerado': True})
    return render(request, 'profesor/regenerar_codigo.html', {'profesor': profesor})


@never_cache
@transaction.atomic
def activar_cuenta_profesor_view(request):
    form = ActivarCuentaPersonalForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        rut = validar_rut_personal_seguro(form.cleaned_data['rut'])
        usuario = get_user_model().objects.filter(perfil_personal__rut=rut).first() if rut else None
        activacion = ActivacionProfesor.objects.select_for_update().select_related('profesor').filter(profesor__usuario=usuario).first() if usuario else None
        ahora = timezone.now()
        if not activacion or not activacion.profesor.activo or not usuario.is_active or activacion.usado_en or activacion.vence_en <= ahora:
            form.add_error(None, 'No se pudo activar la cuenta. Revisa los datos o contacta a administración.')
        elif activacion.bloqueado_hasta and activacion.bloqueado_hasta > ahora:
            form.add_error(None, 'Demasiados intentos. Espera 15 minutos antes de volver a intentarlo.')
        else:
            if activacion.bloqueado_hasta:
                activacion.intentos_fallidos = 0
                activacion.bloqueado_hasta = None
            if not check_password(form.cleaned_data['codigo'], activacion.codigo_hash):
                activacion.intentos_fallidos += 1
                if activacion.intentos_fallidos >= 3:
                    activacion.bloqueado_hasta = ahora + timedelta(minutes=15)
                activacion.save(update_fields=['intentos_fallidos', 'bloqueado_hasta'])
                form.add_error(None, 'No se pudo activar la cuenta. Revisa los datos o contacta a administración.')
            else:
                try:
                    password_validation.validate_password(form.cleaned_data['clave'], usuario)
                except ValidationError as exc:
                    form.add_error('clave', exc)
                if not form.errors:
                    usuario.set_password(form.cleaned_data['clave'])
                    usuario.save(update_fields=['password'])
                    perfil = usuario.perfil_personal
                    perfil.pregunta_seguridad = form.cleaned_data['pregunta']
                    perfil.respuesta_seguridad_hash = make_password(form.cleaned_data['respuesta'].strip().casefold())
                    perfil.save(update_fields=['pregunta_seguridad', 'respuesta_seguridad_hash'])
                    activacion.usado_en = ahora
                    activacion.save(update_fields=['usado_en', 'intentos_fallidos', 'bloqueado_hasta'])
                    messages.success(request, 'Cuenta activada. Ya puedes iniciar sesión con tu contraseña.')
                    return redirect('login')
    return render(request, 'profesor/activar_cuenta.html', {'form': form})


@login_required
@never_cache
def profesor_cambiar_clave_view(request):
    if not es_profesor(request.user) or es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para profesores autorizados.')
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        usuario = form.save()
        update_session_auth_hash(request, usuario)
        messages.success(request, 'Tu contraseña se actualizó correctamente.')
        return redirect('profesor_perfil')
    return render(request, 'profesor/cambiar_clave.html', {'form': form})


@login_required
def profesor_perfil_view(request):
    if not es_profesor(request.user) or es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para profesores autorizados.')
    profesor = get_object_or_404(Profesor, usuario=request.user)
    form = ContactoProfesorForm(request.POST or None, instance=profesor)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Tus datos de contacto se actualizaron correctamente.')
        return redirect('profesor_perfil')
    return render(request, 'profesor/perfil.html', {'profesor': profesor, 'form': form})


@login_required
def profesor_notificaciones_view(request):
    if not es_profesor(request.user) or es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para profesores autorizados.')
    avisos = NotificacionProfesor.objects.filter(usuario=request.user, oculta=False)
    avisos.filter(leida=False).update(leida=True)
    return render(request, 'profesor/notificaciones.html', {'avisos': avisos})


@login_required
@never_cache
def profesor_avisos_resumen_view(request):
    if not es_profesor(request.user) or es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para profesores autorizados.')
    avisos = NotificacionProfesor.objects.filter(usuario=request.user, oculta=False)
    return JsonResponse({'sin_leer': avisos.filter(leida=False).exists(), 'avisos': [{'mensaje': a.mensaje, 'fecha': timezone.localtime(a.creada).strftime('%d/%m/%Y %H:%M'), 'leida': a.leida, 'url': reverse('profesor_solicitudes') if a.solicitud_id else (reverse('profesor_detalle_clase', args=[a.clase_id]) if a.clase_id and a.clase.activo and a.clase.profesor.usuario_id == request.user.pk else None)} for a in avisos.select_related('clase__profesor')[:5]]})


@login_required
@never_cache
def profesor_limpiar_notificaciones_view(request):
    if not es_profesor(request.user) or es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para profesores autorizados.')
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    NotificacionProfesor.objects.filter(usuario=request.user, oculta=False).update(oculta=True, leida=True)
    messages.success(request, 'Se limpió tu historial de notificaciones.')
    return redirect('profesor_notificaciones')


@login_required
def profesor_clases_view(request):
    from .horarios_fijos import asegurar_horarios
    if not es_profesor(request.user) or es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para profesores autorizados.')
    profesor = get_object_or_404(Profesor.objects, usuario=request.user)
    asegurar_horarios()
    clases_base = Clase.objects.filter(profesor=profesor).select_related('sala_asignada', 'actividad')
    ahora = timezone.now()
    proxima = next((clase for clase in clases_base.filter(fecha__gte=timezone.localdate()).order_by('fecha', 'hora_inicio') if inicio_clase_local(clase) >= ahora), None)
    vista = request.GET.get('vista', 'proximas')
    if vista not in ('proximas', 'anteriores'):
        vista = 'proximas'
    clases_base = clases_base.filter(fecha__lt=timezone.localdate()).order_by('-fecha', '-hora_inicio') if vista == 'anteriores' else clases_base.filter(fecha__gte=timezone.localdate()).order_by('fecha', 'hora_inicio')
    consulta = request.GET.get('q', '').strip()
    if consulta:
        clases_base = clases_base.filter(Q(nombre__icontains=consulta) | Q(actividad__nombre__icontains=consulta))
    clases = list(clases_base)
    for clase in clases:
        clase.imagen_tipo = imagen_tipo_clase(clase)
    return render(request, 'profesor/clases.html', {'clases': clases, 'profesor': profesor, 'proxima_id': proxima.pk if proxima else None, 'vista': vista, 'consulta': consulta})


@login_required
@transaction.atomic
def avisar_indisponibilidad_view(request, clase_id):
    if not es_profesor(request.user) or es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Solo el profesor asignado puede enviar este aviso.')
    clase = get_object_or_404(Clase.objects.select_for_update(), pk=clase_id, profesor__usuario=request.user)
    if request.method == 'POST':
        motivo = request.POST.get('motivo', '').strip()
        if timezone.now() > inicio_clase_local(clase) - timedelta(hours=48):
            messages.warning(request, 'El aviso debe enviarse con al menos 48 horas de anticipación. Si surge una emergencia, contacta directamente al gimnasio.')
        elif not motivo or len(motivo) > 1000:
            messages.warning(request, 'Escribe un motivo de hasta 1000 caracteres.')
        elif AvisoIndisponibilidad.objects.filter(clase=clase, profesor=clase.profesor, revisado_en__isnull=True).exists():
            messages.warning(request, 'Ya existe un aviso pendiente para esta clase.')
        else:
            aviso = AvisoIndisponibilidad.objects.create(clase=clase, profesor=clase.profesor, motivo=motivo)
            NotificacionRecepcion.objects.create(clase=clase, destino=reverse('avisos_indisponibilidad') + f'#aviso-{aviso.pk}', mensaje=f'El profesor informó que no estará disponible para {clase.nombre} del {clase.fecha:%d/%m/%Y} a las {clase.hora_inicio:%H:%M}. La clase continúa programada hasta que administración actúe.')
            messages.success(request, 'Aviso enviado a administración. La clase sigue programada hasta que administración actúe.')
        return redirect('profesor_detalle_clase', clase_id=clase.pk)
    return render(request, 'profesor/avisar_indisponibilidad.html', {'clase': clase})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
def avisos_indisponibilidad_view(request):
    avisos = AvisoIndisponibilidad.objects.filter(revisado_en__isnull=True).select_related('clase', 'profesor', 'revisado_por')
    return render(request, 'gestion/avisos_indisponibilidad.html', {'avisos': avisos, 'profesores': Profesor.objects.order_by('nombres', 'apellidos')})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def revisar_indisponibilidad_view(request, aviso_id):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    aviso = get_object_or_404(AvisoIndisponibilidad.objects.select_for_update(), pk=aviso_id)
    if aviso.revisado_en is None:
        aviso.revisado_en = timezone.now()
        aviso.revisado_por = request.user
        aviso.resolucion = 'Marcado como revisado'
        aviso.save(update_fields=['revisado_en', 'revisado_por', 'resolucion'])
    return redirect('avisos_indisponibilidad')


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def reemplazar_profesor_aviso_view(request, aviso_id):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    aviso = get_object_or_404(AvisoIndisponibilidad.objects.select_for_update().select_related('clase', 'profesor'), pk=aviso_id)
    clase = get_object_or_404(Clase.all_objects.select_for_update(), pk=aviso.clase_id)
    profesor = Profesor.objects.select_for_update().filter(pk=request.POST.get('profesor', '')).first() if request.POST.get('profesor', '').isdigit() else None
    if aviso.revisado_en or not clase.activo or inicio_clase_local(clase) <= timezone.now() or clase.profesor_id != aviso.profesor_id:
        messages.warning(request, 'El aviso o la clase ya cambió; revisa sus datos antes de continuar.')
    elif not profesor or profesor.pk == aviso.profesor_id:
        messages.warning(request, 'Selecciona otro profesor activo.')
    elif Clase.objects.filter(profesor=profesor, fecha=clase.fecha, hora_inicio__lt=clase.hora_fin, hora_fin__gt=clase.hora_inicio).exclude(pk=clase.pk).exists():
        messages.warning(request, 'El profesor seleccionado tiene otra clase en ese horario.')
    else:
        clase.profesor = profesor
        clase.save(update_fields=['profesor'])
        aviso.revisado_en = timezone.now()
        aviso.revisado_por = request.user
        aviso.resolucion = f'Reemplazo asignado: {profesor.nombres} {profesor.apellidos}'
        aviso.save(update_fields=['revisado_en', 'revisado_por', 'resolucion'])
        if profesor.usuario_id:
            NotificacionProfesor.objects.create(usuario=profesor.usuario, clase=clase, mensaje=f'Te asignaron como reemplazo en {clase.nombre} del {clase.fecha:%d/%m/%Y}.')
        if aviso.profesor.usuario_id:
            NotificacionProfesor.objects.create(usuario=aviso.profesor.usuario, clase=clase, mensaje=f'Se asignó un reemplazo para {clase.nombre} del {clase.fecha:%d/%m/%Y}.')
        for reserva in Reserva.objects.filter(clase=clase, estado__in=['CONFIRMADA', 'LISTA_ESPERA']).select_related('socio'):
            NotificacionSocio.objects.create(socio=reserva.socio, reserva=reserva, tipo=f'CAMBIO_PROF_{aviso.pk}', mensaje=f'La clase {clase.nombre} del {clase.fecha:%d/%m/%Y} tendrá como profesor a {profesor.nombres} {profesor.apellidos}.')
        messages.success(request, 'Reemplazo asignado y aviso resuelto.')
    return redirect('avisos_indisponibilidad')


@login_required
def profesor_detalle_clase_view(request, clase_id):
    if not es_profesor(request.user) or es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para profesores autorizados.')
    clase = get_object_or_404(Clase.objects.select_related('profesor', 'sala_asignada'), pk=clase_id, profesor__usuario=request.user)
    from django.core import signing
    reservas = list(clase.reservas.filter(estado='CONFIRMADA').select_related('socio').order_by('socio__apellidos', 'socio__nombres'))
    snapshot = signing.dumps([[r.pk, r.asistencia, r.fecha_actualizacion.isoformat()] for r in reservas], salt='profesor.asistencia')
    pendientes = set(SolicitudCorreccionAsistencia.objects.filter(reserva__in=reservas, estado='PENDIENTE').values_list('reserva_id', flat=True))
    for reserva in reservas:
        reserva.correccion_pendiente = reserva.pk in pendientes
    from datetime import datetime
    fin = timezone.make_aware(datetime.combine(clase.fecha, clase.hora_fin), timezone.get_current_timezone())
    aviso_pendiente = AvisoIndisponibilidad.objects.filter(clase=clase, profesor=clase.profesor, revisado_en__isnull=True).exists()
    return render(request, 'profesor/detalle_clase.html', {'clase': clase, 'reservas': reservas,
        'puede_avisar': timezone.now() <= inicio_clase_local(clase) - timedelta(hours=48) and not aviso_pendiente,
        'aviso_pendiente': aviso_pendiente, 'puede_cerrar': timezone.now() >= fin, 'snapshot': snapshot})


@login_required
@transaction.atomic
def profesor_guardar_asistencia_view(request, clase_id):
    from django.core import signing
    from django.http import HttpResponseNotAllowed
    if not es_profesor(request.user) or es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para profesores autorizados.')
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    clase = get_object_or_404(Clase.objects.select_for_update(), pk=clase_id, profesor__usuario=request.user)
    destino = redirect('profesor_detalle_clase', clase_id=clase.pk)
    if clase.asistencia_cerrada_en:
        messages.warning(request, 'La asistencia ya está cerrada. Solicita una corrección a administración.')
        return destino
    reservas = list(clase.reservas.select_for_update().filter(estado='CONFIRMADA').order_by('pk'))
    actual = sorted([[r.pk, r.asistencia, r.fecha_actualizacion.isoformat()] for r in reservas])
    try:
        anterior = sorted(signing.loads(request.POST.get('snapshot', ''), salt='profesor.asistencia', max_age=86400))
    except (signing.BadSignature, TypeError, ValueError):
        messages.warning(request, 'La lista ha caducado. Vuelve a revisarla antes de guardar.')
        return destino
    seleccion = request.POST.getlist('asistio')
    ids = {str(r.pk) for r in reservas}
    if actual != anterior or len(seleccion) != len(set(seleccion)) or not set(seleccion).issubset(ids):
        messages.warning(request, 'La lista cambió mientras la revisabas. Comprueba la asistencia y vuelve a guardar.')
        return destino
    for reserva in reservas:
        nuevo = 'ASISTIO' if str(reserva.pk) in seleccion else ('NO_ASISTIO' if reserva.asistencia == 'NO_ASISTIO' else 'PENDIENTE')
        if reserva.asistencia != nuevo:
            HistorialAsistencia.objects.create(reserva=reserva, estado_anterior=reserva.asistencia, estado_nuevo=nuevo, usuario=request.user, origen='PROFESOR')
            reserva.asistencia = nuevo
            reserva.asistencia_registrada_por = request.user
            reserva.fecha_asistencia = timezone.now()
            reserva.save(update_fields=['asistencia', 'asistencia_registrada_por', 'fecha_asistencia', 'fecha_actualizacion'])
    messages.success(request, 'Asistencia guardada.')
    if request.POST.get('accion') == 'revisar':
        from datetime import datetime
        fin = timezone.make_aware(datetime.combine(clase.fecha, clase.hora_fin), timezone.get_current_timezone())
        if timezone.now() >= fin:
            return redirect('cerrar_asistencia_clase', clase_id=clase.pk)
        messages.warning(request, 'La asistencia se puede cerrar cuando termina la clase.')
    return destino


@login_required
@transaction.atomic
def profesor_marcar_asistencia_view(request, clase_id, reserva_id):
    if not es_profesor(request.user) or es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para profesores autorizados.')
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    clase = get_object_or_404(Clase.objects.select_for_update(), pk=clase_id, profesor__usuario=request.user)
    reserva = get_object_or_404(Reserva.objects.select_for_update(), pk=reserva_id, clase=clase, estado='CONFIRMADA')
    if clase.asistencia_cerrada_en:
        messages.warning(request, 'La asistencia está cerrada. Solicita una corrección a administración.')
        return redirect('profesor_detalle_clase', clase_id=clase.pk)
    nuevo = request.POST.get('asistencia')
    if nuevo not in ('ASISTIO', 'PENDIENTE'):
        return HttpResponseForbidden('Estado de asistencia no permitido.')
    if nuevo != reserva.asistencia:
        HistorialAsistencia.objects.create(reserva=reserva, estado_anterior=reserva.asistencia, estado_nuevo=nuevo, usuario=request.user, origen='PROFESOR')
        reserva.asistencia = nuevo
        reserva.asistencia_registrada_por = request.user
        reserva.fecha_asistencia = timezone.now()
        reserva.save(update_fields=['asistencia', 'asistencia_registrada_por', 'fecha_asistencia', 'fecha_actualizacion'])
        messages.success(request, 'Asistencia actualizada.')
    return redirect('profesor_detalle_clase', clase_id=clase.pk)


@login_required
@transaction.atomic
def cerrar_asistencia_clase_view(request, clase_id):
    es_admin = es_administrador(request.user)
    if not es_admin and (not es_profesor(request.user) or es_recepcionista_o_admin(request.user)):
        return HttpResponseForbidden('Solo el profesor asignado o administración pueden cerrar la asistencia.')
    clases = Clase.objects.select_for_update()
    if not es_admin:
        clases = clases.filter(profesor__usuario=request.user)
    clase = get_object_or_404(clases, pk=clase_id)
    reservas = clase.reservas.filter(estado='CONFIRMADA').select_related('socio').order_by('socio__apellidos', 'socio__nombres')
    if request.method == 'POST':
        from datetime import datetime
        fin = timezone.make_aware(datetime.combine(clase.fecha, clase.hora_fin), timezone.get_current_timezone())
        if clase.asistencia_cerrada_en:
            messages.warning(request, 'La asistencia ya está cerrada.')
        elif timezone.now() < fin:
            messages.warning(request, 'La asistencia se puede cerrar cuando termina la clase.')
        elif request.POST.get('confirmar') != 'si':
            messages.warning(request, 'Confirma el cierre antes de continuar.')
        else:
            for reserva in reservas.select_for_update():
                if reserva.asistencia == 'PENDIENTE':
                    HistorialAsistencia.objects.create(reserva=reserva, estado_anterior='PENDIENTE', estado_nuevo='NO_ASISTIO', usuario=request.user, origen='CIERRE')
                    reserva.asistencia = 'NO_ASISTIO'
                    reserva.asistencia_registrada_por = request.user
                    reserva.fecha_asistencia = timezone.now()
                    reserva.save(update_fields=['asistencia', 'asistencia_registrada_por', 'fecha_asistencia', 'fecha_actualizacion'])
            clase.asistencia_cerrada_en = timezone.now()
            clase.asistencia_cerrada_por = request.user
            clase.save(update_fields=['asistencia_cerrada_en', 'asistencia_cerrada_por'])
            messages.success(request, 'Lista cerrada. Las personas pendientes quedaron como no asistieron.')
        return redirect('clases_list' if es_admin else 'profesor_detalle_clase', **({} if es_admin else {'clase_id': clase.pk}))
    return render(request, 'profesor/cerrar_asistencia_admin.html' if es_admin else 'profesor/cerrar_asistencia.html', {'clase': clase, 'reservas': reservas, 'es_admin_cierre': es_admin})


@login_required
def profesor_solicitudes_view(request):
    if not es_profesor(request.user) or es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para profesores autorizados.')
    solicitudes = SolicitudCorreccionAsistencia.objects.filter(solicitante=request.user).select_related(
        'reserva__socio', 'reserva__clase', 'revisado_por',
    )
    avisos = AvisoIndisponibilidad.objects.filter(profesor__usuario=request.user).select_related('clase')
    filtro = request.GET.get('tipo', 'todas')
    if filtro not in ('todas', 'correcciones', 'indisponibilidad'):
        filtro = 'todas'
    return render(request, 'profesor/solicitudes.html', {'solicitudes': solicitudes, 'avisos': avisos, 'filtro': filtro})


@login_required
@transaction.atomic
def solicitar_correccion_asistencia_view(request, clase_id, reserva_id):
    if not es_profesor(request.user) or es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Solo el profesor asignado puede solicitar correcciones.')
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    clase = get_object_or_404(Clase.objects.select_for_update(), pk=clase_id, profesor__usuario=request.user)
    reserva = get_object_or_404(Reserva.objects.select_for_update(), pk=reserva_id, clase=clase, estado='CONFIRMADA')
    nuevo = request.POST.get('estado_solicitado', '')
    motivo = request.POST.get('motivo', '').strip()
    if not clase.asistencia_cerrada_en:
        messages.warning(request, 'La lista aún no está cerrada; puedes corregirla directamente.')
    elif nuevo not in ('ASISTIO', 'NO_ASISTIO') or not motivo:
        messages.warning(request, 'Indica el estado correcto y el motivo de la solicitud.')
    elif nuevo == reserva.asistencia:
        messages.warning(request, 'El estado solicitado ya está registrado.')
    elif SolicitudCorreccionAsistencia.objects.filter(reserva=reserva, estado='PENDIENTE').exists():
        messages.warning(request, 'Ya existe una solicitud pendiente para esta reserva.')
    else:
        solicitud = SolicitudCorreccionAsistencia.objects.create(reserva=reserva, solicitante=request.user, estado_solicitado=nuevo, motivo=motivo)
        from django.contrib.auth import get_user_model
        admins = get_user_model().objects.filter(Q(is_superuser=True) | Q(groups__name='Administrador'), is_active=True).distinct()
        NotificacionRecepcion.objects.bulk_create([NotificacionRecepcion(destinatario=admin, destino=reverse('solicitudes_asistencia') + f'#asistencia-{solicitud.pk}', mensaje=f'Nueva solicitud de corrección de asistencia para {reserva.socio.nombre_completo}.') for admin in admins])
        messages.success(request, 'Solicitud enviada a administración.')
    return redirect('profesor_detalle_clase', clase_id=clase.pk)


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@never_cache
def solicitudes_congelamiento_view(request):
    from django.core import signing
    from .models import SolicitudCongelamiento
    from .services import impacto_reactivacion, propuesta_renovacion_reactivacion, reservas_fuera_de_renovacion, procesar_reactivaciones_vencidas
    from datetime import date
    procesar_reactivaciones_vencidas()
    solicitudes = SolicitudCongelamiento.objects.filter(Q(estado='PENDIENTE') | Q(reactivacion_pendiente=True)).select_related('membresia__socio', 'membresia__plan', 'solicitante').order_by('-creada_en', '-pk')
    revisiones = []
    for solicitud in solicitudes:
        membresia = solicitud.membresia
        reservas = Reserva.objects.filter(socio_id=membresia.socio_id, estado__in=['CONFIRMADA', 'LISTA_ESPERA'], clase__fecha__range=(solicitud.fecha_inicio, solicitud.fecha_fin)).select_related('clase').order_by('clase__fecha', 'clase__hora_inicio')
        fin_estimado = membresia.fecha_fin + timedelta(days=solicitud.dias_solicitados)
        futuras = Membresia.objects.filter(socio_id=membresia.socio_id, estado='ACTIVA', fecha_inicio__gt=membresia.fecha_fin, fecha_inicio__lte=fin_estimado).exclude(pk=membresia.pk).select_related('plan').order_by('fecha_inicio')
        impacto = sorted([[r.pk, r.estado, r.clase_id] for r in reservas])
        conflictos = list(Membresia.objects.filter(socio_id=membresia.socio_id, estado__in=['ACTIVA', 'CONGELADA'], fecha_inicio__lte=fin_estimado, fecha_fin__gte=membresia.fecha_inicio).exclude(pk=membresia.pk).order_by('fecha_inicio', 'pk'))
        renovaciones = [[m.pk, m.fecha_inicio.isoformat(), m.fecha_fin.isoformat()] for m in conflictos]
        futuras_mostradas = []
        reservas_renovacion = []
        for futura in futuras:
            delta = fin_estimado + timedelta(days=1) - futura.fecha_inicio
            inicio_nuevo, fin_nuevo = futura.fecha_inicio + delta, futura.fecha_fin + delta
            afectadas = list(reservas_fuera_de_renovacion(futura, inicio_nuevo, fin_nuevo))
            reservas_renovacion.extend([[r.pk, r.estado, r.clase_id] for r in afectadas])
            futuras_mostradas.append({'membresia': futura, 'inicio_nuevo': inicio_nuevo, 'fin_nuevo': fin_nuevo, 'reservas_afectadas': afectadas})
        propuesta = signing.dumps({'solicitud': solicitud.pk, 'usuario': request.user.pk, 'inicio': solicitud.fecha_inicio.isoformat(), 'fin': solicitud.fecha_fin.isoformat(), 'vencimiento': membresia.fecha_fin.isoformat(), 'reservas': impacto, 'renovaciones': renovaciones, 'reservas_renovacion': sorted(reservas_renovacion)}, salt='fitlife.aprobar_pausa')
        reactivacion = None
        if solicitud.reactivacion_pendiente:
            hoy = timezone.localdate()
            nuevo_fin, reservas_reactivacion, otras_pausas = impacto_reactivacion(solicitud, hoy)
            renovacion = propuesta_renovacion_reactivacion(solicitud, nuevo_fin)
            reservas_futuras = Reserva.objects.none()
            if solicitud.renovacion_ajustada_id and renovacion:
                futura = solicitud.renovacion_ajustada
                reservas_futuras = reservas_fuera_de_renovacion(futura, date.fromisoformat(renovacion[3]), date.fromisoformat(renovacion[4]))
            reservas_firmadas = [[r.pk, r.estado, r.clase_id] for r in reservas_futuras]
            reservas_actuales = [[r.pk, r.estado, r.clase_id] for r in reservas_reactivacion]
            token = signing.dumps({'solicitud': solicitud.pk, 'usuario': request.user.pk, 'fecha': hoy.isoformat(), 'nuevo_fin': nuevo_fin.isoformat(), 'renovacion': renovacion, 'reservas_futuras': reservas_firmadas, 'reservas_actuales': reservas_actuales}, salt='fitlife.reactivacion')
            reactivacion = {'nuevo_fin': nuevo_fin, 'reservas': reservas_reactivacion, 'otras_pausas': otras_pausas, 'renovacion': renovacion, 'reservas_futuras': reservas_futuras, 'propuesta': token}
        revisiones.append({'solicitud': solicitud, 'reservas': reservas, 'fin_estimado': fin_estimado, 'futuras': futuras_mostradas, 'requiere_actualizacion': solicitud.fecha_inicio < timezone.localdate(), 'propuesta': propuesta, 'reactivacion': reactivacion})
    return render(request, 'gestion/solicitudes_congelamiento.html', {'revisiones': revisiones})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def aprobar_reactivacion_view(request, solicitud_id):
    from django.core import signing
    from .models import SolicitudCongelamiento
    from .services import aprobar_reactivacion
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    get_object_or_404(SolicitudCongelamiento, pk=solicitud_id)
    try:
        propuesta = signing.loads(request.POST.get('propuesta', ''), salt='fitlife.reactivacion', max_age=1800)
        if propuesta['solicitud'] != solicitud_id or propuesta['usuario'] != request.user.pk:
            raise signing.BadSignature
        aprobar_reactivacion(solicitud_id, request.user, propuesta['fecha'], propuesta['nuevo_fin'], propuesta['renovacion'], propuesta['reservas_futuras'], propuesta['reservas_actuales'])
    except (signing.BadSignature, KeyError, TypeError):
        messages.warning(request, 'La revisión venció o no es válida. Revisa nuevamente la solicitud.')
    except ValidationError as error:
        messages.warning(request, ' '.join(error.messages))
    else:
        messages.success(request, 'Reactivación aprobada y socio notificado.')
    return redirect('solicitudes_congelamiento')


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def aprobar_congelamiento_view(request, solicitud_id):
    from django.core import signing
    from .models import SolicitudCongelamiento
    from .services import aprobar_congelamiento_sin_conflictos
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    solicitud = get_object_or_404(SolicitudCongelamiento.objects.select_related('membresia'), pk=solicitud_id)
    try:
        propuesta = signing.loads(request.POST.get('propuesta', ''), salt='fitlife.aprobar_pausa', max_age=1800)
        if propuesta['solicitud'] != solicitud.pk or propuesta['usuario'] != request.user.pk or propuesta['inicio'] != solicitud.fecha_inicio.isoformat() or propuesta['fin'] != solicitud.fecha_fin.isoformat() or propuesta['vencimiento'] != solicitud.membresia.fecha_fin.isoformat():
            raise ValidationError('Las condiciones cambiaron; revisa nuevamente la solicitud.')
        aprobar_congelamiento_sin_conflictos(solicitud_id, request.user, propuesta['reservas'], propuesta['vencimiento'], propuesta['renovaciones'], propuesta['reservas_renovacion'])
    except (signing.BadSignature, KeyError, TypeError):
        messages.warning(request, 'La revisión venció o no es válida. Revisa nuevamente la solicitud.')
    except ValidationError as error:
        messages.warning(request, ' '.join(error.messages))
    else:
        messages.success(request, 'Congelamiento aprobado y socio notificado.')
    return redirect('solicitudes_congelamiento')


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def rechazar_congelamiento_view(request, solicitud_id):
    from .models import SolicitudCongelamiento
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    solicitud = get_object_or_404(SolicitudCongelamiento.objects.select_for_update().select_related('membresia__socio'), pk=solicitud_id)
    motivo = request.POST.get('motivo', '').strip()
    if solicitud.estado != 'PENDIENTE':
        messages.warning(request, 'Esta solicitud ya no está pendiente.')
    elif not motivo:
        messages.warning(request, 'Indica el motivo del rechazo.')
    else:
        solicitud.estado = 'RECHAZADA'
        solicitud.motivo_rechazo = motivo
        solicitud.revisado_por = request.user
        solicitud.revisada_en = timezone.now()
        solicitud.save(update_fields=['estado', 'motivo_rechazo', 'revisado_por', 'revisada_en'])
        NotificacionSocio.objects.create(socio=solicitud.membresia.socio, tipo='CONGELAMIENTO_RECHAZADO', mensaje=f'Tu solicitud de congelamiento fue rechazada. Motivo: {motivo}')
        messages.success(request, 'Solicitud rechazada. Se notificó al socio.')
    return redirect('solicitudes_congelamiento')


@login_required
@transaction.atomic
def socio_solicitar_reactivacion_view(request, solicitud_id):
    from .models import SolicitudCongelamiento
    from .services import solicitar_reactivacion
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    get_object_or_404(SolicitudCongelamiento, pk=solicitud_id, membresia__socio__usuario=request.user)
    try:
        solicitar_reactivacion(solicitud_id, request.user)
    except ValidationError as error:
        messages.warning(request, ' '.join(error.messages))
    else:
        messages.success(request, 'Solicitud de reactivación enviada. Tu pausa continúa hasta que administración la apruebe.')
    return redirect('socio_membresias')


@login_required
@transaction.atomic
def cancelar_solicitud_congelamiento_view(request, solicitud_id):
    from .models import SolicitudCongelamiento
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    solicitud = get_object_or_404(SolicitudCongelamiento.objects.select_for_update(), pk=solicitud_id, membresia__socio__usuario=request.user)
    if solicitud.estado == 'PENDIENTE':
        solicitud.estado = 'CANCELADA'
        solicitud.save(update_fields=['estado'])
        messages.success(request, 'Tu solicitud pendiente fue cancelada. Tu membresía no cambió.')
    else:
        messages.warning(request, 'Esta solicitud ya no está pendiente.')
    return redirect('socio_membresias')


@login_required
@never_cache
def socio_cancelar_pausa_aprobada_view(request, solicitud_id):
    from django.core import signing
    from .models import SolicitudCongelamiento
    from .services import impacto_cancelar_pausa, cancelar_pausa_aprobada, reservas_fuera_de_renovacion
    if not es_socio(request.user) or es_recepcionista_o_admin(request.user) or es_profesor(request.user):
        return HttpResponseForbidden('Esta sección está disponible para socios autorizados.')
    solicitud = get_object_or_404(SolicitudCongelamiento.objects.select_related('membresia__socio', 'membresia__plan', 'renovacion_ajustada__plan'), pk=solicitud_id, membresia__socio__usuario=request.user)
    if solicitud.estado != 'APROBADA' or solicitud.fecha_inicio <= timezone.localdate() or not solicitud.membresia.activo or solicitud.membresia.estado != 'ACTIVA':
        messages.warning(request, 'Solo se puede cancelar una pausa aprobada antes de su inicio.')
        return redirect('socio_membresias')
    nuevo_fin, reservas, otras_pausas = impacto_cancelar_pausa(solicitud)
    if request.method == 'POST':
        try:
            propuesta = signing.loads(request.POST.get('propuesta', ''), salt='fitlife.cancelar_pausa', max_age=1800)
            if propuesta['solicitud'] != solicitud.pk or propuesta['usuario'] != request.user.pk:
                raise signing.BadSignature
            cancelar_pausa_aprobada(solicitud.pk, request.user, propuesta['nuevo_fin'], propuesta['renovacion'], propuesta['reservas_renovacion'], propuesta['reservas_actuales'])
        except (signing.BadSignature, KeyError, TypeError, ValidationError):
            messages.warning(request, 'No se pudo cancelar la pausa. Revisa nuevamente las fechas y posibles conflictos.')
            return redirect('socio_cancelar_pausa_aprobada', solicitud_id=solicitud.pk)
        messages.success(request, 'La pausa programada fue cancelada y se ajustó el vencimiento.')
        return redirect('socio_membresias')
    futura = solicitud.renovacion_ajustada
    renovacion = None
    reservas_renovacion = Reserva.objects.none()
    conflicto_renovacion = False
    if futura:
        anterior_inicio, anterior_fin = solicitud.renovacion_inicio_anterior, solicitud.renovacion_fin_anterior
        renovacion = [futura.pk, futura.fecha_inicio.isoformat(), futura.fecha_fin.isoformat(), anterior_inicio.isoformat() if anterior_inicio else None, anterior_fin.isoformat() if anterior_fin else None]
        if anterior_inicio and anterior_fin:
            reservas_renovacion = reservas_fuera_de_renovacion(futura, anterior_inicio, anterior_fin)
            conflicto_renovacion = (not futura.activo or futura.estado != 'ACTIVA' or anterior_inicio <= nuevo_fin or anterior_inicio <= timezone.localdate() or futura.solicitudes_congelamiento.filter(estado__in=['PENDIENTE', 'APROBADA']).exists() or Membresia.objects.filter(socio_id=solicitud.membresia.socio_id, estado__in=['ACTIVA', 'CONGELADA'], fecha_inicio__lte=anterior_fin, fecha_fin__gte=anterior_inicio).exclude(pk__in=[solicitud.membresia_id, futura.pk]).exists())
        else:
            conflicto_renovacion = True
    reservas_firmadas = [[r.pk, r.estado, r.clase_id] for r in reservas_renovacion]
    reservas_actuales = [[r.pk, r.estado, r.clase_id] for r in reservas]
    propuesta = signing.dumps({'solicitud': solicitud.pk, 'usuario': request.user.pk, 'nuevo_fin': nuevo_fin.isoformat(), 'renovacion': renovacion, 'reservas_renovacion': reservas_firmadas, 'reservas_actuales': reservas_actuales}, salt='fitlife.cancelar_pausa')
    return render(request, 'socio/cancelar_pausa.html', {'solicitud': solicitud, 'nuevo_fin': nuevo_fin, 'reservas': reservas, 'otras_pausas': otras_pausas, 'futura': futura, 'reservas_renovacion': reservas_renovacion, 'conflicto_renovacion': conflicto_renovacion, 'propuesta': propuesta})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@never_cache
def solicitudes_historial_view(request):
    from .models import SolicitudCongelamiento
    from .services import procesar_reactivaciones_vencidas
    procesar_reactivaciones_vencidas()
    from django.core.paginator import Paginator
    from .models import SolicitudAnulacionPago
    categoria = request.GET.get('categoria', '')
    consulta = request.GET.get('q', '').strip()
    filas = []
    def agregar(tipo, nombre, fecha, estado, objeto):
        if categoria and categoria != tipo:
            return
        if consulta and consulta.casefold() not in f'{nombre} {tipo} {estado}'.casefold():
            return
        filas.append({'tipo': tipo, 'nombre': nombre, 'fecha': fecha, 'estado': estado, 'objeto': objeto})
    for o in SolicitudCongelamiento.objects.exclude(estado='PENDIENTE').select_related('membresia__socio', 'membresia__plan'):
        agregar('congelamiento', o.membresia.socio.nombre_completo, o.revisada_en or o.cancelada_en or o.creada_en, o.get_estado_display(), o)
    for o in SolicitudCorreccionAsistencia.objects.exclude(estado='PENDIENTE').select_related('reserva__socio', 'reserva__clase'):
        agregar('asistencia', o.reserva.socio.nombre_completo, o.fecha_revision or o.fecha_solicitud, o.get_estado_display(), o)
    for o in AvisoIndisponibilidad.objects.filter(revisado_en__isnull=False).select_related('clase', 'profesor'):
        agregar('avisos', f'{o.profesor.nombres} {o.profesor.apellidos}', o.revisado_en, 'Revisado', o)
    for o in SolicitudAnulacionPago.objects.exclude(estado='PENDIENTE').select_related('pago__membresia__socio'):
        agregar('pagos', o.pago.membresia.socio.nombre_completo, o.revisada or o.creada, o.get_estado_display(), o)
    filas.sort(key=lambda f: (f['fecha'], f['objeto'].pk), reverse=True)
    pagina = Paginator(filas, 15).get_page(request.GET.get('pagina'))
    filtros = request.GET.copy(); filtros.pop('pagina', None)
    return render(request, 'gestion/solicitudes_historial.html', {'filas': pagina, 'categoria': categoria, 'consulta': consulta, 'filtros': filtros.urlencode()})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
def solicitud_historial_detalle_view(request, categoria, solicitud_id):
    from .models import SolicitudCongelamiento, SolicitudAnulacionPago
    modelos = {
        'congelamiento': SolicitudCongelamiento.objects.exclude(estado='PENDIENTE').select_related('membresia__socio', 'membresia__plan', 'revisado_por', 'cancelada_por'),
        'asistencia': SolicitudCorreccionAsistencia.objects.exclude(estado='PENDIENTE').select_related('reserva__socio', 'reserva__clase', 'solicitante', 'revisado_por'),
        'avisos': AvisoIndisponibilidad.objects.filter(revisado_en__isnull=False).select_related('clase', 'profesor', 'revisado_por'),
        'pagos': SolicitudAnulacionPago.objects.exclude(estado='PENDIENTE').select_related('pago__membresia__socio', 'pago__membresia__plan', 'solicitante', 'revisado_por'),
    }
    from django.http import Http404
    if categoria not in modelos:
        raise Http404
    objeto = get_object_or_404(modelos[categoria], pk=solicitud_id)
    return render(request, 'gestion/solicitud_historial_detalle.html', {'categoria': categoria, 'solicitud': objeto})



@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
def solicitudes_asistencia_view(request):
    solicitudes = SolicitudCorreccionAsistencia.objects.filter(estado='PENDIENTE').select_related('reserva__socio', 'reserva__clase', 'solicitante', 'revisado_por')
    return render(request, 'gestion/solicitudes_asistencia.html', {'solicitudes': solicitudes})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def resolver_correccion_asistencia_view(request, solicitud_id):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    referencia = get_object_or_404(SolicitudCorreccionAsistencia.objects.select_related('reserva'), pk=solicitud_id)
    clase = get_object_or_404(Clase.all_objects.select_for_update(), pk=referencia.reserva.clase_id)
    solicitud = get_object_or_404(SolicitudCorreccionAsistencia.objects.select_for_update(), pk=solicitud_id)
    reserva = get_object_or_404(Reserva.objects.select_for_update(), pk=solicitud.reserva_id, clase=clase)
    decision = request.POST.get('decision')
    if solicitud.estado != 'PENDIENTE':
        messages.warning(request, 'Esta solicitud ya fue revisada.')
    elif decision not in ('APROBADA', 'RECHAZADA'):
        return HttpResponseForbidden('Decisión no válida.')
    elif decision == 'APROBADA' and (not clase.asistencia_cerrada_en or reserva.estado != 'CONFIRMADA' or reserva.asistencia == solicitud.estado_solicitado):
        messages.warning(request, 'La asistencia cambió o ya no se puede corregir. Revisa la solicitud antes de resolverla.')
    else:
        if decision == 'APROBADA':
            HistorialAsistencia.objects.create(reserva=reserva, estado_anterior=reserva.asistencia, estado_nuevo=solicitud.estado_solicitado, usuario=request.user, origen='ADMINISTRACION')
            reserva.asistencia = solicitud.estado_solicitado
            reserva.asistencia_registrada_por = request.user
            reserva.fecha_asistencia = timezone.now()
            reserva.save(update_fields=['asistencia', 'asistencia_registrada_por', 'fecha_asistencia', 'fecha_actualizacion'])
        solicitud.estado = decision
        solicitud.revisado_por = request.user
        solicitud.fecha_revision = timezone.now()
        solicitud.comentario_revision = request.POST.get('comentario', '').strip()
        solicitud.save(update_fields=['estado', 'revisado_por', 'fecha_revision', 'comentario_revision'])
        texto = 'aprobada' if decision == 'APROBADA' else 'rechazada'
        NotificacionProfesor.objects.get_or_create(
            solicitud=solicitud,
            defaults={'usuario': solicitud.solicitante, 'mensaje': f'Tu solicitud de corrección para {clase.nombre} fue {texto}.'},
        )
        messages.success(request, 'Solicitud revisada.')
    return redirect('solicitudes_asistencia')


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
    from .models import SolicitudCongelamiento
    pausadas_hoy = SolicitudCongelamiento.objects.filter(estado='APROBADA', fecha_inicio__lte=hoy, fecha_fin__gte=hoy).filter(Q(fecha_reactivacion__isnull=True) | Q(fecha_reactivacion__gt=hoy)).values('membresia_id')
    total_membresias_activas = Membresia.objects.filter(activo=True, estado='ACTIVA', fecha_inicio__lte=hoy, fecha_fin__gte=hoy).exclude(pk__in=pausadas_hoy).count()
    clases_hoy = Clase.objects.filter(fecha=hoy, activo=True)
    total_clases_hoy = clases_hoy.count()

    # Ingresos registrados hoy
    ingresos_hoy = Pago.objects.filter(fecha_pago__date=hoy, anulado=False).aggregate(total=Sum('monto'))['total'] or 0

    # 2. Desglose de membresías por plan (para el gráfico donut)
    desglose_planes = Plan.all_objects.annotate(
        total_activas=Count('membresias', filter=Q(membresias__estado='ACTIVA', membresias__activo=True, membresias__fecha_inicio__lte=hoy, membresias__fecha_fin__gte=hoy) & ~Q(membresias__pk__in=pausadas_hoy))
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

    # Se cuentan asistencias efectivas, no reservas, por nombre de clase.
    ranking = list(Reserva.objects.filter(
        estado='CONFIRMADA', asistencia='ASISTIO',
        clase__fecha__range=(hoy - timedelta(days=29), hoy),
    ).values('clase__nombre').annotate(total=Count('id')).order_by('-total', 'clase__nombre')[:5])
    maximo = ranking[0]['total'] if ranking else 1
    for item in ranking:
        item['porcentaje'] = round(item['total'] * 100 / maximo)

    planes = [plan for plan in desglose_planes if plan.total_activas]
    colores = ['#c5fa00', '#a1d72f', '#729d39', '#d4ed9b', '#71816a', '#99b854']
    inicio = 0
    segmentos = []
    for index, plan in enumerate(planes):
        plan.color_grafico = colores[index % len(colores)]
        fin = inicio + (plan.total_activas * 100 / total_membresias_activas if total_membresias_activas else 0)
        segmentos.append(f'{plan.color_grafico} {inicio:.4f}% {fin:.4f}%')
        inicio = fin
    grafico_planes = ', '.join(segmentos) if segmentos else '#39413b 0% 100%'

    from .models import SolicitudAnulacionPago
    contexto = {
        'anulaciones_pendientes': SolicitudAnulacionPago.objects.filter(estado='PENDIENTE').count(),
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
        'planes_grafico': planes,
        'grafico_planes': grafico_planes,
        'ranking_asistencias': ranking,
        'congelamientos_pendientes': SolicitudCongelamiento.objects.filter(estado='PENDIENTE').count(),
        'correcciones_pendientes': SolicitudCorreccionAsistencia.objects.filter(estado='PENDIENTE').count(),
        'avisos_profesores_pendientes': AvisoIndisponibilidad.objects.filter(revisado_en__isnull=True).count(),
        'ultimos_pagos': ultimos_pagos,
        'clases_hoy': clases_con_datos,
    }
    return render(request, 'admin_dashboard.html', contexto)


@login_required
@never_cache
def recepcion_avisos_resumen_view(request):
    if not es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para recepción y administración.')
    avisos = NotificacionRecepcion.objects.filter(Q(destinatario__isnull=True) | Q(destinatario=request.user)).exclude(oculta_por=request.user)
    return JsonResponse({'sin_leer': avisos.exclude(leida_por=request.user).exists(), 'avisos': [{'mensaje': a.mensaje, 'fecha': timezone.localtime(a.creada).strftime('%d/%m/%Y %H:%M'), 'url': a.destino if a.destino and es_administrador(request.user) else (reverse('admin_clase_detalle', args=[a.clase_id]) if a.clase_id and es_administrador(request.user) else (reverse('clases_list') + '?clase=' + str(a.clase_id) if a.clase_id and a.clase.activo else None))} for a in avisos.select_related('clase')[:5]]})


@login_required
@never_cache
def recepcion_notificaciones_view(request):
    if not es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para recepción y administración.')
    avisos = list(NotificacionRecepcion.objects.filter(Q(destinatario__isnull=True) | Q(destinatario=request.user)).exclude(oculta_por=request.user)[:100])
    if not es_administrador(request.user):
        for aviso in avisos:
            aviso.url_recepcion = reverse('clases_list') + '?clase=' + str(aviso.clase_id) if aviso.clase_id and aviso.clase and aviso.clase.activo and aviso.clase.fecha >= timezone.localdate() else ''
    through = NotificacionRecepcion.leida_por.through
    through.objects.bulk_create([through(notificacionrecepcion_id=a.pk, user_id=request.user.pk) for a in avisos], ignore_conflicts=True)
    return render(request, 'recepcion/notificaciones.html', {'avisos': avisos})


@login_required
@never_cache
def recepcion_limpiar_notificaciones_view(request):
    from django.http import HttpResponseNotAllowed
    if not es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Esta sección está disponible para recepción y administración.')
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    avisos = NotificacionRecepcion.objects.filter(Q(destinatario__isnull=True) | Q(destinatario=request.user)).exclude(oculta_por=request.user)
    through = NotificacionRecepcion.oculta_por.through
    through.objects.bulk_create([through(notificacionrecepcion_id=pk, user_id=request.user.pk) for pk in avisos.values_list('pk', flat=True)], ignore_conflicts=True)
    messages.success(request, 'Se limpió tu historial de notificaciones.')
    return redirect('recepcion_notificaciones')


def _buscar_socios_recepcion(consulta):
    from django.db.models import F, Value
    from django.db.models.functions import Concat, Replace
    consulta = consulta.strip()
    if len(consulta) < 2:
        return Socio.objects.none()
    rut_limpio = consulta.replace('.', '').replace('-', '').replace(' ', '')
    socios = Socio.objects.annotate(nombre_completo_busqueda=Concat('nombres', Value(' '), 'apellidos'), rut_busqueda=Replace(Replace(Replace(F('rut'), Value('.'), Value('')), Value('-'), Value('')), Value(' '), Value('')))
    filtro = Q(nombre_completo_busqueda__icontains=consulta)
    if rut_limpio:
        filtro |= Q(rut_busqueda__icontains=rut_limpio)
    return socios.filter(filtro).order_by('apellidos', 'nombres', 'pk')[:10]


@login_required
@never_cache
def recepcion_buscar_socios_view(request):
    if not es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Solo recepción y administración pueden consultar socios.')
    consulta = request.GET.get('q', '').strip()
    if len(consulta) < 2:
        return JsonResponse({'socios': []})
    encontrados = _buscar_socios_recepcion(consulta)
    return JsonResponse({'socios': [{'id': s.pk, 'nombre': s.nombre_completo, 'rut': s.rut} for s in encontrados]})


@login_required
@never_cache
def recepcion_contratar_membresia_view(request):
    from django.core import signing
    if not es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Solo recepción y administración pueden contratar membresías.')
    seleccionado = None
    identificador = request.POST.get('socio', '') if request.method == 'POST' else request.GET.get('socio', '')
    if identificador.isdigit():
        seleccionado = Socio.objects.filter(pk=int(identificador)).first()
    consulta = request.GET.get('q', '').strip()
    resultados = _buscar_socios_recepcion(consulta)
    planes = Plan.objects.all()
    vigente = (seleccionado.membresia_vigente() or seleccionado.membresia_en_pausa()) if seleccionado else None
    inicio_sugerido = (vigente.fecha_fin + timedelta(days=1)) if vigente else timezone.localdate()
    errores = []
    if request.method == 'POST':
        from django.utils.dateparse import parse_date
        try:
            inicio = parse_date(request.POST.get('fecha_inicio', ''))
        except ValueError:
            inicio = None
        plan_id = request.POST.get('plan', '')
        plan = planes.filter(pk=int(plan_id)).first() if plan_id.isdigit() else None
        metodo = request.POST.get('metodo_pago', '')
        rut = request.POST.get('rut', '').strip()
        if not seleccionado: errores.append('Busca y selecciona un socio activo.')
        if not plan: errores.append('Selecciona un plan disponible.')
        if not inicio: errores.append('Ingresa una fecha de inicio válida.')
        if metodo not in dict(Pago.METODOS_PAGO): errores.append('Selecciona un medio de pago.')
        if seleccionado and normalizar_rut(rut) != normalizar_rut(seleccionado.rut): errores.append('El RUT no coincide con el de la ficha del socio.')
        if not errores:
            try:
                fin = _validar_contratacion_socio(seleccionado, plan, inicio)
            except ValidationError as error:
                errores.extend(error.messages)
            else:
                propuesta = _propuesta_contratacion(seleccionado, plan, inicio, metodo)
                propuesta['personal'] = request.user.pk
                token = signing.dumps(propuesta, salt='fitlife.recepcion.contratacion')
                return render(request, 'gestion/membresia_revision.html', {'socio': seleccionado, 'plan': plan, 'inicio': inicio, 'fin': fin, 'metodo': dict(Pago.METODOS_PAGO)[metodo], 'token': token})
    return render(request, 'gestion/membresia_contratar.html', {'socio_seleccionado': seleccionado, 'vigente': vigente, 'inicio_sugerido': inicio_sugerido, 'planes': planes, 'medios_pago': Pago.METODOS_PAGO, 'errores': errores, 'consulta': consulta, 'resultados': resultados})


@login_required
@never_cache
@transaction.atomic
def recepcion_confirmar_membresia_view(request):
    from datetime import date
    from django.core import signing
    from django.http import HttpResponseNotAllowed
    if not es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Solo recepción y administración pueden contratar membresías.')
    if request.method != 'POST': return HttpResponseNotAllowed(['POST'])
    try:
        propuesta = signing.loads(request.POST.get('propuesta', ''), salt='fitlife.recepcion.contratacion', max_age=900)
        if not isinstance(propuesta, dict) or propuesta.get('personal') != request.user.pk or propuesta.get('metodo') not in dict(Pago.METODOS_PAGO): raise ValueError
        socio = Socio.objects.select_for_update().filter(pk=propuesta.get('socio')).first()
        plan = Plan.objects.select_for_update().filter(pk=propuesta.get('plan')).first()
        inicio = date.fromisoformat(propuesta['inicio'])
        if not socio or not plan: raise ValueError
        esperada = _propuesta_contratacion(socio, plan, inicio, propuesta['metodo']); esperada['personal'] = request.user.pk
        if propuesta != esperada: raise ValueError
        fin = _validar_contratacion_socio(socio, plan, inicio)
    except (signing.BadSignature, ValueError, TypeError, KeyError, ValidationError):
        messages.warning(request, 'No se pudo confirmar. Revisa nuevamente los datos y las condiciones del plan.')
        return redirect('recepcion_contratar_membresia')
    membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=inicio, fecha_fin=fin, estado='ACTIVA')
    pago = Pago.objects.create(membresia=membresia, monto=membresia.precio_contratado, metodo_pago=propuesta['metodo'], observaciones='Contratación registrada en recepción.')
    return redirect('recepcion_comprobante_membresia', pago_id=pago.pk)


@login_required
@never_cache
def recepcion_comprobante_membresia_view(request, pago_id):
    if not es_recepcionista_o_admin(request.user):
        return HttpResponseForbidden('Solo recepción y administración pueden consultar el comprobante.')
    pago = get_object_or_404(Pago.objects.select_related('membresia__socio', 'membresia__plan'), pk=pago_id)
    return render(request, 'gestion/membresia_comprobante.html', {'pago': pago})


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def recepcion_dashboard(request):
    """
    Panel de Recepción (enfoque operativo: QR, Aforo en vivo, Próximas Clases, Últimos Accesos).
    """
    if es_administrador(request.user):
        return redirect('admin_dashboard')
    hoy = timezone.localdate()

    # Aforo inicial
    datos_aforo = calcular_aforo_actual()

    # Próximas clases de hoy
    ahora_hora = timezone.localtime().time()
    proximas_clases = Clase.objects.filter(fecha=hoy, activo=True, hora_fin__gte=ahora_hora).select_related('profesor', 'actividad').order_by('hora_inicio')[:5]
    clases_data = []
    for c in proximas_clases:
        clases_data.append({
            'clase': c,
            'imagen_tipo': imagen_tipo_clase(c),
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
    filtro_membresia = request.GET.get('membresia', '') if es_admin else ''
    if es_admin:
        from django.db.models import Case, CharField, Exists, F, IntegerField, OuterRef, Subquery, Value, When
        from .models import SolicitudCongelamiento
        hoy = timezone.localdate()
        pausadas = SolicitudCongelamiento.objects.filter(estado='APROBADA', fecha_inicio__lte=hoy, fecha_fin__gte=hoy).filter(Q(fecha_reactivacion__isnull=True) | Q(fecha_reactivacion__gt=hoy)).values('membresia_id')
        base = Membresia.objects.filter(socio_id=OuterRef('pk'))
        vigente = Q(estado='ACTIVA', fecha_inicio__lte=hoy, fecha_fin__gte=hoy) & ~Q(pk__in=pausadas)
        congelada = Q(fecha_inicio__lte=hoy, fecha_fin__gte=hoy) & (Q(estado='CONGELADA') | Q(estado='ACTIVA', pk__in=pausadas))
        programada = Q(estado='ACTIVA', fecha_inicio__gt=hoy)
        vencida = Q(fecha_fin__lt=hoy) | Q(estado='VENCIDA')
        prioridad = Case(When(vigente, then=Value(0)), When(congelada, then=Value(1)), When(programada, then=Value(2)), When(vencida, then=Value(3)), default=Value(4), output_field=IntegerField())
        elegidas = base.annotate(prioridad=prioridad).filter(prioridad__lt=4).order_by('prioridad', '-fecha_fin', '-pk')
        socios = socios.annotate(membresia_prioridad=Subquery(elegidas.values('prioridad')[:1]), membresia_nombre=Subquery(elegidas.values('plan__nombre')[:1]), membresia_fin=Subquery(elegidas.values('fecha_fin')[:1]))
        if filtro_membresia in ('ACTIVA', 'CONGELADA', 'PROGRAMADA', 'VENCIDA'):
            socios = socios.filter(membresia_prioridad={'ACTIVA': 0, 'CONGELADA': 1, 'PROGRAMADA': 2, 'VENCIDA': 3}[filtro_membresia])
        elif filtro_membresia == 'SIN_MEMBRESIA':
            socios = socios.filter(membresia_prioridad__isnull=True)
        else:
            filtro_membresia = ''
    if query:
        from django.db.models import Value
        from django.db.models.functions import Concat, Replace
        socios = socios.annotate(nombre_busqueda=Concat('nombres', Value(' '), 'apellidos'), rut_busqueda=Replace(Replace(Replace('rut', Value('.'), Value('')), Value('-'), Value('')), Value(' '), Value('')))
        rut_limpio = query.replace('.', '').replace('-', '').replace(' ', '')
        filtro = Q(nombre_busqueda__icontains=query) | Q(email__icontains=query) | Q(codigo_qr__icontains=query)
        if rut_limpio:
            filtro |= Q(rut_busqueda__icontains=rut_limpio)
        socios = socios.filter(filtro)

    contexto = {
        'socios': socios,
        'query': query,
        'es_admin': es_admin,
        'ver_inactivos': ver_inactivos,
        'filtro_membresia': filtro_membresia,
        'socio_creado': Socio.objects.filter(pk=request.session.pop('socio_recien_creado', None)).first(),
    }
    return render(request, 'socios/list.html' if es_admin else 'socios/list_recepcion.html', contexto)


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
@never_cache
def socio_ficha_view(request, socio_id):
    es_admin = es_administrador(request.user)
    socios = Socio.all_objects if es_admin else Socio.objects
    socio = get_object_or_404(socios, pk=socio_id)
    membresias = list(Membresia.all_objects.filter(socio=socio).select_related('plan').prefetch_related('pagos').order_by('-fecha_inicio', '-pk'))
    for membresia in membresias:
        membresia.sin_registros_pago = not bool(membresia.pagos.all())
    from django.db.models import Exists, OuterRef
    from .models import SolicitudAnulacionPago
    pendientes = SolicitudAnulacionPago.objects.filter(pago_id=OuterRef('pk'), estado='PENDIENTE')
    pagos = Pago.objects.filter(membresia__socio=socio).select_related('membresia__plan').annotate(anulacion_pendiente=Exists(pendientes)).order_by('-fecha_pago', '-pk')
    from .models import SolicitudCongelamiento
    solicitudes_congelamiento = SolicitudCongelamiento.objects.filter(membresia__socio=socio, solicitante=request.user).select_related('membresia__plan') if not es_admin else []
    solicitudes_pago = SolicitudAnulacionPago.objects.filter(pago__membresia__socio=socio, solicitante=request.user).select_related('pago__membresia__plan') if not es_admin else []
    return render(request, 'socios/ficha.html' if es_admin else 'socios/ficha_recepcion.html', {'socio': socio, 'membresias': membresias, 'pagos': pagos, 'es_admin': es_admin, 'solicitudes_congelamiento_personal': solicitudes_congelamiento, 'solicitudes_pago_personal': solicitudes_pago})


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
@never_cache
def recepcion_pago_detalle_view(request, socio_id, pago_id):
    socio = get_object_or_404(Socio.objects, pk=socio_id)
    pago = get_object_or_404(Pago.objects.select_related('membresia__plan'), pk=pago_id, membresia__socio=socio)
    from .models import SolicitudAnulacionPago
    solicitudes = SolicitudAnulacionPago.objects.filter(pago=pago).select_related('solicitante', 'revisado_por').order_by('-creada', '-pk')
    return render(request, 'socios/pago_detalle_recepcion.html', {'socio': socio, 'pago': pago, 'solicitudes': solicitudes, 'anulacion_pendiente': solicitudes.filter(estado='PENDIENTE').exists()})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@never_cache
def socio_membresia_detalle_view(request, socio_id, membresia_id):
    socio = get_object_or_404(Socio.all_objects, pk=socio_id)
    membresia = get_object_or_404(Membresia.all_objects.select_related('plan', 'socio'), pk=membresia_id, socio=socio)
    pagos = membresia.pagos.order_by('-fecha_pago', '-pk')
    return render(request, 'socios/membresia_detalle.html', {'socio': socio, 'membresia': membresia, 'pagos': pagos})


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
@never_cache
@transaction.atomic
def socio_registrar_pago_pendiente_view(request, socio_id, membresia_id):
    from django import forms
    socio = get_object_or_404(Socio.objects, pk=socio_id)
    membresia = get_object_or_404(Membresia.objects.select_for_update().select_related('plan'), pk=membresia_id, socio=socio)
    if membresia.pagos.exists():
        messages.warning(request, 'Esta membresía ya tiene movimientos de pago. Revisa su historial o contacta a administración.')
        return redirect('socio_ficha', socio_id=socio.pk)
    form = PagoForm(request.POST or None, initial={'membresia': membresia.pk})
    form.fields['membresia'].queryset = Membresia.objects.filter(pk=membresia.pk)
    form.fields['membresia'].widget = forms.HiddenInput()
    if membresia.precio_contratado is not None:
        form.fields['monto'].widget.attrs['readonly'] = True
        form.initial['monto'] = membresia.precio_contratado
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'El pago quedó registrado en la ficha del socio.')
        return redirect('socio_ficha', socio_id=socio.pk)
    return render(request, 'socios/pago_pendiente.html', {'socio': socio, 'membresia': membresia, 'form': form})


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
@never_cache
@transaction.atomic
def renovar_codigo_acceso_view(request, socio_id):
    socio = get_object_or_404(Socio.objects.select_for_update(), pk=socio_id)
    if request.method == 'POST':
        if request.POST.get('identidad_verificada') != 'si':
            messages.warning(request, 'Confirma que verificaste la identidad antes de generar un nuevo código.')
        else:
            nuevo_codigo = 'FL-' + secrets.token_hex(12).upper()
            while Socio.all_objects.filter(codigo_qr=nuevo_codigo).exists():
                nuevo_codigo = 'FL-' + secrets.token_hex(12).upper()
            socio.codigo_qr = nuevo_codigo
            socio.save(update_fields=['codigo_qr'])
            CambioCodigoAcceso.objects.create(socio=socio, realizado_por=request.user)
            messages.success(request, 'Código actualizado. El socio puede consultar el nuevo en su cuenta.')
            return redirect('socios_list')
    cambios = socio.cambios_codigo_acceso.select_related('realizado_por')[:10]
    return render(request, 'socios/renovar_codigo.html', {'socio': socio, 'cambios': cambios})


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def socio_form_view(request, socio_id=None):
    socio = get_object_or_404(Socio.objects, pk=socio_id) if socio_id else None
    form = SocioForm(request.POST or None, instance=socio)
    if request.method == 'POST' and form.is_valid():
        guardado = form.save()
        if socio is None:
            request.session['socio_recien_creado'] = guardado.pk
        messages.success(request, 'Socio guardado correctamente.')
        return redirect('socios_list')
    return render(request, 'socios/form.html', {'form': form, 'socio': socio})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
def ingresos_admin_view(request):
    from datetime import date, timedelta
    from django.core.paginator import Paginator
    from django.db.models.functions import TruncDate
    hoy = timezone.localdate()
    periodo = request.GET.get('periodo', 'mes')
    if periodo not in ('mes', '30dias', 'ano', 'personalizado'):
        periodo = 'mes'
    inicio, fin = hoy.replace(day=1), hoy
    error = ''
    if periodo == '30dias':
        inicio = hoy - timedelta(days=29)
    elif periodo == 'ano':
        inicio = hoy.replace(month=1, day=1)
    elif periodo == 'personalizado':
        try:
            inicio = date.fromisoformat(request.GET.get('desde', ''))
            fin = date.fromisoformat(request.GET.get('hasta', ''))
            if fin < inicio or (fin - inicio).days > 365:
                raise ValueError
        except (ValueError, TypeError):
            inicio, fin = hoy.replace(day=1), hoy
            error = 'Selecciona un rango válido de hasta 366 días.'
    pagos = Pago.objects.filter(fecha_pago__date__gte=inicio, fecha_pago__date__lte=fin).select_related('membresia__socio', 'membresia__plan')
    plan = request.GET.get('plan', '')
    metodo = request.GET.get('metodo', '')
    if plan.isdigit():
        pagos = pagos.filter(membresia__plan_id=int(plan))
    else:
        plan = ''
    if metodo in dict(Pago.METODOS_PAGO):
        pagos = pagos.filter(metodo_pago=metodo)
    else:
        metodo = ''
    vigentes = pagos.filter(anulado=False)
    anulados = pagos.filter(anulado=True)
    total = vigentes.aggregate(total=Sum('monto'))['total'] or 0
    monto_anulado = anulados.aggregate(total=Sum('monto'))['total'] or 0
    por_dia = {row['dia']: int(row['total']) for row in vigentes.annotate(dia=TruncDate('fecha_pago')).values('dia').annotate(total=Sum('monto'))}
    dias = (fin - inicio).days + 1
    valores = [por_dia.get(inicio + timedelta(days=i), 0) for i in range(dias)]
    maximo = max(valores, default=0)
    import math
    def escala_redondeada(valor):
        if valor <= 0:
            return 0
        magnitud = 10 ** math.floor(math.log10(valor))
        paso = magnitud / 2
        return int(math.ceil(valor / paso) * paso)
    escala_diaria = escala_redondeada(maximo)
    puntos, puntos_detalle = [], []
    for i, valor in enumerate(valores):
        x = 115 + (i * 645 / max(dias - 1, 1)) if dias > 1 else 437.5
        y = 185 - (valor * 150 / max(escala_diaria, 1))
        puntos.append(f'{x:.2f},{y:.2f}')
        if valor:
            puntos_detalle.append({'x': f'{x:.2f}', 'y': f'{y:.2f}', 'valor': valor, 'fecha': inicio + timedelta(days=i)})
    if dias == 1:
        puntos.insert(0, f'115.00,{y:.2f}')
        puntos.append(f'760.00,{y:.2f}')
    etiquetas = []
    for i in sorted({0, (dias-1)//4, (dias-1)//2, (dias-1)*3//4, dias-1}):
        etiquetas.append({'x': 115 + i * 645 / max(dias - 1, 1) if dias > 1 else 437.5, 'fecha': inicio + timedelta(days=i)})
    ejes_diarios = [{'y': 185 - i * 37.5, 'valor': round(escala_diaria * i / 4)} for i in range(5)]
    planes = list(vigentes.values('membresia__plan__nombre').annotate(total=Sum('monto')).order_by('-total', 'membresia__plan__nombre'))
    mayor_plan = max((row['total'] for row in planes), default=0)
    escala_planes = escala_redondeada(int(mayor_plan))
    for row in planes:
        row['porcentaje'] = round(row['total'] * 100 / escala_planes, 2) if escala_planes else 0
    ejes_planes = [round(escala_planes * i / 4) for i in range(5)]
    movimientos = Paginator(pagos.order_by('-fecha_pago', '-pk'), 20).get_page(request.GET.get('pagina'))
    filtros_paginacion = request.GET.copy()
    filtros_paginacion.pop('pagina', None)
    return render(request, 'gestion/pagos_lista_admin.html', {
        'periodo': periodo, 'desde': inicio, 'hasta': fin, 'error_filtros': error,
        'plan_seleccionado': plan, 'metodo_seleccionado': metodo,
        'planes_filtro': Plan.all_objects.order_by('nombre'), 'metodos': Pago.METODOS_PAGO,
        'total': total, 'cantidad': vigentes.count(), 'cantidad_anulados': anulados.count(),
        'monto_anulado': monto_anulado, 'puntos': ' '.join(puntos), 'maximo': maximo,
        'etiquetas': etiquetas, 'ejes_diarios': ejes_diarios, 'puntos_detalle': puntos_detalle,
        'ejes_planes': ejes_planes, 'planes_ingresos': planes, 'movimientos': movimientos,
        'filtros_paginacion': filtros_paginacion.urlencode(),
    })


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@never_cache
def gimnasio_ficha_view(request, modulo, objeto_id):
    from django.http import Http404
    from .horarios_fijos import asegurar_horarios
    modelos = {'profesores': Profesor, 'salas': Sala, 'actividades': Actividad}
    if modulo not in modelos:
        raise Http404
    objeto = get_object_or_404(modelos[modulo].all_objects, pk=objeto_id)
    asegurar_horarios()
    filtros = {'profesores': {'profesor': objeto}, 'salas': {'sala_asignada': objeto}, 'actividades': {'actividad': objeto}}
    clases = Clase.objects.filter(fecha__gte=timezone.localdate(), **filtros[modulo]).select_related('profesor', 'sala_asignada').order_by('fecha', 'hora_inicio')
    proximas = [clase for clase in clases if inicio_clase_local(clase) > timezone.now()][:15]
    return render(request, 'gestion/gym_ficha.html', {'modulo': modulo, 'objeto': objeto, 'proximas': proximas})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@never_cache
def recepcionista_ficha_view(request, usuario_id):
    usuario = get_object_or_404(get_user_model().objects.filter(groups__name='Recepcion').select_related('activacion_recepcion', 'perfil_personal').distinct(), pk=usuario_id)
    editable = _cuentas_recepcion_editables().filter(pk=usuario.pk).exists()
    return render(request, 'recepcion/ficha_personal.html', {'persona': usuario, 'editable': editable})


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def gestion_list_view(request, modulo):
    if modulo == 'pagos':
        return ingresos_admin_view(request) if es_administrador(request.user) else redirect('socios_list')
    ver_inactivos = es_administrador(request.user) and request.GET.get('estado') == 'inactivos'
    if modulo == 'planes':
        objetos, titulo = (Plan.all_objects.filter(activo=False) if ver_inactivos else Plan.objects.all()), 'Planes'
    elif modulo == 'membresias':
        objetos, titulo = (Membresia.all_objects.filter(activo=False) if ver_inactivos else Membresia.objects.all()).select_related('socio', 'plan'), 'Membresías'
        busqueda = request.GET.get('q', '').strip()
        plan = request.GET.get('plan', '')
        estado = request.GET.get('estado_m', '')
        if busqueda:
            from django.db.models import Value
            from django.db.models.functions import Concat, Replace
            objetos = objetos.annotate(nombre_socio_busqueda=Concat('socio__nombres', Value(' '), 'socio__apellidos'), rut_socio_busqueda=Replace(Replace(Replace('socio__rut', Value('.'), Value('')), Value('-'), Value('')), Value(' '), Value('')))
            rut_limpio = busqueda.replace('.', '').replace('-', '').replace(' ', '')
            filtro_busqueda = Q(nombre_socio_busqueda__icontains=busqueda)
            if rut_limpio:
                filtro_busqueda |= Q(rut_socio_busqueda__icontains=rut_limpio)
            objetos = objetos.filter(filtro_busqueda)
        if plan.isdigit():
            objetos = objetos.filter(plan_id=int(plan))
        hoy = timezone.localdate()
        from .models import SolicitudCongelamiento
        pausadas_hoy = SolicitudCongelamiento.objects.filter(estado='APROBADA', fecha_inicio__lte=hoy, fecha_fin__gte=hoy).filter(Q(fecha_reactivacion__isnull=True) | Q(fecha_reactivacion__gt=hoy)).values('membresia_id')
        if estado == 'ACTIVA':
            objetos = objetos.filter(estado='ACTIVA', fecha_inicio__lte=hoy, fecha_fin__gte=hoy).exclude(pk__in=pausadas_hoy)
        elif estado == 'PROGRAMADA':
            objetos = objetos.filter(estado='ACTIVA', fecha_inicio__gt=hoy)
        elif estado == 'VENCIDA':
            objetos = objetos.filter(Q(estado='VENCIDA') | (Q(fecha_fin__lt=hoy) & ~Q(estado='CONGELADA')))
        elif estado == 'CONGELADA':
            objetos = objetos.filter(Q(estado='CONGELADA') | Q(estado='ACTIVA', fecha_inicio__lte=hoy, fecha_fin__gte=hoy, pk__in=pausadas_hoy))
    elif modulo == 'pagos':
        objetos, titulo = Pago.objects.select_related('membresia__socio', 'membresia__plan'), 'Pagos'
    elif modulo == 'actividades':
        if not es_administrador(request.user):
            return HttpResponseForbidden('Solo administración puede gestionar las actividades.')
        objetos, titulo = (Actividad.all_objects.filter(activo=False) if ver_inactivos else Actividad.objects.all()), 'Tipos de clase'
    elif modulo == 'salas':
        if not es_administrador(request.user):
            return HttpResponseForbidden('Solo administración puede gestionar las salas.')
        objetos, titulo = (Sala.all_objects.filter(activo=False) if ver_inactivos else Sala.objects.all()), 'Salas'
    elif modulo == 'profesores':
        objetos, titulo = (Profesor.all_objects.filter(activo=False) if ver_inactivos else Profesor.objects.all()), 'Profesores'
    else:
        from django.http import Http404
        raise Http404
    plantilla = ('gestion/membresias_lista.html' if es_administrador(request.user) else 'gestion/membresias_recepcion.html') if modulo == 'membresias' else ('gestion/pagos_lista_admin.html' if modulo == 'pagos' and es_administrador(request.user) else ('gestion/planes_lista.html' if modulo == 'planes' and es_administrador(request.user) else ('gestion/gym_list.html' if modulo in ('profesores', 'salas', 'actividades') and es_administrador(request.user) else 'gestion/list.html')))
    return render(request, plantilla, {'objetos': objetos, 'modulo': modulo, 'titulo': titulo, 'es_admin': es_administrador(request.user), 'ver_inactivos': ver_inactivos, 'planes_filtro': Plan.objects.all() if modulo == 'membresias' else []})


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
@never_cache
def recepcion_membresia_ficha_view(request, membresia_id):
    membresia = get_object_or_404(Membresia.objects.select_related('socio', 'plan'), pk=membresia_id)
    pagos = membresia.pagos.order_by('-fecha_pago', '-pk')
    tiene_solicitud_pendiente = membresia.solicitudes_congelamiento.filter(estado='PENDIENTE').exists()
    return render(request, 'gestion/membresia_ficha_recepcion.html', {
        'membresia': membresia, 'pagos': pagos, 'tiene_solicitud_pendiente': tiene_solicitud_pendiente,
    })


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def cancelar_clase_view(request, clase_id):
    clase = get_object_or_404(Clase.all_objects.select_for_update(), pk=clase_id)
    reservas = Reserva.objects.filter(clase=clase, estado__in=['CONFIRMADA', 'LISTA_ESPERA']).select_related('socio')
    if request.method == 'POST':
        if not clase.activo or inicio_clase_local(clase) <= timezone.now():
            messages.warning(request, 'Solo se pueden cancelar clases activas que aún no comienzan.')
            return redirect('clases_list')
        for reserva in reservas.select_for_update():
            reserva.estado = 'CANCELADA'
            reserva.consume_clase = False
            reserva.save(update_fields=['estado', 'consume_clase', 'fecha_actualizacion'])
            NotificacionSocio.objects.get_or_create(reserva=reserva, tipo='CLASE_CANCELADA', defaults={'socio': reserva.socio, 'mensaje': f'El gimnasio canceló la clase {clase.nombre} del {clase.fecha:%d/%m/%Y}. No se descontará una clase por esta reserva.'})
        clase.soft_delete()
        AvisoIndisponibilidad.objects.filter(clase=clase, revisado_en__isnull=True).update(revisado_en=timezone.now(), revisado_por=request.user, resolucion='Clase cancelada')
        NotificacionRecepcion.objects.create(clase=clase, mensaje=f'Administración canceló la clase {clase.nombre} del {clase.fecha:%d/%m/%Y} a las {clase.hora_inicio:%H:%M}.')
        if clase.profesor.usuario_id:
            NotificacionProfesor.objects.create(usuario=clase.profesor.usuario, clase=clase, mensaje=f'Administración canceló la clase {clase.nombre} del {clase.fecha:%d/%m/%Y} a las {clase.hora_inicio:%H:%M}.')
        messages.success(request, 'Clase cancelada. Se conservaron las reservas en el historial y se registraron los avisos.')
        return redirect('clases_list')
    return render(request, 'gestion/cancelar_clase.html', {'clase': clase, 'reservas': reservas})


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
@transaction.atomic
def gestion_estado_view(request, modulo, objeto_id, accion):
    from django.http import Http404
    modelos = {'planes': Plan, 'membresias': Membresia, 'profesores': Profesor, 'clases': Clase, 'salas': Sala, 'actividades': Actividad}
    if modulo not in modelos or accion not in ('desactivar', 'restaurar'):
        raise Http404
    objetos = modelos[modulo].all_objects
    if request.method == 'POST' and modulo in ('clases', 'salas', 'profesores', 'actividades'):
        objetos = objetos.select_for_update()
    objeto = get_object_or_404(objetos, pk=objeto_id)
    destino = 'clases_list' if modulo == 'clases' else 'gestion_list'
    destino_kwargs = {} if modulo == 'clases' else {'modulo': modulo}
    if request.method == 'POST':
        if accion == 'desactivar':
            if modulo == 'actividades' and Clase.objects.filter(actividad=objeto, fecha__gte=timezone.localdate()).exists():
                messages.error(request, 'No se puede desactivar la actividad: tiene clases programadas.')
            elif modulo == 'salas' and Clase.objects.filter(sala_asignada=objeto, fecha__gte=timezone.localdate()).exists():
                messages.error(request, 'No se puede desactivar la sala: tiene clases programadas.')
            elif modulo == 'profesores' and Clase.objects.filter(profesor=objeto, fecha__gte=timezone.localdate()).exists():
                messages.error(request, 'No se puede desactivar al profesor: tiene clases programadas.')
            elif modulo == 'clases' and Reserva.objects.filter(clase=objeto, estado__in=['CONFIRMADA', 'LISTA_ESPERA']).exists():
                messages.error(request, 'No se puede desactivar la clase mientras tenga reservas o lista de espera.')
            elif modulo == 'membresias' and Reserva.objects.filter(socio=objeto.socio, clase__fecha__gte=timezone.localdate(), estado__in=['CONFIRMADA', 'LISTA_ESPERA']).exists():
                messages.error(request, 'El socio tiene reservas próximas. Cancélalas antes de desactivar la membresía.')
            else:
                estaba_activo = objeto.activo
                objeto.soft_delete()
                if modulo == 'clases' and estaba_activo and inicio_clase_local(objeto) > timezone.now():
                    NotificacionRecepcion.objects.create(clase=objeto, mensaje=f'La clase {objeto.nombre} del {objeto.fecha:%d/%m/%Y} a las {objeto.hora_inicio:%H:%M} fue desactivada.')
                if modulo == 'clases' and estaba_activo and inicio_clase_local(objeto) > timezone.now() and objeto.profesor.usuario_id:
                    NotificacionProfesor.objects.create(usuario=objeto.profesor.usuario, clase=objeto, mensaje=f'La clase {objeto.nombre} del {objeto.fecha:%d/%m/%Y} a las {objeto.hora_inicio:%H:%M} fue desactivada por administración.')
                messages.success(request, 'Registro desactivado; se conserva su historial.')
        else:
            estaba_inactivo = not objeto.activo
            if modulo == 'clases' and estaba_inactivo:
                # Bloquear los mismos recursos usados al programar una clase evita
                # restauraciones simultáneas con horarios incompatibles.
                actividad = Actividad.all_objects.select_for_update().filter(pk=objeto.actividad_id).first() if objeto.actividad_id else None
                sala = Sala.all_objects.select_for_update().filter(pk=objeto.sala_asignada_id).first() if objeto.sala_asignada_id else None
                profesor = Profesor.all_objects.select_for_update().get(pk=objeto.profesor_id)
                cruces = Clase.objects.filter(fecha=objeto.fecha, hora_inicio__lt=objeto.hora_fin, hora_fin__gt=objeto.hora_inicio).exclude(pk=objeto.pk)
                sala_ocupada = cruces.filter(Q(sala_asignada=sala) | Q(sala_asignada__isnull=True, sala__iexact=sala.nombre)).exists() if sala else cruces.filter(Q(sala__iexact=objeto.sala) | Q(sala_asignada__nombre__iexact=objeto.sala)).exists()
                if not profesor.activo or (sala and not sala.activo) or (actividad and not actividad.activo):
                    messages.error(request, 'No se puede restaurar: el profesor, la sala o la actividad están desactivados.')
                    return redirect(destino, **destino_kwargs)
                if cruces.filter(profesor=profesor).exists() or sala_ocupada:
                    messages.error(request, 'No se puede restaurar: el profesor o la sala tienen otra clase en ese horario.')
                    return redirect(destino, **destino_kwargs)
            objeto.restore()
            if modulo == 'clases' and estaba_inactivo and inicio_clase_local(objeto) > timezone.now():
                NotificacionRecepcion.objects.create(clase=objeto, mensaje=f'La clase {objeto.nombre} del {objeto.fecha:%d/%m/%Y} a las {objeto.hora_inicio:%H:%M} volvió a estar activa.')
            if modulo == 'clases' and estaba_inactivo and inicio_clase_local(objeto) > timezone.now() and objeto.profesor.usuario_id:
                NotificacionProfesor.objects.create(usuario=objeto.profesor.usuario, clase=objeto, mensaje=f'La clase {objeto.nombre} del {objeto.fecha:%d/%m/%Y} a las {objeto.hora_inicio:%H:%M} volvió a estar activa.')
            messages.success(request, 'Registro restaurado.')
        if modulo == 'membresias':
            return redirect('socio_membresia_detalle', socio_id=objeto.socio_id, membresia_id=objeto.pk)
        return redirect(destino, **destino_kwargs)
    plantilla = 'gestion/membresia_estado.html' if modulo == 'membresias' else ('gestion/clases_estado_admin.html' if modulo == 'clases' else ('gestion/planes_estado.html' if modulo == 'planes' else ('gestion/gym_estado.html' if modulo in ('profesores', 'salas', 'actividades') else 'gestion/estado.html')))
    return render(request, plantilla, {'objeto': objeto, 'accion': accion, 'modulo': modulo})


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
@transaction.atomic
def gestion_form_view(request, modulo, objeto_id=None):
    opciones = {'planes': (Plan, PlanForm, 'plan'), 'membresias': (Membresia, MembresiaForm, 'membresía'), 'pagos': (Pago, PagoForm, 'pago'), 'profesores': (Profesor, ProfesorForm, 'profesor'), 'clases': (Clase, ClaseForm, 'clase'), 'salas': (Sala, SalaForm, 'sala'), 'actividades': (Actividad, ActividadForm, 'tipo de clase')}
    if modulo == 'membresias' and not es_administrador(request.user):
        if objeto_id is None and request.method == 'GET':
            return redirect('recepcion_contratar_membresia')
        return HttpResponseForbidden('Las modificaciones de membresías las realiza administración.')
    if modulo == 'membresias' and objeto_id is None and request.method == 'GET':
        return redirect('recepcion_contratar_membresia')
    if modulo == 'pagos' and not es_administrador(request.user):
        return HttpResponseForbidden('Para registrar un pago pendiente, entra a la ficha del socio.')
    if modulo not in opciones:
        from django.http import Http404
        raise Http404
    if modulo in ('planes', 'profesores', 'clases', 'salas', 'actividades') and not es_administrador(request.user):
        return HttpResponseForbidden('Solo administración puede modificar este registro.')
    if modulo == 'pagos' and objeto_id is not None:
        return HttpResponseForbidden('Los pagos registrados no se editan desde esta pantalla.')
    modelo, formulario, titulo = opciones[modulo]
    objetos = modelo.objects
    if modulo in ('clases', 'salas', 'profesores', 'actividades') and objeto_id and request.method == 'POST':
        objetos = objetos.select_for_update()
    objeto = get_object_or_404(objetos, pk=objeto_id) if objeto_id else None
    if modulo == 'membresias' and not objeto_id and request.method == 'POST':
        socio_id = request.POST.get('socio', '')
        if socio_id.isdigit():
            Socio.all_objects.select_for_update().filter(pk=int(socio_id)).first()
    if modulo == 'clases' and request.method == 'POST':
        # Bloquear recursos antes de validar evita programaciones simultáneas incompatibles.
        actividad_id = request.POST.get('actividad', '')
        sala_id = request.POST.get('sala_asignada', '')
        profesor_id = request.POST.get('profesor', '')
        if actividad_id.isdigit():
            Actividad.all_objects.select_for_update().filter(pk=int(actividad_id)).first()
        if sala_id.isdigit():
            Sala.all_objects.select_for_update().filter(pk=int(sala_id)).first()
        if profesor_id.isdigit():
            Profesor.all_objects.select_for_update().filter(pk=int(profesor_id)).first()
    clase_anterior = None
    if modulo == 'clases' and objeto:
        clase_anterior = {'profesor_id': objeto.profesor_id, 'fecha': objeto.fecha, 'hora_inicio': objeto.hora_inicio, 'hora_fin': objeto.hora_fin, 'sala_id': objeto.sala_asignada_id, 'sala': objeto.sala, 'nombre': objeto.nombre, 'actividad_id': objeto.actividad_id, 'capacidad': objeto.capacidad_maxima}
    form = formulario(request.POST or None, instance=objeto)
    if request.method == 'POST' and form.is_valid():
        guardado = form.save()
        if modulo == 'clases' and objeto and guardado.programacion_id and form.has_changed():
            guardado.excepcion_programacion = True
            guardado.save(update_fields=['excepcion_programacion'])
        if modulo == 'clases' and inicio_clase_local(guardado) > timezone.now():
            if clase_anterior and any((clase_anterior['profesor_id'] != guardado.profesor_id, clase_anterior['fecha'] != guardado.fecha, clase_anterior['hora_inicio'] != guardado.hora_inicio, clase_anterior['hora_fin'] != guardado.hora_fin, clase_anterior['sala_id'] != guardado.sala_asignada_id, clase_anterior['sala'] != guardado.sala, clase_anterior['nombre'] != guardado.nombre, clase_anterior['actividad_id'] != guardado.actividad_id, clase_anterior['capacidad'] != guardado.capacidad_maxima)):
                NotificacionRecepcion.objects.create(clase=guardado, mensaje=f'Se actualizaron los datos de la clase {guardado.nombre}: {guardado.fecha:%d/%m/%Y} a las {guardado.hora_inicio:%H:%M}. Revisa la programación.')
            profesor_actual = guardado.profesor
            if clase_anterior is None or clase_anterior['profesor_id'] != guardado.profesor_id:
                if clase_anterior:
                    anterior = Profesor.all_objects.get(pk=clase_anterior['profesor_id'])
                    if anterior.usuario_id:
                        NotificacionProfesor.objects.create(usuario=anterior.usuario, clase=guardado, mensaje=f'Ya no estás asignado a la clase {guardado.nombre} del {guardado.fecha:%d/%m/%Y}.')
                if profesor_actual.usuario_id:
                    NotificacionProfesor.objects.create(usuario=profesor_actual.usuario, clase=guardado, mensaje=f'Te asignaron la clase {guardado.nombre} del {guardado.fecha:%d/%m/%Y} a las {guardado.hora_inicio:%H:%M}.')
            elif profesor_actual.usuario_id and any((clase_anterior['fecha'] != guardado.fecha, clase_anterior['hora_inicio'] != guardado.hora_inicio, clase_anterior['hora_fin'] != guardado.hora_fin, clase_anterior['sala_id'] != guardado.sala_asignada_id)):
                NotificacionProfesor.objects.create(usuario=profesor_actual.usuario, clase=guardado, mensaje=f'Cambió el horario o la sala de tu clase {guardado.nombre}. Revisa sus detalles actualizados.')
        messages.success(request, f'{titulo.capitalize()} guardado correctamente.')
        if modulo == 'clases':
            return redirect('clases_list')
        if modulo == 'membresias' and objeto and es_administrador(request.user):
            return redirect('socio_membresia_detalle', socio_id=guardado.socio_id, membresia_id=guardado.pk)
        return redirect('gestion_list', modulo=modulo)
    plantilla = 'gestion/membresia_editar.html' if modulo == 'membresias' and objeto else ('gestion/clases_form_admin.html' if modulo == 'clases' else ('gestion/pagos_form_admin.html' if modulo == 'pagos' else ('gestion/planes_form.html' if modulo == 'planes' else ('gestion/gym_form.html' if modulo in ('profesores', 'salas', 'actividades') else 'gestion/form.html'))))
    contexto = {'form': form, 'titulo': titulo, 'modulo': modulo, 'objeto': objeto}
    if modulo == 'clases' and (not objeto or not objeto.programacion_id or objeto.programacion.activa):
        from .vistas_horario import form_inicial
        contexto['horario_form'] = form_inicial(objeto.programacion if objeto and objeto.programacion_id else None, objeto)
    return render(request, plantilla, contexto)


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
def anular_pago_view(request, pago_id):
    messages.info(request, 'Las anulaciones se gestionan desde las solicitudes de pago.')
    return redirect('solicitudes_anulacion_pago')


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
    from .horarios_fijos import asegurar_horarios
    asegurar_horarios()
    """Listado de clases con reserva y lista de espera."""
    cerrar_listas_iniciadas()
    hoy = timezone.localdate()
    es_admin = es_administrador(request.user)
    ver_inactivas = es_admin and request.GET.get('estado') == 'inactivas'
    from django.db.models import Prefetch
    reservas_visibles = Reserva.objects.filter(estado__in=['CONFIRMADA', 'LISTA_ESPERA']).select_related('socio').order_by('fecha_reserva', 'pk')
    clases = (Clase.all_objects.filter(activo=False) if ver_inactivas else Clase.objects.filter(fecha__gte=hoy)).select_related('profesor', 'actividad', 'sala_asignada').prefetch_related(Prefetch('reservas', queryset=reservas_visibles, to_attr='reservas_portal')).order_by('fecha', 'hora_inicio')
    clase_nombre = request.GET.get('clase_q', '').strip() if not es_admin else ''
    clase_fecha = request.GET.get('fecha', '') if not es_admin else ''
    if clase_nombre:
        clases = clases.filter(Q(nombre__icontains=clase_nombre) | Q(actividad__nombre__icontains=clase_nombre) | Q(profesor__nombres__icontains=clase_nombre) | Q(profesor__apellidos__icontains=clase_nombre))
    if clase_fecha:
        from django.utils.dateparse import parse_date
        try:
            fecha_filtrada = parse_date(clase_fecha)
        except ValueError:
            fecha_filtrada = None
        if fecha_filtrada:
            clases = clases.filter(fecha=fecha_filtrada)
        else:
            clase_fecha = ''
    consulta = request.GET.get('q', '').strip()
    clase_buscada = request.GET.get('clase', '')
    resultados = _buscar_socios_recepcion(consulta) if len(consulta) >= 2 and clase_buscada.isdigit() else Socio.objects.none()
    filas = []
    for clase in clases:
        confirmadas = [r for r in clase.reservas_portal if r.estado == 'CONFIRMADA']
        if not es_admin:
            ahora = timezone.now()
            inicio = inicio_clase_local(clase)
            for reserva in confirmadas:
                reserva.puede_confirmar_recepcion = False
                if reserva.asistencia == 'PENDIENTE' and not clase.asistencia_cerrada_en:
                    if ahora < inicio - timedelta(minutes=30):
                        reserva.motivo_asistencia = 'Disponible desde 30 minutos antes de la clase.'
                    elif ahora > inicio + timedelta(minutes=5):
                        reserva.motivo_asistencia = 'El plazo de confirmación terminó; consulta al profesor.'
                    else:
                        ultimo = reserva.socio.accesos.filter(resultado='PERMITIDO').order_by('-fecha_hora', '-id').first()
                        reserva.puede_confirmar_recepcion = bool(ultimo and ultimo.tipo == 'ENTRADA' and inicio - timedelta(minutes=30) <= ultimo.fecha_hora <= ahora)
                        if not reserva.puede_confirmar_recepcion:
                            reserva.motivo_asistencia = 'Requiere una entrada permitida reciente al gimnasio.'
                elif clase.asistencia_cerrada_en and reserva.asistencia == 'PENDIENTE':
                    reserva.motivo_asistencia = 'La asistencia está cerrada; solicita revisión.'
        espera = [r for r in clase.reservas_portal if r.estado == 'LISTA_ESPERA']
        filas.append({'clase': clase, 'confirmadas': confirmadas, 'espera': espera, 'reservados': len(confirmadas), 'disponibles': max(clase.capacidad_maxima - len(confirmadas), 0), 'porcentaje': min(100, round(len(confirmadas) * 100 / clase.capacidad_maxima)) if clase.capacidad_maxima else 0, 'imagen_tipo': imagen_tipo_clase(clase)})
    contexto = {
        'filas': filas,
        'consulta': consulta,
        'clase_buscada': clase_buscada,
        'resultados': resultados,
        'es_admin': es_admin,
        'ver_inactivas': ver_inactivas,
        'clase_nombre': clase_nombre, 'clase_fecha': clase_fecha, 'hoy': hoy,
    }
    if es_admin:
        from .models import ProgramacionClase
        contexto['alertas_horario'] = ProgramacionClase.objects.filter(activa=True).exclude(aviso_conflicto='')
    return render(request, 'clases/admin_list.html' if es_admin else 'clases/list.html', contexto)


@login_required
@user_passes_test(es_administrador, login_url='dashboard_redirect')
def admin_clase_detalle_view(request, clase_id):
    clase = get_object_or_404(Clase.all_objects.select_related('profesor', 'actividad', 'sala_asignada'), pk=clase_id)
    reservas = list(clase.reservas.filter(estado__in=['CONFIRMADA', 'LISTA_ESPERA']).select_related('socio').order_by('fecha_reserva', 'pk'))
    confirmadas = [r for r in reservas if r.estado == 'CONFIRMADA']
    espera = [r for r in reservas if r.estado == 'LISTA_ESPERA']
    asistencias = sum(r.asistencia == 'ASISTIO' for r in confirmadas)
    return render(request, 'clases/admin_detalle.html', {
        'clase': clase, 'imagen_tipo': imagen_tipo_clase(clase),
        'confirmadas': confirmadas, 'espera': espera, 'asistencias': asistencias,
        'disponibles': max(clase.capacidad_maxima - len(confirmadas), 0),
    })


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def reservar_clase_web_view(request, clase_id):
    """Procesa una reserva desde la interfaz web."""
    if request.method == 'POST':
        socio_id = request.POST.get('socio_id')
        if not socio_id or not socio_id.isdigit():
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
def confirmar_asistencia_web_view(request, reserva_id):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    exito, mensaje = confirmar_asistencia_recepcion(reserva_id, request.user)
    (messages.success if exito else messages.warning)(request, mensaje)
    return redirect('clases_list')


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def confirmar_asistencia_api_view(request, reserva_id):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    exito, mensaje = confirmar_asistencia_recepcion(reserva_id, request.user)
    return JsonResponse({'exito': exito, 'mensaje': mensaje}, status=200 if exito else 400)


@login_required
@user_passes_test(es_recepcionista_o_admin, login_url='login')
def control_acceso_view(request):
    """Vista dedicada a la simulación y validación de códigos QR."""
    if es_administrador(request.user):
        return redirect('actividad_gimnasio')
    ultimos_accesos = RegistroAcceso.objects.select_related('socio').order_by('-fecha_hora')[:10]
    contexto = {'ultimos_accesos': ultimos_accesos, 'aforo': calcular_aforo_actual()}
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


class SoloRecepcionOperativa(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user.is_authenticated and request.user.groups.filter(name='Recepcion').exists() and not es_administrador(request.user) and not es_profesor(request.user))


class ValidarAccesoAPIView(APIView):
    """
    Endpoint POST /api/acceso/validar/
    Recibe un código QR o identificador, evalúa las reglas de negocio y registra el acceso.
    Retorna la decisión, detalles del socio y latencia medida en milisegundos.
    """
    permission_classes = [SoloRecepcionOperativa]

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

        clases_proximas = []
        if resultado['autorizado'] and resultado['tipo'] == 'ENTRADA' and resultado['socio']:
            from datetime import timedelta
            ahora = timezone.now()
            fecha_local = timezone.localtime(ahora).date()
            reservas = Reserva.objects.filter(
                socio=resultado['socio'], estado='CONFIRMADA', asistencia='PENDIENTE',
                clase__fecha__in=[fecha_local, fecha_local + timedelta(days=1)],
                clase__activo=True,
            ).select_related('clase')
            for reserva in reservas:
                inicio = inicio_clase_local(reserva.clase)
                if inicio - timedelta(minutes=30) <= ahora <= inicio + timedelta(minutes=5) and not reserva.clase.asistencia_cerrada_en:
                    clases_proximas.append({'reserva_id': reserva.pk, 'nombre': reserva.clase.nombre, 'hora': inicio.strftime('%H:%M')})

        response_payload = {
            'clases_proximas': clases_proximas,
            'autorizado': resultado['autorizado'],
            'motivo': resultado['motivo'],
            'tipo': resultado['tipo'],
            'tiempo_respuesta_ms': resultado['tiempo_respuesta_ms'],
            'registro_id': resultado['registro_id'],
            'socio': socio_data,
        }
        return Response(response_payload, status=status.HTTP_200_OK)


class DisponibilidadClasesAPIView(APIView):
    """Cupos reservados para clases de hoy; no mide ocupación física de salas."""
    permission_classes = [PersonalFitLife]

    def get(self, request):
        ahora = timezone.localtime()
        clases = Clase.objects.filter(fecha=ahora.date(), hora_fin__gte=ahora.time()).select_related('sala_asignada').order_by('hora_inicio', 'pk')
        filas = []
        for clase in clases:
            confirmadas = clase.reservas.filter(estado='CONFIRMADA').count()
            filas.append({'clase': clase.nombre, 'sala': clase.sala_asignada.nombre if clase.sala_asignada else clase.sala, 'inicio': clase.hora_inicio.strftime('%H:%M'), 'fin': clase.hora_fin.strftime('%H:%M'), 'reservados': confirmadas, 'capacidad': clase.capacidad_maxima, 'disponibles': max(clase.capacidad_maxima - confirmadas, 0)})
        return Response({'clases': filas})


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
    queryset = Clase.objects.select_related('profesor', 'sala_asignada')
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
