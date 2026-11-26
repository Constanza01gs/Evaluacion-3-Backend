from datetime import datetime, time
from unittest.mock import patch
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone
from gestion.models import Clase, Profesor, RegistroAcceso, Reserva, Sala, Socio

class GymActivityTest(TestCase):
    def setUp(self):
        self.admin=User.objects.create_superuser('activity_admin',password='pass')
        self.staff=User.objects.create_user('activity_recepcion',password='pass')
        self.staff.groups.add(Group.objects.create(name='Recepcion'))
        self.socio=Socio.objects.create(rut='12345678-5',nombres='Camila',apellidos='Prueba',email='camila@example.com',telefono='912345678')
        self.prof=Profesor.objects.create(nombres='Ana',apellidos='Prueba',especialidad='Yoga')
        self.sala=Sala.objects.create(nombre='Sala Uno',capacidad=15)
        self.clase=Clase.objects.create(nombre='Yoga',profesor=self.prof,sala_asignada=self.sala,fecha=timezone.localdate(),hora_inicio=time(10),hora_fin=time(13),capacidad_maxima=10)
        Reserva.objects.create(socio=self.socio,clase=self.clase,estado='CONFIRMADA',asistencia='ASISTIO')
        RegistroAcceso.objects.create(socio=self.socio,tipo='ENTRADA',resultado='DENEGADO',motivo='Sin membresía')
    def test_admin_summary_filters_and_role_boundaries(self):
        self.client.force_login(self.admin)
        now=timezone.make_aware(datetime.combine(timezone.localdate(),time(12)))
        original=timezone.localtime
        with patch('gestion.actividad_gimnasio.timezone.localtime',side_effect=lambda value=None: now if value is None else original(value)):
            response=self.client.get('/actividad-gimnasio/',{'q':'Camila Prueba','resultado':'DENEGADO'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['clases_en_curso'],1)
        self.assertEqual(response.context['salas_en_curso'],1)
        self.assertEqual(response.context['salas'][0]['clase'].confirmadas,1)
        self.assertEqual(response.context['salas'][0]['clase'].asistencias,1)
        self.assertContains(response,'Sin membresía')
        self.assertNotContains(self.client.get('/actividad-gimnasio/',{'q':'Otra persona'}),'Sin membresía')
        self.assertRedirects(self.client.get('/control-acceso/'),'/actividad-gimnasio/',fetch_redirect_response=False)
        self.assertEqual(self.client.post('/api/acceso/validar/',{'identificador':'codigo','tipo':'ENTRADA'},content_type='application/json').status_code,403)
        self.client.force_login(self.staff)
        self.assertNotEqual(self.client.get('/actividad-gimnasio/').status_code,200)
        self.assertEqual(self.client.get('/control-acceso/').status_code,200)
        self.assertEqual(self.client.post('/api/acceso/validar/',{'identificador':self.socio.codigo_qr,'tipo':'ENTRADA'},content_type='application/json').status_code,200)
