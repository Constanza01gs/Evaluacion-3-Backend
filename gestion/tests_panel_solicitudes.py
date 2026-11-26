from datetime import timedelta, time
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone
from gestion.models import Clase, Profesor, AvisoIndisponibilidad, NotificacionRecepcion, Socio, Reserva

class SolicitudesPanelTest(TestCase):
    def setUp(self):
        self.admin=User.objects.create_superuser('admin_panel',password='x')
        self.client.force_login(self.admin)
        self.original=Profesor.objects.create(nombres='Ana',apellidos='Original',especialidad='Yoga')
        self.reemplazo=Profesor.objects.create(nombres='Luis',apellidos='Reemplazo',especialidad='Yoga')
        self.clase=Clase.objects.create(nombre='Yoga',profesor=self.original,fecha=timezone.localdate()+timedelta(days=2),hora_inicio=time(10),hora_fin=time(11),capacidad_maxima=5)
        self.aviso=AvisoIndisponibilidad.objects.create(clase=self.clase,profesor=self.original,motivo='Viaje')
        socio=Socio.objects.create(rut='12345678-5',nombres='Ana',apellidos='Socio',email='ana@example.com',telefono='912345678')
        Reserva.objects.create(clase=self.clase,socio=socio,estado='CONFIRMADA')
    def test_templates_replacement_and_notification(self):
        for url in ['/solicitudes/', '/solicitudes/?categoria=avisos', '/solicitudes/asistencia/', '/gestion/avisos-profesores/', '/solicitudes/pagos/']:
            self.assertEqual(self.client.get(url).status_code,200)
        response=self.client.get('/gestion/avisos-profesores/')
        self.assertContains(response,'Asignar reemplazo')
        self.client.post(f'/gestion/avisos-profesores/{self.aviso.pk}/reemplazo/',{'profesor':self.reemplazo.pk})
        self.clase.refresh_from_db();self.aviso.refresh_from_db()
        self.assertEqual(self.clase.profesor,self.reemplazo)
        self.assertTrue(self.aviso.revisado_en)
        self.assertIn('Reemplazo asignado',self.aviso.resolucion)
        self.assertTrue(self.clase.reservas.filter(estado='CONFIRMADA').exists())
        aviso=NotificacionRecepcion.objects.create(destinatario=self.admin,mensaje='Aviso de ejemplo',destino='/solicitudes/asistencia/#asistencia-1')
        self.assertEqual(self.client.get('/recepcion/notificaciones/resumen/').json()['avisos'][0]['url'],aviso.destino)
    def test_replacement_rejects_schedule_collision(self):
        Clase.objects.create(nombre='Otra',profesor=self.reemplazo,fecha=self.clase.fecha,hora_inicio=time(10,30),hora_fin=time(11,30),capacidad_maxima=5)
        self.client.post(f'/gestion/avisos-profesores/{self.aviso.pk}/reemplazo/',{'profesor':self.reemplazo.pk})
        self.clase.refresh_from_db();self.aviso.refresh_from_db()
        self.assertEqual(self.clase.profesor,self.original)
        self.assertIsNone(self.aviso.revisado_en)
