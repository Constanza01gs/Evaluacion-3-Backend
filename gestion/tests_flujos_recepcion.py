from datetime import time, timedelta
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from gestion.models import Clase, Membresia, Plan, Profesor, Reserva, Socio, SolicitudCongelamiento

class FlujosRecepcionTest(TestCase):
    def setUp(self):
        self.recep = User.objects.create_user('recepcion_flujos', password='pass')
        self.recep.groups.add(Group.objects.create(name='Recepcion'))
        self.admin = User.objects.create_superuser('admin_flujos', password='pass')
        self.socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='camila@example.com', telefono='912345678')
        plan = Plan.objects.create(nombre='Plan', precio=20000, duracion_dias=30)
        self.membresia = Membresia.objects.create(socio=self.socio, plan=plan, fecha_inicio=timezone.localdate(), fecha_fin=timezone.localdate()+timedelta(days=29))
        self.prof = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        self.clase = Clase.objects.create(nombre='Yoga prueba', profesor=self.prof, fecha=timezone.localdate()+timedelta(days=2), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        Reserva.objects.create(clase=self.clase, socio=self.socio, estado='CONFIRMADA')
        self.client.force_login(self.recep)

    def test_membresias_solo_se_editan_por_admin(self):
        url = reverse('gestion_editar', args=['membresias', self.membresia.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.assertNotContains(self.client.get(reverse('gestion_list', args=['membresias'])), url)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_filtro_clase_y_aviso_de_asistencia(self):
        response = self.client.get(reverse('clases_list'), {'clase_q':'Yoga', 'fecha':self.clase.fecha.isoformat()})
        self.assertContains(response, 'Yoga prueba')
        self.assertContains(response, 'Disponible desde 30 minutos antes')
        self.assertNotContains(response, 'Confirmar asistencia</button>')
        self.assertNotContains(self.client.get(reverse('clases_list'), {'clase_q':'Pilates'}), 'Yoga prueba')

    def test_solicitudes_propias_en_ficha(self):
        SolicitudCongelamiento.objects.create(membresia=self.membresia, solicitante=self.recep, fecha_inicio=timezone.localdate()+timedelta(days=2), fecha_fin=timezone.localdate()+timedelta(days=3))
        self.assertContains(self.client.get(reverse('socio_ficha', args=[self.socio.pk])), 'Mis solicitudes para este socio')
        self.assertContains(self.client.get(reverse('socio_ficha', args=[self.socio.pk])), 'Congelamiento')
    def test_paginas_recepcion_con_estilo_propio(self):
        for name in ['recepcion_dashboard', 'control_acceso', 'socios_list', 'recepcion_contratar_membresia', 'clases_list', 'recepcion_notificaciones']:
            with self.subTest(pagina=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'recepcion-unificado')
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(reverse('socios_list')), 'recepcion-unificado.css')
    def test_detalle_pago_recepcion_y_socio_correcto(self):
        from gestion.models import Pago
        pago = Pago.objects.create(membresia=self.membresia, monto=20000, metodo_pago='EFECTIVO')
        ficha = self.client.get(reverse('socio_ficha', args=[self.socio.pk]))
        self.assertContains(ficha, 'Solicitar anulación')
        url = reverse('recepcion_pago_detalle', args=[self.socio.pk, pago.pk])
        detalle = self.client.get(url + '?solicitar=1')
        self.assertContains(detalle, 'Efectivo')
        self.assertContains(detalle, 'Solicitar anulación')
        otro = Socio.objects.create(rut='11111111-1', nombres='Otra', apellidos='Persona', email='otra@example.com', telefono='912345679')
        self.assertEqual(self.client.get(reverse('recepcion_pago_detalle', args=[otro.pk, pago.pk])).status_code, 404)
        visitante = User.objects.create_user('visitante_ficha', password='pass')
        self.client.force_login(visitante)
        self.assertNotEqual(self.client.get(url).status_code, 200)
    def test_ficha_membresia_recepcion_condiciones_y_permisos(self):
        from gestion.models import Pago
        Pago.objects.create(membresia=self.membresia, monto=20000, metodo_pago='EFECTIVO')
        url = reverse('recepcion_membresia_ficha', args=[self.membresia.pk])
        self.assertContains(self.client.get(reverse('gestion_list', args=['membresias'])), url)
        detalle = self.client.get(url)
        self.assertContains(detalle, self.socio.nombre_completo)
        self.assertContains(detalle, 'Precio contratado')
        self.assertContains(detalle, 'Ver detalle')
        self.assertNotContains(detalle, 'Editar membresía')
        visitante = User.objects.create_user('membresia_visitante', password='pass')
        self.client.force_login(visitante)
        self.assertNotEqual(self.client.get(url).status_code, 200)
