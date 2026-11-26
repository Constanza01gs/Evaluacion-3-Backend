from datetime import timedelta
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from .models import Membresia, Plan, Socio
from .services import solicitar_congelamiento_socio, aprobar_congelamiento_sin_conflictos
from .views import _validar_contratacion_socio


class RenovacionPausaTest(TestCase):
    def test_renovacion_bloqueada_durante_pausa_aprobada_y_permitida_al_terminar(self):
        hoy = timezone.localdate()
        usuario = User.objects.create_user('socio_prueba_pausa', password='clave')
        admin = User.objects.create_superuser('admin_prueba_pausa', password='clave')
        socio = Socio.objects.create(usuario=usuario, rut='12345678-5', nombres='Ana', apellidos='Prueba', email='pausarenovacion@example.org', telefono='912345678')
        plan = Plan.objects.create(nombre='Plan pausa', precio=35000, duracion_dias=30, dias_congelamiento=7)
        membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy + timedelta(days=29))
        pausa = solicitar_congelamiento_socio(membresia.pk, usuario, hoy, hoy + timedelta(days=1))
        aprobar_congelamiento_sin_conflictos(pausa.pk, admin)
        membresia.refresh_from_db()
        self.assertEqual(membresia.estado, 'ACTIVA')
        self.assertEqual(membresia.estado_actual_display, 'Congelada')
        with self.assertRaisesMessage(ValidationError, 'Tu membresía está congelada'):
            _validar_contratacion_socio(socio, plan, membresia.fecha_fin + timedelta(days=1))
        pausa.refresh_from_db()
        pausa.fecha_reactivacion = hoy
        pausa.save(update_fields=['fecha_reactivacion'])
        self.assertEqual(_validar_contratacion_socio(socio, plan, membresia.fecha_fin + timedelta(days=1)), membresia.fecha_fin + timedelta(days=30))
