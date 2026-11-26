from datetime import timedelta, time
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from gestion.models import (Socio, Plan, Membresia, Pago, Profesor, Clase, Reserva,
    SolicitudCongelamiento, SolicitudCorreccionAsistencia, SolicitudAnulacionPago, AvisoIndisponibilidad)

class Command(BaseCommand):
    help = 'Agrega ejemplos identificados para revisar las solicitudes sin modificar datos existentes.'

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.filter(username__startswith='demo_solicitudes_').exists():
            raise CommandError('Ya existen ejemplos de solicitudes. No se agregó ni modificó información.')
        hoy = timezone.localdate()
        ahora = timezone.now()
        plan = Plan.objects.filter(permite_clases=True, dias_congelamiento__gte=7).order_by('pk').first()
        if not plan:
            raise CommandError('Se necesita un plan activo que incluya clases y al menos 7 días de congelamiento.')
        admin = User.objects.filter(is_superuser=True, is_active=True).order_by('pk').first()
        if not admin:
            raise CommandError('Se necesita una cuenta administradora activa para identificar las revisiones.')
        grupo_socio, _ = Group.objects.get_or_create(name='Socio')
        grupo_profe, _ = Group.objects.get_or_create(name='Profesor')
        def cuenta(sufijo, nombre, apellido, grupo):
            u = User(username='demo_solicitudes_'+sufijo, first_name=nombre, last_name=apellido, email='demo.solicitudes.'+sufijo+'@example.invalid')
            u.set_unusable_password(); u.save(); u.groups.add(grupo)
            return u
        profe_user = cuenta('profesor', 'Andrea', 'Ejemplo', grupo_profe)
        profe = Profesor.objects.create(usuario=profe_user, nombres='Andrea', apellidos='Ejemplo', especialidad='Yoga')
        miembros = []
        nombres = ['Camila', 'Tomás', 'Valentina', 'Martín', 'Daniela', 'Javier']
        for i, nombre in enumerate(nombres, 1):
            u = cuenta(f'socio{i}', nombre, 'Ejemplo', grupo_socio)
            socio = Socio.objects.create(usuario=u, rut=f'DEMO-SOL-{i}', nombres=nombre, apellidos='Ejemplo', email=f'demo.solicitudes.socio{i}@example.invalid', telefono='900000000')
            mem = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy-timedelta(days=10), fecha_fin=hoy+timedelta(days=30), precio_contratado=plan.precio, clases_contratadas=plan.permite_clases, limite_clases_contratado=plan.limite_clases, limite_clases_registrado=True, dias_congelamiento_contratados=plan.dias_congelamiento)
            miembros.append((u,socio,mem))
        for i, estado in enumerate(['PENDIENTE','PENDIENTE','APROBADA','RECHAZADA','CANCELADA']):
            u,socio,mem = miembros[i]
            fecha = hoy + timedelta(days=3) if estado == 'PENDIENTE' else hoy-timedelta(days=8)
            datos = dict(membresia=mem, solicitante=u, fecha_inicio=fecha, fecha_fin=fecha+timedelta(days=2), estado=estado)
            if estado in ('APROBADA','RECHAZADA'): datos.update(revisado_por=admin, revisada_en=ahora-timedelta(days=3))
            if estado == 'RECHAZADA': datos['motivo_rechazo']='Ejemplo: las fechas solicitadas no cumplen las condiciones.'
            if estado == 'CANCELADA': datos.update(cancelada_por=u, cancelada_en=ahora-timedelta(days=2))
            SolicitudCongelamiento.objects.create(**datos)
        pasada=Clase.objects.create(nombre='Yoga · Ejemplo', profesor=profe, fecha=hoy-timedelta(days=3), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=12, asistencia_cerrada_en=ahora-timedelta(days=3), asistencia_cerrada_por=admin)
        futura=Clase.objects.create(nombre='Funcional · Ejemplo', profesor=profe, fecha=hoy+timedelta(days=2), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=12)
        for i, estado in enumerate(['PENDIENTE','APROBADA','RECHAZADA']):
            u,socio,mem = miembros[i]
            asistencia = 'ASISTIO' if estado == 'APROBADA' else 'NO_ASISTIO'
            reserva=Reserva.objects.create(clase=pasada,socio=socio,membresia=mem,estado='CONFIRMADA',asistencia=asistencia)
            datos=dict(reserva=reserva, solicitante=profe_user, estado_solicitado='ASISTIO', motivo='Ejemplo: el socio asistió, pero su ingreso se registró tarde.', estado=estado)
            if estado != 'PENDIENTE': datos.update(revisado_por=admin, fecha_revision=ahora-timedelta(days=1), comentario_revision='Ejemplo de revisión administrativa.')
            SolicitudCorreccionAsistencia.objects.create(**datos)
        AvisoIndisponibilidad.objects.create(clase=futura, profesor=profe, motivo='Ejemplo: indisponibilidad por una cita médica.')
        AvisoIndisponibilidad.objects.create(clase=pasada, profesor=profe, motivo='Ejemplo: actividad personal informada con anticipación.', revisado_en=ahora-timedelta(days=1), revisado_por=admin)
        for i, estado in enumerate(['PENDIENTE','PENDIENTE','APROBADA','RECHAZADA']):
            u,socio,mem=miembros[i]
            pago=Pago.objects.create(membresia=mem, monto=plan.precio, metodo_pago='TARJETA_DEBITO', numero_comprobante=f'DEMO-SOL-{i+1}', observaciones='Pago de ejemplo para solicitudes.', anulado=estado=='APROBADA', motivo_anulacion='Ejemplo: pago duplicado' if estado=='APROBADA' else '', fecha_anulacion=ahora-timedelta(days=1) if estado=='APROBADA' else None, anulado_por=admin if estado=='APROBADA' else None)
            datos=dict(pago=pago, solicitante=u, origen='SOCIO', motivo='Ejemplo: solicitar revisión por pago duplicado.', estado=estado)
            if estado != 'PENDIENTE': datos.update(revisado_por=admin, revisada=ahora-timedelta(days=1), comentario='Ejemplo de resolución administrativa.')
            SolicitudAnulacionPago.objects.create(**datos)
        self.stdout.write(self.style.SUCCESS('Ejemplos creados: 5 congelamientos, 3 correcciones de asistencia, 2 avisos y 4 solicitudes de pago. Todos identificados como Ejemplo.'))
