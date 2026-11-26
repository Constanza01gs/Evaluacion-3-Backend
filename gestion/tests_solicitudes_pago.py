from datetime import timedelta
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone
from gestion.models import Socio, Plan, Membresia, Pago, SolicitudAnulacionPago, NotificacionSocio, NotificacionRecepcion

class PaymentRequestTest(TestCase):
    def setUp(self):
        self.admin=User.objects.create_superuser('admin_requests',password='pass')
        self.socio_user=User.objects.create_user('socio_requests',password='pass')
        self.socio_user.groups.add(Group.objects.create(name='Socio'))
        self.reception=User.objects.create_user('recepcion_requests',password='pass')
        self.reception.groups.add(Group.objects.create(name='Recepcion'))
        self.other=User.objects.create_user('otra_recepcion',password='pass');self.other.groups.add(Group.objects.get(name='Recepcion'))
        self.socio=Socio.objects.create(usuario=self.socio_user,rut='12345678-5',nombres='Ana',apellidos='Prueba',email='ana@example.com',telefono='912345678')
        plan=Plan.objects.create(nombre='Plus prueba',precio=25000,duracion_dias=30,dias_congelamiento=0)
        self.membership=Membresia.objects.create(socio=self.socio,plan=plan,fecha_inicio=timezone.localdate(),fecha_fin=timezone.localdate()+timedelta(days=29))
        self.payment=Pago.objects.create(membresia=self.membership,monto=25000,metodo_pago='EFECTIVO')
        self.url=f'/pagos/{self.payment.pk}/solicitar-anulacion/'
    def test_member_request_approval_no_duplicates(self):
        self.client.force_login(self.socio_user)
        self.assertContains(self.client.get('/socio/pagos/'),'Solicitar anulación')
        self.assertEqual(self.client.post(self.url,{'motivo':'Duplicado'}).status_code,302)
        self.assertEqual(self.client.post(self.url,{'motivo':'Duplicado'}).status_code,302)
        self.assertEqual(SolicitudAnulacionPago.objects.count(),1)
        solicitud=SolicitudAnulacionPago.objects.get()
        self.assertTrue(NotificacionRecepcion.objects.filter(destinatario=self.admin).exists())
        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/gestion/pagos/'),'Ingresos registrados')
        self.assertContains(self.client.get('/gestion/pagos/nuevo/'),'Guardar')
        self.assertContains(self.client.get('/solicitudes/pagos/'),'Duplicado')
        self.assertEqual(self.client.post(f'/solicitudes/pagos/{solicitud.pk}/resolver/',{'decision':'APROBADA','comentario':'Revisado'}).status_code,302)
        self.payment.refresh_from_db();self.membership.refresh_from_db();solicitud.refresh_from_db()
        self.assertTrue(self.payment.anulado);self.assertEqual(solicitud.estado,'APROBADA');self.assertFalse(self.membership.activo)
        self.assertTrue(NotificacionSocio.objects.filter(socio=self.socio,mensaje__contains='Revisado').exists())
        self.assertContains(self.client.get('/solicitudes/?categoria=pagos'),'Ver detalle')
        self.assertContains(self.client.get(f'/solicitudes/historial/pagos/{solicitud.pk}/'),'Duplicado')
        self.client.post(f'/solicitudes/pagos/{solicitud.pk}/resolver/',{'decision':'APROBADA'})
        self.assertEqual(NotificacionSocio.objects.filter(socio=self.socio,tipo='ANULACION_PAGO').count(),1)
    def test_reception_rejection_notification_is_private(self):
        self.client.force_login(self.reception)
        self.assertContains(self.client.get(f'/socios/{self.socio.pk}/'),'Solicitar anulación')
        self.client.post(self.url,{'motivo':'Corregir medio'})
        solicitud=SolicitudAnulacionPago.objects.get()
        self.client.force_login(self.admin)
        self.client.post(f'/solicitudes/pagos/{solicitud.pk}/resolver/',{'decision':'RECHAZADA','comentario':'No procede'})
        self.payment.refresh_from_db();self.assertFalse(self.payment.anulado)
        self.client.force_login(self.reception)
        self.assertContains(self.client.get('/recepcion/notificaciones/'),'No procede')
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get('/recepcion/notificaciones/'),'No procede')
        self.assertNotContains(self.client.get('/recepcion/notificaciones/resumen/'),'Nueva solicitud de anulación')
    def test_member_cannot_request_someone_elses_payment(self):
        other=User.objects.create_user('otro_socio',password='pass');other.groups.add(Group.objects.get(name='Socio'))
        Socio.objects.create(usuario=other,rut='11111111-1',nombres='Otro',apellidos='Socio',email='otro@example.com',telefono='912345679')
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.url,{'motivo':'Error'}).status_code,404)

    def test_ingresos_excludes_cancelled_and_filters_method(self):
        Pago.objects.create(membresia=self.membership, monto=10000, metodo_pago='EFECTIVO', anulado=True, motivo_anulacion='Duplicado')
        Pago.objects.create(membresia=self.membership, monto=15000, metodo_pago='TRANSFERENCIA')
        self.client.force_login(self.admin)
        response=self.client.get('/gestion/pagos/')
        self.assertEqual(response.context['total'], 40000)
        self.assertEqual(response.context['monto_anulado'], 10000)
        self.assertEqual(response.context['cantidad'], 2)
        self.assertEqual(response.context['cantidad_anulados'], 1)
        response=self.client.get('/gestion/pagos/', {'metodo':'EFECTIVO'})
        self.assertEqual(response.context['total'], 25000)
        self.assertEqual(response.context['cantidad_anulados'], 1)
        self.client.force_login(self.reception)
        self.assertEqual(self.client.get('/gestion/pagos/').status_code, 302)

    def test_anulacion_cancela_solo_reservas_de_la_membresia_y_permite_contratar(self):
        from datetime import time
        from gestion.models import Clase, Profesor, Reserva
        from gestion.views import _validar_contratacion_socio
        hoy = timezone.localdate()
        prof = Profesor.objects.create(nombres='Pilar', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Clase actual', profesor=prof, fecha=hoy+timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        reserva = Reserva.objects.create(socio=self.socio, clase=clase, membresia=self.membership, estado='CONFIRMADA', consume_clase=True)
        clase_legacy = Clase.objects.create(nombre='Clase antigua', profesor=prof, fecha=hoy+timedelta(days=2), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        reserva_legacy = Reserva.objects.create(socio=self.socio, clase=clase_legacy, estado='LISTA_ESPERA')
        self.client.force_login(self.socio_user)
        self.assertContains(self.client.get('/socio/pagos/'), 'también se desactivará la membresía')
        self.client.post(self.url, {'motivo':'No continuar'})
        self.membership.refresh_from_db()
        self.assertTrue(self.membership.activo)
        solicitud=SolicitudAnulacionPago.objects.get(pago=self.payment)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/solicitudes/pagos/'), 'la membresía asociada se desactiva')
        self.client.post(f'/solicitudes/pagos/{solicitud.pk}/resolver/', {'decision':'APROBADA'})
        self.membership.refresh_from_db(); reserva.refresh_from_db(); reserva_legacy.refresh_from_db()
        self.assertFalse(self.membership.activo)
        self.assertEqual(reserva.estado, 'CANCELADA')
        self.assertFalse(reserva.consume_clase)
        self.assertEqual(reserva_legacy.estado, 'CANCELADA')
        self.assertEqual(_validar_contratacion_socio(self.socio, self.membership.plan, hoy), hoy+timedelta(days=29))
        self.assertTrue(NotificacionSocio.objects.filter(socio=self.socio, mensaje__contains='membresía asociada fue desactivada').exists())

    def test_anulacion_de_renovacion_conserva_la_membresia_y_reservas_actuales(self):
        from datetime import time
        from gestion.models import Clase, Profesor, Reserva
        hoy=timezone.localdate()
        futura=Membresia.objects.create(socio=self.socio,plan=self.membership.plan,fecha_inicio=hoy+timedelta(days=30),fecha_fin=hoy+timedelta(days=59))
        pago_futuro=Pago.objects.create(membresia=futura,monto=25000,metodo_pago='EFECTIVO')
        prof=Profesor.objects.create(nombres='Ana',apellidos='Profesora',especialidad='Yoga')
        clase=Clase.objects.create(nombre='Clase vigente',profesor=prof,fecha=hoy+timedelta(days=1),hora_inicio=time(10),hora_fin=time(11),capacidad_maxima=10)
        reserva=Reserva.objects.create(socio=self.socio,clase=clase,membresia=self.membership,estado='CONFIRMADA')
        self.client.force_login(self.socio_user)
        self.client.post(f'/pagos/{pago_futuro.pk}/solicitar-anulacion/',{'motivo':'No continuar'})
        solicitud=SolicitudAnulacionPago.objects.get(pago=pago_futuro)
        self.client.force_login(self.admin)
        self.client.post(f'/solicitudes/pagos/{solicitud.pk}/resolver/',{'decision':'APROBADA'})
        self.membership.refresh_from_db(); futura.refresh_from_db(); reserva.refresh_from_db()
        self.assertTrue(self.membership.activo)
        self.assertFalse(futura.activo)
        self.assertEqual(reserva.estado,'CONFIRMADA')

    def test_si_no_se_puede_cancelar_reserva_no_se_anula_ningun_registro(self):
        from datetime import time
        from unittest.mock import patch
        from gestion.models import Clase, Profesor, Reserva
        prof=Profesor.objects.create(nombres='Pilar',apellidos='Prueba',especialidad='Yoga')
        clase=Clase.objects.create(nombre='Clase pendiente',profesor=prof,fecha=timezone.localdate()+timedelta(days=1),hora_inicio=time(10),hora_fin=time(11),capacidad_maxima=10)
        reserva=Reserva.objects.create(socio=self.socio,clase=clase,membresia=self.membership,estado='CONFIRMADA')
        self.client.force_login(self.socio_user)
        self.client.post(self.url,{'motivo':'No continuar'})
        solicitud=SolicitudAnulacionPago.objects.get(pago=self.payment)
        self.client.force_login(self.admin)
        with patch('gestion.services.cancelar_reserva_clase',return_value=(False,'Revisión necesaria')):
            self.client.post(f'/solicitudes/pagos/{solicitud.pk}/resolver/',{'decision':'APROBADA'})
        self.payment.refresh_from_db();self.membership.refresh_from_db();solicitud.refresh_from_db();reserva.refresh_from_db()
        self.assertFalse(self.payment.anulado)
        self.assertTrue(self.membership.activo)
        self.assertEqual(solicitud.estado,'PENDIENTE')
        self.assertEqual(reserva.estado,'CONFIRMADA')
