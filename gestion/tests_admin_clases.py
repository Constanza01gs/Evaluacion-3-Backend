from datetime import time, timedelta
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone
from gestion.models import Clase, Profesor, Reserva, Socio

class AdminClasesTest(TestCase):
    def test_supervision_and_reception_remain_separate(self):
        admin = User.objects.create_superuser('admin_clases', password='pass')
        recepcion = User.objects.create_user('recepcion_clases', password='pass')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Pérez', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=timezone.localdate()+timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=5)
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Torres', email='camila@example.com', telefono='912345678')
        Reserva.objects.create(clase=clase, socio=socio, estado='CONFIRMADA', asistencia='ASISTIO')
        self.client.force_login(admin)
        lista = self.client.get('/clases/')
        self.assertContains(lista, 'Ver detalle')
        self.assertNotContains(lista, 'Reservar para un socio')
        detalle = self.client.get(f'/clases/{clase.pk}/detalle/')
        self.assertContains(detalle, 'Camila Torres')
        self.assertContains(detalle, '4')
        self.assertContains(detalle, 'Asistió')
        self.assertNotContains(detalle, 'Revisar y cerrar asistencia')
        self.assertContains(detalle, 'super-attendance attended')
        self.client.force_login(recepcion)
        self.assertContains(self.client.get('/clases/'), 'Reservar para un socio')
        self.assertEqual(self.client.get(f'/clases/{clase.pk}/detalle/').status_code, 302)
