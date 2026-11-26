from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import Membresia, Pago, Plan, Socio


class ReportesTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin_reportes', password='clave')
        self.otro = User.objects.create_user('otro_reportes', password='clave')
        socio = Socio.objects.create(rut='12345678-5', nombres='Ana', apellidos='Prueba', email='reportes@example.com', telefono='912345678')
        plan = Plan.objects.create(nombre='Plan reporte', precio=10000, duracion_dias=30, dias_congelamiento=0)
        membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=timezone.localdate(), fecha_fin=timezone.localdate()+timedelta(days=29))
        Pago.objects.create(membresia=membresia, monto=10000, metodo_pago='EFECTIVO')
        Pago.objects.create(membresia=membresia, monto=5000, metodo_pago='EFECTIVO', anulado=True)

    def test_datos_pdf_y_permisos(self):
        self.client.force_login(self.otro)
        self.assertNotEqual(self.client.get(reverse('reportes')).status_code, 200)
        self.assertNotEqual(self.client.get(reverse('reportes_pdf')).status_code, 200)
        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse('reportes'))
        self.assertEqual(respuesta.context['ingresos'], 10000)
        self.assertEqual(respuesta.context['pagos_count'], 1)
        self.assertEqual(respuesta.context['membresias_count'], 1)
        pdf = self.client.get(reverse('reportes_pdf'))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        self.assertEqual(pdf['Content-Type'], 'application/pdf')

    def test_periodo_invalido_no_genera_pdf(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('reportes_pdf'), {'desde':'2026-12-31', 'hasta':'2026-01-01'}).status_code, 400)

    def test_detalle_general_con_estados_y_fechas(self):
        from datetime import time
        from .models import Clase, Profesor, RegistroAcceso, Reserva
        socio = Socio.objects.get(email='reportes@example.com')
        profesor = Profesor.objects.create(nombres='Pilar', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=timezone.localdate(), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        Reserva.objects.create(socio=socio, clase=clase, estado='CONFIRMADA', asistencia='ASISTIO')
        RegistroAcceso.objects.create(socio=socio, tipo='ENTRADA', resultado='PERMITIDO', motivo='OK')
        RegistroAcceso.objects.create(socio=socio, tipo='ENTRADA', resultado='DENEGADO', motivo='No autorizado')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('reportes'))
        datos = response.context
        self.assertEqual(datos['pagos_anulados'], 1)
        self.assertEqual(datos['monto_anulado'], 5000)
        self.assertEqual(datos['socios_nuevos'], 1)
        self.assertEqual(datos['clases_periodo'], 1)
        self.assertEqual(datos['accesos_count'], 1)
        self.assertEqual(datos['accesos_denegados'], 1)
        self.assertEqual(datos['asistencias_count'], 1)
        self.assertEqual(datos['reservas_confirmadas'], 1)
        self.assertEqual(self.client.get(reverse('reportes_pdf')).status_code, 200)
