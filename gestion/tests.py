"""
Suite de Pruebas Unitarias y de Integración para FitLife.
Verifica las 4 funcionalidades clave y las reglas de negocio exigidas en la rúbrica:
1. Estados de membresía y acceso QR (Permitido vs Denegado).
2. Prevención de doble entrada y registro de salidas.
3. Reservas, capacidad máxima y promoción automática en lista de espera FIFO.
4. Eliminación lógica (Soft Delete) y preservación de relaciones históricas.
5. Cálculo dinámico de aforo en tiempo real.
"""

from django.test import TestCase, Client
from django.utils import timezone
from django.contrib.auth.models import User, Group
from datetime import timedelta, time
from decimal import Decimal

from gestion.models import (
    Socio, Plan, Membresia, Pago, Profesor,
    Clase, Reserva, RegistroAcceso
)
from gestion.services import (
    validar_y_registrar_acceso,
    gestionar_reserva_clase,
    cancelar_reserva_clase,
    calcular_aforo_actual
)
from django.core.management import call_command
from django.core.management.base import CommandError
from io import StringIO
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature


class RevisionPlanesTestCase(TestCase):
    def test_incluye_membresias_historicas_sin_modificar_datos(self):
        plan = Plan.objects.create(nombre='Plan prueba', precio=25000, permite_clases=True)
        socio = Socio.objects.create(rut='12345678-5', nombres='Persona', apellidos='Prueba', email='prueba@example.com', telefono='123456789')
        hoy = timezone.localdate()
        membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy + timedelta(days=29))
        Membresia.all_objects.filter(pk=membresia.pk).update(clases_contratadas=None, limite_clases_registrado=False, activo=False)
        salida = StringIO()
        call_command('revisar_planes', stdout=salida)
        self.assertIn('Membresías asociadas: 1', salida.getvalue())
        self.assertIn('Sin registro propio de clases: 1', salida.getvalue())
        membresia.refresh_from_db()
        self.assertIsNone(membresia.clases_contratadas)
        self.assertFalse(membresia.activo)


class CargaDemoTestCase(TestCase):
    def test_carga_no_cambia_cuentas_y_no_repite_datos(self):
        user = User.objects.create_superuser('admin', 'admin@example.org', 'clave-privada')
        call_command('cargar_datos_demo', stdout=StringIO())
        user.refresh_from_db()
        self.assertTrue(user.check_password('clave-privada'))
        self.assertEqual(Socio.all_objects.count(), 13)
        self.assertEqual(set(Plan.objects.values_list('nombre', 'duracion_dias', 'precio', 'permite_clases', 'limite_clases', 'dias_congelamiento')), {
            ('Plan Básico', 30, Decimal('25000'), False, None, 5),
            ('Plan Plus', 30, Decimal('35000'), True, 8, 7),
            ('Plan Premium', 30, Decimal('45000'), True, None, 10),
            ('Plan Plus Trimestral', 90, Decimal('99000'), True, 24, 15),
        })
        for membresia in Membresia.all_objects.select_related('plan'):
            self.assertEqual((membresia.fecha_fin - membresia.fecha_inicio).days + 1, membresia.plan.duracion_dias)
            self.assertEqual(membresia.dias_congelamiento_contratados, membresia.plan.dias_congelamiento)
        for clase in Clase.objects.all():
            self.assertIsNotNone(clase.actividad)
            self.assertIsNotNone(clase.sala_asignada)
            self.assertLessEqual(clase.capacidad_maxima, clase.sala_asignada.capacidad)
            self.assertGreater(clase.fecha, timezone.localdate())
        accesos = RegistroAcceso.objects.count()
        with self.assertRaises(CommandError):
            call_command('cargar_datos_demo', stdout=StringIO())
        self.assertEqual(RegistroAcceso.objects.count(), accesos)


class ReservasSimultaneasTestCase(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_editar_cupo_y_reservar_simultaneamente(self):
        hoy = timezone.localdate()
        plan = Plan.objects.create(nombre='Plan carrera', duracion_dias=30, precio=10000, permite_clases=True)
        profesor = Profesor.objects.create(nombres='Profesor', apellidos='Prueba', especialidad='Yoga')
        from gestion.models import Sala, Actividad
        actividad = Actividad.objects.create(nombre='Yoga carrera')
        sala = Sala.objects.create(nombre='Sala prueba', capacidad=20)
        clase = Clase.objects.create(actividad=actividad, sala_asignada=sala, nombre='Clase carrera', profesor=profesor, fecha=hoy + timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=2, sala='Sala prueba')
        socios = []
        for i in range(2):
            socio = Socio.objects.create(rut=f'2000000{i}-1', nombres=f'Persona {i}', apellidos='Prueba', email=f'carrera{i}@example.com', telefono='123456789')
            Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy + timedelta(days=30), estado='ACTIVA')
            socios.append(socio)
        self.assertTrue(gestionar_reserva_clase(clase.pk, socios[0].pk)[0])
        admin = User.objects.create_superuser('admin_carrera', 'admin@example.org', 'clave-prueba')
        barrera = Barrier(2)

        def reservar():
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                return gestionar_reserva_clase(clase.pk, socios[1].pk)[0]
            finally:
                close_old_connections()

        def editar():
            close_old_connections()
            try:
                cliente = Client()
                cliente.force_login(admin)
                barrera.wait(timeout=10)
                return cliente.post(f'/gestion/clases/{clase.pk}/editar/', {'nombre': clase.nombre, 'profesor': profesor.pk, 'fecha': clase.fecha.isoformat(), 'hora_inicio': '10:00', 'hora_fin': '11:00', 'capacidad_maxima': 1, 'sala_asignada': sala.pk, 'actividad': actividad.pk}).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            primera, segunda = executor.submit(reservar), executor.submit(editar)
            self.assertTrue(primera.result(timeout=20))
            self.assertIn(segunda.result(timeout=20), (200, 302))
        clase.refresh_from_db()
        self.assertLessEqual(clase.total_confirmadas(), clase.capacidad_maxima)

    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_solicitudes_para_un_cupo(self):
        hoy = timezone.localdate()
        plan = Plan.objects.create(nombre='Plan simultáneo', duracion_dias=30, precio=10000, permite_clases=True)
        profesor = Profesor.objects.create(nombres='Profesor', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Clase concurrente', profesor=profesor, fecha=hoy + timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=1, sala='Sala prueba')
        socios = []
        for i in range(2):
            socio = Socio.objects.create(rut=f'1000000{i}-1', nombres=f'Socio {i}', apellidos='Prueba', email=f'concurrencia{i}@example.com', telefono='123456789')
            Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy + timedelta(days=30), estado='ACTIVA')
            socios.append(socio.pk)
        barrera = Barrier(2)

        def reservar(socio_id):
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                exito, mensaje, _ = gestionar_reserva_clase(clase.pk, socio_id)
                return exito, mensaje
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(reservar, socios))
        self.assertTrue(all(exito for exito, _ in resultados), resultados)
        self.assertEqual(Reserva.objects.filter(clase=clase, estado='CONFIRMADA').count(), 1)
        self.assertEqual(Reserva.objects.filter(clase=clase, estado='LISTA_ESPERA').count(), 1)


class FitLifeBusinessLogicTestCase(TestCase):
    def test_proteccion_membresia_con_pago_y_reserva(self):
        from django.core.exceptions import ValidationError
        Pago.objects.create(membresia=self.membresia_activa, monto=100, metodo_pago='EFECTIVO')
        self.membresia_activa.plan = self.plan_basico
        with self.assertRaises(ValidationError):
            self.membresia_activa.clean()
        self.membresia_activa.refresh_from_db()
        self.assertTrue(gestionar_reserva_clase(self.clase_spinning.id, self.socio_activo.id)[0])
        self.membresia_activa.estado = 'CONGELADA'
        with self.assertRaises(ValidationError):
            self.membresia_activa.clean()

    def test_api_no_cambia_plan_de_membresia_con_pagos(self):
        usuario = User.objects.create_user('recepcion_api', password='clave-prueba')
        usuario.groups.add(Group.objects.create(name='Recepcion'))
        Pago.objects.create(membresia=self.membresia_activa, monto=100, metodo_pago='EFECTIVO')
        cliente = Client()
        cliente.force_login(usuario)
        response = cliente.patch(f'/api/membresias/{self.membresia_activa.pk}/', data='{"plan": %s}' % self.plan_basico.pk, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.membresia_activa.refresh_from_db()
        self.assertEqual(self.membresia_activa.plan_id, self.plan_plus.id)

    def test_membresia_debe_cubrir_fecha_de_clase(self):
        self.membresia_activa.fecha_fin = self.hoy
        self.membresia_activa.save()
        exito, mensaje, _ = gestionar_reserva_clase(self.clase_spinning.id, self.socio_activo.id)
        self.assertFalse(exito)
        self.assertIn('fecha de la clase', mensaje)

    def test_clase_terminada_no_admite_reservas(self):
        from unittest.mock import patch
        from datetime import datetime
        self.clase_spinning.fecha = self.hoy
        self.clase_spinning.hora_fin = time(10, 0)
        self.clase_spinning.save()
        ahora = timezone.make_aware(datetime.combine(self.hoy, time(11, 0)))
        with patch('gestion.services.timezone.localtime', return_value=ahora):
            exito, mensaje, _ = gestionar_reserva_clase(self.clase_spinning.id, self.socio_activo.id)
        self.assertFalse(exito)
        self.assertIn('ya terminó', mensaje)
    def test_404_personalizado_en_desarrollo(self):
        from django.test import override_settings
        with override_settings(DEBUG=True):
            response = Client().get('/socios/nuevo/ruta-inexistente')
            self.assertEqual(response.status_code, 404)
            self.assertContains(response, 'ruta fuera del circuito', status_code=404)
            self.assertContains(response, 'href="/"', status_code=404)
            self.assertEqual(Client().get('/login').status_code, 301)
            admin = User.objects.create_superuser('admin_404', 'admin404@example.org', 'clave-prueba')
            client = Client()
            client.force_login(admin)
            response = client.get('/socios/999999/editar/')
            self.assertContains(response, 'ruta fuera del circuito', status_code=404)
            self.assertContains(response, 'href="/"', status_code=404)

    def test_filtros_membresia_y_tarjetas_recepcion(self):
        usuario = User.objects.create_user('recepcion_filtros', password='clave-prueba')
        usuario.groups.add(Group.objects.create(name='Recepcion'))
        cliente = Client()
        cliente.force_login(usuario)
        respuesta = cliente.get('/gestion/membresias/', {'q': 'Valentina', 'estado_m': 'VENCIDA', 'plan': self.plan_plus.id})
        self.assertContains(respuesta, 'Valentina')
        self.assertNotContains(respuesta, 'Camila Rojas')
        self.assertContains(cliente.get('/gestion/planes/'), 'plan-card')
        self.assertRedirects(cliente.get('/gestion/pagos/'), '/socios/')
    def test_membresia_marcada_vencida_no_se_reactiva_sola(self):
        self.membresia_activa.estado = 'VENCIDA'
        self.membresia_activa.save()
        self.membresia_activa.actualizar_estado()
        self.membresia_activa.refresh_from_db()
        self.assertEqual(self.membresia_activa.estado, 'VENCIDA')
        self.assertFalse(validar_y_registrar_acceso(self.socio_activo.codigo_qr)['autorizado'])

    def test_plan_desactivado_no_afecta_acceso_contratado(self):
        self.plan_plus.soft_delete()
        self.assertTrue(validar_y_registrar_acceso(self.socio_activo.codigo_qr)['autorizado'])
    def test_anulacion_pago_solo_admin_y_excluye_ingresos(self):
        from gestion.models import SolicitudAnulacionPago
        admin = User.objects.create_superuser('admin_pago', 'admin@example.org', 'clave-prueba')
        recepcion = User.objects.create_user('recepcion_pago', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        pago = Pago.objects.create(membresia=self.membresia_activa, monto=40000, metodo_pago='EFECTIVO')
        client = Client()
        client.force_login(recepcion)
        client.post(f'/pagos/{pago.pk}/solicitar-anulacion/', {'motivo': 'Error en monto'})
        solicitud = SolicitudAnulacionPago.objects.get(pago=pago)
        client.post(f'/solicitudes/pagos/{solicitud.pk}/resolver/', {'decision': 'APROBADA'})
        pago.refresh_from_db()
        self.assertFalse(pago.anulado)
        client.force_login(admin)
        client.post(f'/solicitudes/pagos/{solicitud.pk}/resolver/', {'decision': 'APROBADA'})
        pago.refresh_from_db()
        self.assertTrue(pago.anulado)
        self.assertEqual(pago.anulado_por, admin)
        self.assertEqual(pago.motivo_anulacion, 'Error en monto')
        self.assertEqual(client.get('/admin-dashboard/').context['ingresos_hoy'], 0)
    def test_promocion_omite_membresia_congelada(self):
        _, _, primera = gestionar_reserva_clase(self.clase_spinning.id, self.socio_activo.id)
        gestionar_reserva_clase(self.clase_spinning.id, self.socio_extra1.id)
        _, _, espera = gestionar_reserva_clase(self.clase_spinning.id, self.socio_extra2.id)
        membresia = self.socio_extra2.membresia_vigente()
        membresia.estado = 'CONGELADA'
        membresia.save()
        exito, mensaje = cancelar_reserva_clase(primera.id)
        espera.refresh_from_db()
        self.assertTrue(exito)
        self.assertEqual(espera.estado, 'CANCELADA')
        self.assertIn('membresía no habilitada', mensaje)

    def test_reserva_clase_fecha_pasada(self):
        self.clase_spinning.fecha = self.hoy - timedelta(days=1)
        self.clase_spinning.save()
        exito, mensaje, _ = gestionar_reserva_clase(self.clase_spinning.id, self.socio_activo.id)
        self.assertFalse(exito)
        self.assertIn('fecha pasada', mensaje)

    def test_estado_membresia_vencida_por_fecha(self):
        self.membresia_activa.fecha_fin = self.hoy - timedelta(days=1)
        self.membresia_activa.estado = 'ACTIVA'
        self.assertEqual(self.membresia_activa.estado_actual_display, 'Vencida')

    def setUp(self):
        """Configuración del entorno de pruebas con datos base."""
        self.hoy = timezone.localdate()

        # 1. Crear Planes
        self.plan_plus = Plan.objects.create(
            nombre="Plan Plus Test",
            descripcion="Con clases",
            duracion_dias=30,
            precio=Decimal("40000"),
            permite_clases=True
        )
        self.plan_basico = Plan.objects.create(
            nombre="Plan Básico Test",
            descripcion="Sin clases",
            duracion_dias=30,
            precio=Decimal("25000"),
            permite_clases=False
        )

        # 2. Crear Profesor y Clase
        self.profesor = Profesor.objects.create(
            nombres="Carlos",
            apellidos="Morales",
            especialidad="Spinning",
            email="prof.carlos@fitlife.cl"
        )
        self.clase_spinning = Clase.objects.create(
            nombre="Spinning Test",
            profesor=self.profesor,
            fecha=self.hoy + timedelta(days=1),
            hora_inicio=time(9, 0),
            hora_fin=time(9, 50),
            capacidad_maxima=2,  # Capacidad reducida a 2 para probar lista de espera
            sala="Sala 1"
        )

        # 3. Socios para diferentes escenarios
        self.socio_activo = Socio.objects.create(
            rut="18.111.111-1",
            nombres="Camila",
            apellidos="Rojas",
            email="camila.test@fitlife.cl",
            telefono="+56911112222",
            codigo_qr="FL-TEST-ACTIVO"
        )
        self.membresia_activa = Membresia.objects.create(
            socio=self.socio_activo,
            plan=self.plan_plus,
            fecha_inicio=self.hoy - timedelta(days=5),
            fecha_fin=self.hoy + timedelta(days=25),
            estado="ACTIVA"
        )

        self.socio_vencido = Socio.objects.create(
            rut="18.222.222-2",
            nombres="Valentina",
            apellidos="Morales",
            email="valentina.test@fitlife.cl",
            telefono="+56922223333",
            codigo_qr="FL-TEST-VENCIDO"
        )
        self.membresia_vencida = Membresia.objects.create(
            socio=self.socio_vencido,
            plan=self.plan_plus,
            fecha_inicio=self.hoy - timedelta(days=40),
            fecha_fin=self.hoy - timedelta(days=10),
            estado="VENCIDA"
        )

        self.socio_congelado = Socio.objects.create(
            rut="18.333.333-3",
            nombres="Benjamín",
            apellidos="Soto",
            email="benjamin.test@fitlife.cl",
            telefono="+56933334444",
            codigo_qr="FL-TEST-CONGELADO"
        )
        self.membresia_congelada = Membresia.objects.create(
            socio=self.socio_congelado,
            plan=self.plan_plus,
            fecha_inicio=self.hoy - timedelta(days=10),
            fecha_fin=self.hoy + timedelta(days=20),
            estado="CONGELADA",
            motivo_congelacion="Reposo médico"
        )

        self.socio_eliminado = Socio.objects.create(
            rut="18.444.444-4",
            nombres="Nicolás",
            apellidos="Tapia",
            email="nicolas.test@fitlife.cl",
            telefono="+56944445555",
            codigo_qr="FL-TEST-ELIMINADO",
            activo=False
        )

        # Socios adicionales para probar cupos de clase
        self.socio_extra1 = Socio.objects.create(
            rut="18.555.555-5", nombres="Matías", apellidos="Fuentes",
            email="matias.test@fitlife.cl", telefono="+56955556666",
            codigo_qr="FL-TEST-EXTRA1"
        )
        Membresia.objects.create(
            socio=self.socio_extra1, plan=self.plan_plus,
            fecha_inicio=self.hoy, fecha_fin=self.hoy + timedelta(days=30), estado="ACTIVA"
        )

        self.socio_extra2 = Socio.objects.create(
            rut="18.666.666-6", nombres="Sofía", apellidos="Castro",
            email="sofia.test@fitlife.cl", telefono="+56966667777",
            codigo_qr="FL-TEST-EXTRA2"
        )
        Membresia.objects.create(
            socio=self.socio_extra2, plan=self.plan_plus,
            fecha_inicio=self.hoy, fecha_fin=self.hoy + timedelta(days=30), estado="ACTIVA"
        )

    # --------------------------------------------------------------------------
    # Prueba 1: Control de Acceso por QR y Reglas de Membresía
    # --------------------------------------------------------------------------
    def test_acceso_qr_socio_activo_permitido(self):
        """Un socio con membresía activa debe recibir acceso PERMITIDO y latencia registrada."""
        res = validar_y_registrar_acceso(self.socio_activo.codigo_qr, tipo="ENTRADA")
        self.assertTrue(res['autorizado'])
        self.assertEqual(res['tipo'], "ENTRADA")
        self.assertGreater(res['tiempo_respuesta_ms'], 0.0)

        # Comprobar registro en base de datos
        registro = RegistroAcceso.objects.get(id=res['registro_id'])
        self.assertEqual(registro.resultado, "PERMITIDO")
        self.assertEqual(registro.socio, self.socio_activo)

    def test_acceso_qr_socio_vencido_denegado(self):
        """Un socio con membresía vencida debe ser DENEGADO con explicación clara."""
        res = validar_y_registrar_acceso(self.socio_vencido.codigo_qr, tipo="ENTRADA")
        self.assertFalse(res['autorizado'])
        self.assertIn("VENCIDA", res['motivo'])

    def test_acceso_qr_socio_congelado_denegado(self):
        """Un socio con membresía congelada debe ser DENEGADO con su motivo."""
        res = validar_y_registrar_acceso(self.socio_congelado.codigo_qr, tipo="ENTRADA")
        self.assertFalse(res['autorizado'])
        self.assertIn("CONGELADA", res['motivo'])

    def test_acceso_qr_socio_eliminado_denegado(self):
        """Un socio dado de baja lógica debe ser DENEGADO."""
        res = validar_y_registrar_acceso(self.socio_eliminado.codigo_qr, tipo="ENTRADA")
        self.assertFalse(res['autorizado'])

    # --------------------------------------------------------------------------
    # Prueba 2: Consistencia de Entradas y Salidas (Evitar Doble Entrada)
    # --------------------------------------------------------------------------
    def test_prevencion_doble_entrada_consecutiva(self):
        """No debe permitirse una segunda entrada si no hay salida previa registrada."""
        # Primera entrada: Exitosa
        res1 = validar_y_registrar_acceso(self.socio_activo.codigo_qr, tipo="ENTRADA")
        self.assertTrue(res1['autorizado'])
        self.assertTrue(self.socio_activo.esta_dentro())

        # Segunda entrada consecutiva: Debe denegarse por inconsistencia
        res2 = validar_y_registrar_acceso(self.socio_activo.codigo_qr, tipo="ENTRADA")
        self.assertFalse(res2['autorizado'])
        self.assertIn("entrada activa sin salida previa", res2['motivo'])

        # Registro de salida: Debe permitirse
        res3 = validar_y_registrar_acceso(self.socio_activo.codigo_qr, tipo="SALIDA")
        self.assertTrue(res3['autorizado'])
        self.assertFalse(self.socio_activo.esta_dentro())

    # --------------------------------------------------------------------------
    # Prueba 3: Reservas, Sobrecupo y Lista de Espera FIFO
    # --------------------------------------------------------------------------
    def test_cupos_clase_y_promocion_automatica_lista_espera(self):
        """
        Verifica que al llenarse la clase el socio entra a LISTA_ESPERA,
        y al cancelarse un cupo confirmado, se promueve automáticamente al primero en espera (FIFO).
        """
        # Capacidad de la clase es 2
        # Reserva 1: Camila Rojas -> CONFIRMADA
        exito1, msg1, r1 = gestionar_reserva_clase(self.clase_spinning.id, self.socio_activo.id)
        self.assertTrue(exito1)
        self.assertEqual(r1.estado, 'CONFIRMADA')

        # Reserva 2: Matías Fuentes -> CONFIRMADA (Clase llena: 2/2)
        exito2, msg2, r2 = gestionar_reserva_clase(self.clase_spinning.id, self.socio_extra1.id)
        self.assertTrue(exito2)
        self.assertEqual(r2.estado, 'CONFIRMADA')
        self.assertTrue(self.clase_spinning.esta_llena())

        # Reserva 3: Sofía Castro -> LISTA DE ESPERA (Posición #1)
        exito3, msg3, r3 = gestionar_reserva_clase(self.clase_spinning.id, self.socio_extra2.id)
        self.assertTrue(exito3)
        self.assertEqual(r3.estado, 'LISTA_ESPERA')
        self.assertIn("LISTA DE ESPERA", msg3)

        # Ahora Camila (r1) cancela su cupo confirmado
        exito_cancel, msg_cancel = cancelar_reserva_clase(r1.id)
        self.assertTrue(exito_cancel)

        # Comprobar que Sofía (r3) fue ascendida automáticamente a CONFIRMADA
        r3.refresh_from_db()
        self.assertEqual(r3.estado, 'CONFIRMADA')
        self.assertIn("asignado automáticamente a Sofía Castro", msg_cancel)

    # --------------------------------------------------------------------------
    # Prueba 4: Eliminación Lógica (Soft Delete)
    # --------------------------------------------------------------------------
    def test_eliminacion_logica_preserva_historial(self):
        """Al dar de baja lógica a un socio, no se borra físicamente y se oculta de las consultas habituales."""
        total_inicial_activos = Socio.objects.count()

        # Dar de baja lógica a Camila
        self.socio_activo.soft_delete()

        # En consultas habituales (ActiveManager) ya no aparece
        self.assertEqual(Socio.objects.count(), total_inicial_activos - 1)
        self.assertFalse(Socio.objects.filter(id=self.socio_activo.id).exists())

        # Pero en all_objects o base de datos relacional sigue existiendo para auditoría
        self.assertTrue(Socio.all_objects.filter(id=self.socio_activo.id).exists())
        self.assertIsNotNone(self.socio_activo.fecha_eliminacion)

        # Restaurar
        self.socio_activo.restore()
        self.assertTrue(Socio.objects.filter(id=self.socio_activo.id).exists())

    # --------------------------------------------------------------------------
    # Prueba 5: Cálculo Dinámico de Aforo
    # --------------------------------------------------------------------------
    def test_calculo_aforo_actual(self):
        """Verifica que el aforo refleje con exactitud la cantidad de personas adentro."""
        # Nadie adentro inicialmente
        aforo0 = calcular_aforo_actual()
        self.assertEqual(aforo0['personas_dentro'], 0)

        # Camila entra
        validar_y_registrar_acceso(self.socio_activo.codigo_qr, tipo="ENTRADA")
        aforo1 = calcular_aforo_actual()
        self.assertEqual(aforo1['personas_dentro'], 1)

        # Camila sale
        validar_y_registrar_acceso(self.socio_activo.codigo_qr, tipo="SALIDA")
        aforo2 = calcular_aforo_actual()
        self.assertEqual(aforo2['personas_dentro'], 0)



class CalculoMembresiaFormTestCase(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(nombre='Plan de prueba', duracion_dias=30, precio=35000, permite_clases=True)
        self.socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='camila@example.org', telefono='123456789')

    def test_calcula_fecha_inclusiva_y_descarta_fecha_manipulada(self):
        from gestion.forms import MembresiaForm
        inicio = timezone.localdate()
        form = MembresiaForm({'socio': self.socio.pk, 'plan': self.plan.pk, 'fecha_inicio': inicio.isoformat(), 'fecha_fin': '2030-01-01', 'estado': 'ACTIVA'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().fecha_fin, inicio + timedelta(days=29))

    def test_editar_membresia_existente_conserva_su_fecha(self):
        from datetime import date
        from gestion.forms import MembresiaForm
        membresia = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=date(2026, 11, 1), fecha_fin=date(2026, 12, 8), estado='ACTIVA')
        form = MembresiaForm({'socio': self.socio.pk, 'plan': self.plan.pk, 'fecha_inicio': '2026-11-01', 'fecha_fin': '2026-12-08', 'estado': 'ACTIVA'}, instance=membresia)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().fecha_fin, date(2026, 12, 8))

    def test_no_permite_contratar_plan_desactivado(self):
        from gestion.forms import MembresiaForm
        self.plan.activo = False
        self.plan.save()
        form = MembresiaForm({'socio': self.socio.pk, 'plan': self.plan.pk, 'fecha_inicio': '2026-11-01', 'estado': 'ACTIVA'})
        self.assertFalse(form.is_valid())
        self.assertIn('plan', form.errors)

class PrecioContratadoTestCase(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(nombre='Plus precio', duracion_dias=30, precio=35000, permite_clases=True)
        self.socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='camila@example.org', telefono='123456789')
        self.membresia = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=timezone.localdate(), fecha_fin=timezone.localdate()+timedelta(days=29))

    def test_precio_se_conserva_y_pago_usa_importe_contratado(self):
        from gestion.forms import PagoForm
        self.plan.precio = 45000
        self.plan.save()
        self.membresia.refresh_from_db()
        self.assertEqual(self.membresia.precio_contratado, Decimal('35000'))
        self.assertIn('data-precio="35000"', str(PagoForm()['membresia']))
        form = PagoForm({'membresia': self.membresia.pk, 'monto': '1', 'metodo_pago': 'TARJETA_DEBITO'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().monto, Decimal('35000'))

    def test_membresia_antigua_solicita_monto_manual(self):
        from gestion.forms import PagoForm
        Membresia.objects.filter(pk=self.membresia.pk).update(precio_contratado=None)
        form = PagoForm({'membresia': self.membresia.pk, 'metodo_pago': 'TARJETA_CREDITO'})
        self.assertFalse(form.is_valid())
        self.assertIn('monto', form.errors)
        form = PagoForm({'membresia': self.membresia.pk, 'monto': '32000', 'metodo_pago': 'TARJETA_CREDITO'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().monto, Decimal('32000'))


class BeneficiosContratadosTestCase(TestCase):
    def test_clases_contratadas_se_conservan_si_cambia_plan(self):
        plan = Plan.objects.create(nombre='Clases originales', duracion_dias=30, precio=35000, permite_clases=True)
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='camila@example.org', telefono='123456789')
        hoy = timezone.localdate()
        membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=29))
        plan.permite_clases = False
        plan.save()
        membresia.refresh_from_db()
        self.assertTrue(membresia.incluye_clases)
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Pérez', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=hoy+timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=5)
        self.assertTrue(gestionar_reserva_clase(clase.pk, socio.pk)[0])

    def test_plan_sin_clases_no_gana_beneficio_si_plan_cambia(self):
        plan = Plan.objects.create(nombre='Sin clases', duracion_dias=30, precio=25000, permite_clases=False)
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='camila@example.org', telefono='123456789')
        hoy = timezone.localdate()
        membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=29))
        plan.permite_clases = True
        plan.save()
        membresia.refresh_from_db()
        self.assertFalse(membresia.incluye_clases)


class FechasContratacionTestCase(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.plan = Plan.objects.create(nombre='Fechas', duracion_dias=30, precio=35000, permite_clases=True)
        self.socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='camila@example.org', telefono='123456789')

    def formulario(self, fecha):
        from gestion.forms import MembresiaForm
        return MembresiaForm({'socio': self.socio.pk, 'plan': self.plan.pk, 'fecha_inicio': fecha.isoformat(), 'estado': 'ACTIVA'})

    def test_primera_contratacion_admite_hasta_treinta_dias(self):
        self.assertTrue(self.formulario(self.hoy + timedelta(days=30)).is_valid())
        self.assertFalse(self.formulario(self.hoy + timedelta(days=31)).is_valid())
        self.assertFalse(self.formulario(self.hoy - timedelta(days=1)).is_valid())

    def test_renovacion_sigue_termino_y_no_admite_otra_futura(self):
        actual = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.hoy, fecha_fin=self.hoy+timedelta(days=29))
        self.assertFalse(self.formulario(self.hoy+timedelta(days=1)).is_valid())
        form = self.formulario(actual.fecha_fin+timedelta(days=1))
        self.assertTrue(form.is_valid(), form.errors)
        programada = form.save()
        self.assertEqual(programada.estado_actual_display, 'Programada')
        self.assertFalse(programada.puede_ingresar()[0])
        self.assertFalse(self.formulario(programada.fecha_fin+timedelta(days=1)).is_valid())


class MembresiaFuturaReservaTestCase(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.plan_actual = Plan.objects.create(nombre='Actual sin clases', duracion_dias=30, precio=25000, permite_clases=False)
        self.plan_futuro = Plan.objects.create(nombre='Futuro con clases', duracion_dias=30, precio=35000, permite_clases=True)
        self.socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='camila@example.org', telefono='123456789')
        self.actual = Membresia.objects.create(socio=self.socio, plan=self.plan_actual, fecha_inicio=self.hoy, fecha_fin=self.hoy+timedelta(days=1))
        self.futura = Membresia.objects.create(socio=self.socio, plan=self.plan_futuro, fecha_inicio=self.hoy+timedelta(days=1), fecha_fin=self.hoy+timedelta(days=30))
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Pérez', especialidad='Yoga')
        self.clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=self.hoy+timedelta(days=2), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=1)

    def test_reserva_segun_membresia_que_cubre_fecha(self):
        exito, mensaje, reserva = gestionar_reserva_clase(self.clase.pk, self.socio.pk)
        self.assertTrue(exito, mensaje)
        self.assertEqual(reserva.estado, 'CONFIRMADA')
        self.assertEqual(self.socio.membresia_vigente(), self.actual)

    def test_no_reserva_fuera_del_periodo(self):
        self.clase.fecha = self.futura.fecha_fin+timedelta(days=1)
        self.clase.save()
        self.assertFalse(gestionar_reserva_clase(self.clase.pk, self.socio.pk)[0])

    def test_lista_espera_promueve_con_membresia_futura(self):
        otro = Socio.objects.create(rut='12345679-3', nombres='Ana', apellidos='Prueba', email='ana@example.org', telefono='123456789')
        Membresia.objects.create(socio=otro, plan=self.plan_futuro, fecha_inicio=self.hoy, fecha_fin=self.hoy+timedelta(days=29))
        _, _, primera = gestionar_reserva_clase(self.clase.pk, otro.pk)
        _, _, espera = gestionar_reserva_clase(self.clase.pk, self.socio.pk)
        self.assertEqual(espera.estado, 'LISTA_ESPERA')
        self.assertTrue(cancelar_reserva_clase(primera.pk)[0])
        espera.refresh_from_db()
        self.assertEqual(espera.estado, 'CONFIRMADA')


class CrucesDeReservaTestCase(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.plan = Plan.objects.create(nombre='Cruces', duracion_dias=30, precio=35000, permite_clases=True)
        self.profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        self.socios = []
        for i in range(3):
            socio = Socio.objects.create(rut=f'1234567{i}-5', nombres=f'Persona {i}', apellidos='Prueba', email=f'persona{i}@example.org', telefono='123456789')
            Membresia.objects.create(socio=socio, plan=self.plan, fecha_inicio=self.hoy, fecha_fin=self.hoy+timedelta(days=29))
            self.socios.append(socio)
        self.clase = Clase.objects.create(nombre='Yoga', profesor=self.profesor, fecha=self.hoy+timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=1)
        self.otra = Clase.objects.create(nombre='Pilates', profesor=self.profesor, fecha=self.clase.fecha, hora_inicio=time(10, 30), hora_fin=time(11, 30), capacidad_maxima=3)

    def test_no_confirma_horarios_superpuestos_pero_permite_clases_consecutivas(self):
        self.assertTrue(gestionar_reserva_clase(self.clase.pk, self.socios[0].pk)[0])
        exito, mensaje, _ = gestionar_reserva_clase(self.otra.pk, self.socios[0].pk)
        self.assertFalse(exito)
        self.assertIn('horario', mensaje)
        self.otra.hora_inicio = time(11)
        self.otra.save()
        self.assertTrue(gestionar_reserva_clase(self.otra.pk, self.socios[0].pk)[0])

    def test_espera_con_cruce_pasa_al_siguiente_socio(self):
        _, _, primera = gestionar_reserva_clase(self.clase.pk, self.socios[0].pk)
        self.assertTrue(gestionar_reserva_clase(self.otra.pk, self.socios[1].pk)[0])
        _, _, espera1 = gestionar_reserva_clase(self.clase.pk, self.socios[1].pk)
        _, _, espera2 = gestionar_reserva_clase(self.clase.pk, self.socios[2].pk)
        self.assertEqual(espera1.estado, 'LISTA_ESPERA')
        self.assertTrue(cancelar_reserva_clase(primera.pk)[0])
        espera1.refresh_from_db()
        espera2.refresh_from_db()
        self.assertEqual(espera1.estado, 'CRUCE_HORARIO')
        self.assertEqual(espera2.estado, 'CONFIRMADA')


class VentanaReservaTestCase(TestCase):
    def setUp(self):
        from datetime import datetime
        self.hoy = timezone.localdate()
        self.inicio = timezone.make_aware(datetime.combine(self.hoy+timedelta(days=4), time(10)))
        self.plan = Plan.objects.create(nombre='Ventana', duracion_dias=30, precio=35000, permite_clases=True)
        self.profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        self.clase = Clase.objects.create(nombre='Yoga', profesor=self.profesor, fecha=self.inicio.date(), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=1)
        self.socios = []
        for i in range(3):
            socio = Socio.objects.create(rut=f'1234567{i}-5', nombres=f'Persona {i}', apellidos='Prueba', email=f'ventana{i}@example.org', telefono='123456789')
            Membresia.objects.create(socio=socio, plan=self.plan, fecha_inicio=self.hoy, fecha_fin=self.hoy+timedelta(days=29))
            self.socios.append(socio)

    def reservar_en(self, momento, socio):
        from unittest.mock import patch
        with patch('gestion.services.timezone.localtime', return_value=momento):
            return gestionar_reserva_clase(self.clase.pk, socio.pk)

    def test_limites_exactos_de_apertura_y_cierre(self):
        self.assertFalse(self.reservar_en(self.inicio-timedelta(hours=72, seconds=1), self.socios[0])[0])
        self.assertTrue(self.reservar_en(self.inicio-timedelta(hours=72), self.socios[0])[0])
        self.assertTrue(self.reservar_en(self.inicio-timedelta(minutes=15, seconds=1), self.socios[1])[0])
        self.assertFalse(self.reservar_en(self.inicio-timedelta(minutes=15), self.socios[2])[0])

    def test_ultimo_cuarto_hora_solo_promueve_quien_esta_dentro(self):
        from unittest.mock import patch
        _, _, confirmada = self.reservar_en(self.inicio-timedelta(hours=1), self.socios[0])
        _, _, espera_fuera = self.reservar_en(self.inicio-timedelta(hours=1), self.socios[1])
        _, _, espera_dentro = self.reservar_en(self.inicio-timedelta(hours=1), self.socios[2])
        RegistroAcceso.objects.create(socio=self.socios[2], tipo='ENTRADA', resultado='PERMITIDO', motivo='Prueba')
        with patch('gestion.services.timezone.localtime', return_value=self.inicio-timedelta(minutes=10)):
            self.assertTrue(cancelar_reserva_clase(confirmada.pk)[0])
        espera_fuera.refresh_from_db()
        espera_dentro.refresh_from_db()
        self.assertEqual(espera_fuera.estado, 'LISTA_ESPERA')
        self.assertEqual(espera_dentro.estado, 'CONFIRMADA')

    def test_no_promueve_una_vez_iniciada_la_clase(self):
        from unittest.mock import patch
        _, _, confirmada = self.reservar_en(self.inicio-timedelta(hours=1), self.socios[0])
        _, _, espera = self.reservar_en(self.inicio-timedelta(hours=1), self.socios[1])
        with patch('gestion.services.timezone.localtime', return_value=self.inicio):
            self.assertTrue(cancelar_reserva_clase(confirmada.pk)[0])
        espera.refresh_from_db()
        self.assertEqual(espera.estado, 'SIN_CUPO')


class CierreListaEsperaTestCase(TestCase):
    def test_cierre_es_idempotente_y_respeta_hora(self):
        from datetime import datetime
        from gestion.services import cerrar_listas_iniciadas
        hoy = timezone.localdate()
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='camila@example.org', telefono='123456789')
        clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=hoy, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=1)
        reserva = Reserva.objects.create(clase=clase, socio=socio, estado='LISTA_ESPERA')
        inicio = timezone.make_aware(datetime.combine(hoy, time(10)))
        self.assertEqual(cerrar_listas_iniciadas(inicio-timedelta(seconds=1)), 0)
        self.assertEqual(cerrar_listas_iniciadas(inicio), 1)
        reserva.refresh_from_db()
        self.assertEqual(reserva.estado, 'SIN_CUPO')
        self.assertEqual(cerrar_listas_iniciadas(inicio), 0)


class NotificacionesReservaTestCase(TestCase):
    def test_cerrar_espera_genera_un_aviso_sin_duplicados(self):
        from datetime import datetime
        from gestion.services import cerrar_listas_iniciadas
        from gestion.models import NotificacionSocio
        hoy = timezone.localdate()
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='camila@example.org', telefono='123456789')
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=hoy, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=1)
        reserva = Reserva.objects.create(clase=clase, socio=socio, estado='LISTA_ESPERA')
        inicio = timezone.make_aware(datetime.combine(hoy, time(10)))
        cerrar_listas_iniciadas(inicio)
        cerrar_listas_iniciadas(inicio)
        avisos = NotificacionSocio.objects.filter(reserva=reserva)
        self.assertEqual(avisos.count(), 1)
        self.assertEqual(avisos.get().tipo, 'SIN_CUPO')
        self.assertFalse(avisos.get().leida)

    def test_espera_promovida_genera_aviso(self):
        from gestion.models import NotificacionSocio
        hoy = timezone.localdate()
        plan = Plan.objects.create(nombre='Avisos', duracion_dias=30, precio=35000, permite_clases=True)
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=hoy+timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=1)
        reservas = []
        for i in range(2):
            socio = Socio.objects.create(rut=f'1234567{i}-5', nombres='Persona', apellidos=str(i), email=f'aviso{i}@example.org', telefono='123456789')
            Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=29))
            reservas.append(gestionar_reserva_clase(clase.pk, socio.pk)[2])
        self.assertTrue(cancelar_reserva_clase(reservas[0].pk)[0])
        self.assertTrue(NotificacionSocio.objects.filter(reserva=reservas[1], tipo='CUPO_ASIGNADO').exists())


class LimiteClasesPlanTestCase(TestCase):
    def test_formulario_valida_limite(self):
        from gestion.forms import PlanForm
        base = {'nombre': 'Plan prueba', 'duracion_dias': 30, 'precio': 35000}
        self.assertFalse(PlanForm({**base, 'limite_clases': 8}).is_valid())
        self.assertFalse(PlanForm({**base, 'permite_clases': 'on', 'limite_clases': 0}).is_valid())
        self.assertTrue(PlanForm({**base, 'permite_clases': 'on', 'limite_clases': 8}).is_valid())
        self.assertTrue(PlanForm({**base, 'permite_clases': 'on'}).is_valid())

    def test_membresia_conserva_limite_al_cambiar_plan(self):
        plan = Plan.objects.create(nombre='Limitado', duracion_dias=30, precio=35000, permite_clases=True, limite_clases=8, dias_congelamiento=7)
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='camila@example.org', telefono='123456789')
        hoy = timezone.localdate()
        membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=29))
        plan.limite_clases = 12
        plan.save()
        membresia.refresh_from_db()
        self.assertTrue(membresia.limite_clases_registrado)
        self.assertEqual(membresia.limite_clases_contratado, 8)
        self.assertEqual(membresia.dias_congelamiento_contratados, 7)
        plan.dias_congelamiento = 10
        plan.save(update_fields=['dias_congelamiento'])
        membresia.refresh_from_db()
        self.assertEqual(membresia.dias_congelamiento_contratados, 7)


class SolicitudCongelamientoTestCase(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user('socia_pausa', password='clave-prueba')
        self.socio = Socio.objects.create(usuario=self.usuario, rut='12345678-5', nombres='Camila', apellidos='Prueba', email='pausa@example.org', telefono='123456789')
        self.plan = Plan.objects.create(nombre='Plan pausa', duracion_dias=30, precio=35000, dias_congelamiento=7)
        self.hoy = timezone.localdate()
        self.membresia = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.hoy, fecha_fin=self.hoy + timedelta(days=29))

    def test_solicitud_pendiente_cuenta_dias_sin_alterar_membresia(self):
        from gestion.services import solicitar_congelamiento_socio
        solicitud = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=6))
        self.assertEqual(solicitud.dias_solicitados, 7)
        self.assertEqual(solicitud.estado, 'PENDIENTE')
        self.membresia.refresh_from_db()
        self.assertEqual(self.membresia.estado, 'ACTIVA')
        self.assertEqual(self.membresia.fecha_fin, self.hoy + timedelta(days=29))

    def test_saldo_congelamiento_considera_pausas_y_renovacion_independiente(self):
        from gestion.services import solicitar_congelamiento_socio, aprobar_congelamiento_sin_conflictos
        pausa = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=2))
        self.assertEqual(self.membresia.dias_congelamiento_disponibles, 7)
        admin = User.objects.create_superuser('admin_saldo_pausa', 'saldo-pausa@example.org', 'clave-prueba')
        aprobar_congelamiento_sin_conflictos(pausa.pk, admin)
        self.assertEqual(self.membresia.dias_congelamiento_disponibles, 4)
        pausa.refresh_from_db()
        pausa.fecha_reactivacion = self.hoy + timedelta(days=1)
        pausa.save(update_fields=['fecha_reactivacion'])
        self.assertEqual(self.membresia.dias_congelamiento_disponibles, 6)
        futura = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.hoy + timedelta(days=40), fecha_fin=self.hoy + timedelta(days=69))
        self.assertEqual(futura.dias_congelamiento_disponibles, 7)
        antigua = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.hoy - timedelta(days=60), fecha_fin=self.hoy - timedelta(days=31))
        Membresia.objects.filter(pk=antigua.pk).update(dias_congelamiento_contratados=None)
        antigua.refresh_from_db()
        self.assertIsNone(antigua.dias_congelamiento_disponibles)

    def test_pausa_se_muestra_en_portal_filtros_y_metricas(self):
        from gestion.services import solicitar_congelamiento_socio, aprobar_congelamiento_sin_conflictos
        pausa = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=2))
        admin = User.objects.create_superuser('admin_estado_pausa', 'estado-pausa@example.org', 'clave-prueba')
        aprobar_congelamiento_sin_conflictos(pausa.pk, admin)
        self.usuario.groups.add(Group.objects.get_or_create(name='Socio')[0])
        cliente = Client()
        cliente.force_login(self.usuario)
        portal = cliente.get('/socio/mi-cuenta/')
        self.assertContains(portal, 'membresía congelada')
        self.assertNotContains(portal, 'No tienes una membresía vigente')
        cliente.force_login(admin)
        activas = cliente.get('/gestion/membresias/?estado_m=ACTIVA')
        congeladas = cliente.get('/gestion/membresias/?estado_m=CONGELADA')
        self.assertNotContains(activas, self.socio.nombre_completo)
        self.assertContains(congeladas, self.socio.nombre_completo)
        self.assertContains(cliente.get('/socios/'), 'Congelada')
        tablero = cliente.get('/admin-dashboard/')
        self.assertEqual(tablero.context['membresias_activas'], 0)
        self.assertEqual(tablero.context['desglose_planes'].get(pk=self.plan.pk).total_activas, 0)

    def test_recepcion_solicita_pausa_y_se_registra_quien_la_creo(self):
        from gestion.models import SolicitudCongelamiento, NotificacionSocio
        recepcion = User.objects.create_user('recepcion_pausa', password='clave-prueba')
        recepcion.groups.add(Group.objects.get_or_create(name='Recepcion')[0])
        cliente = Client()
        cliente.force_login(recepcion)
        ruta = f'/gestion/membresias/{self.membresia.pk}/solicitar-congelamiento/'
        ficha_url = f'/recepcion/membresias/{self.membresia.pk}/ficha/'
        self.assertContains(cliente.get('/gestion/membresias/'), ficha_url)
        self.assertContains(cliente.get(ficha_url), 'Solicitar congelamiento')
        self.assertContains(cliente.get(ruta), self.socio.nombre_completo)
        datos = {'fecha_inicio': self.hoy.isoformat(), 'fecha_fin': (self.hoy + timedelta(days=2)).isoformat(), 'motivo': 'Viaje familiar'}
        self.assertRedirects(cliente.post(ruta, datos), '/gestion/membresias/')
        solicitud = SolicitudCongelamiento.objects.get(membresia=self.membresia)
        self.assertEqual(solicitud.solicitante, recepcion)
        self.assertEqual(solicitud.motivo, 'Viaje familiar')
        admin = User.objects.create_superuser('admin_motivo_pausa', password='clave-prueba')
        cliente.force_login(admin)
        self.assertContains(cliente.get('/solicitudes/congelamiento/'), 'Viaje familiar')
        cliente.force_login(recepcion)
        self.assertEqual(solicitud.estado, 'PENDIENTE')
        self.assertTrue(NotificacionSocio.objects.filter(socio=self.socio, tipo='CONGELAMIENTO_SOLICITADO').exists())
        self.assertContains(cliente.post(ruta, datos), 'Ya tienes una solicitud')
        self.membresia.refresh_from_db()
        self.assertEqual(self.membresia.fecha_fin, self.hoy + timedelta(days=29))

    def test_socio_no_puede_usar_la_vista_de_recepcion(self):
        cliente = Client()
        cliente.force_login(self.usuario)
        ruta = f'/gestion/membresias/{self.membresia.pk}/solicitar-congelamiento/'
        respuesta = cliente.post(ruta, {'fecha_inicio': self.hoy.isoformat(), 'fecha_fin': self.hoy.isoformat()})
        self.assertNotEqual(respuesta.status_code, 200)
        self.assertFalse(self.membresia.solicitudes_congelamiento.exists())

    def test_rechaza_fechas_saldo_otro_usuario_y_duplicados(self):
        from django.core.exceptions import ValidationError
        from gestion.services import solicitar_congelamiento_socio
        otro = User.objects.create_user('otro_pausa', password='clave-prueba')
        casos = [(self.usuario, self.hoy - timedelta(days=1), self.hoy), (self.usuario, self.hoy, self.hoy + timedelta(days=7)), (otro, self.hoy, self.hoy), (self.usuario, self.hoy, self.hoy - timedelta(days=1))]
        for usuario, inicio, fin in casos:
            with self.subTest(inicio=inicio, fin=fin, usuario=usuario), self.assertRaises(ValidationError):
                solicitar_congelamiento_socio(self.membresia.pk, usuario, inicio, fin)
        solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy)
        with self.assertRaises(ValidationError):
            solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy)

    def test_administracion_revisa_impacto_sin_modificar_reservas(self):
        from gestion.services import solicitar_congelamiento_socio
        solicitud = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=6))
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga afectada', profesor=profesor, fecha=self.hoy + timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        reserva = Reserva.objects.create(socio=self.socio, clase=clase, estado='CONFIRMADA', membresia=self.membresia)
        futura = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.membresia.fecha_fin + timedelta(days=1), fecha_fin=self.membresia.fecha_fin + timedelta(days=30))
        clase_futura = Clase.objects.create(nombre='Clase de renovación', profesor=profesor, fecha=futura.fecha_inicio, hora_inicio=time(12), hora_fin=time(13), capacidad_maxima=10)
        Reserva.objects.create(socio=self.socio, clase=clase_futura, estado='CONFIRMADA', membresia=futura)
        admin = User.objects.create_superuser('admin_pausas', 'admin-pausa@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(self.usuario)
        self.assertNotEqual(cliente.get('/solicitudes/congelamiento/').status_code, 200)
        cliente.force_login(admin)
        respuesta = cliente.get('/solicitudes/congelamiento/')
        self.assertContains(respuesta, 'Yoga afectada')
        self.assertContains(respuesta, 'Renovaciones que necesitan revisión')
        self.assertContains(respuesta, 'Plan pausa')
        self.assertContains(respuesta, 'se cancelarán sin descontar clases')
        reserva.refresh_from_db()
        solicitud.refresh_from_db()
        self.assertEqual(reserva.estado, 'CONFIRMADA')
        self.assertEqual(solicitud.estado, 'PENDIENTE')

    def test_admin_rechaza_con_motivo_y_socio_recibe_aviso_una_vez(self):
        from gestion.services import solicitar_congelamiento_socio
        from gestion.models import NotificacionSocio
        solicitud = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=1))
        admin = User.objects.create_superuser('admin_rechazo', 'rechazo@example.org', 'clave-prueba')
        cliente = Client()
        ruta = f'/solicitudes/congelamiento/{solicitud.pk}/rechazar/'
        cliente.force_login(self.usuario)
        self.assertNotEqual(cliente.post(ruta, {'motivo': 'Prueba'}).status_code, 200)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'PENDIENTE')
        cliente.force_login(admin)
        self.assertEqual(cliente.get(ruta).status_code, 405)
        cliente.post(ruta, {'motivo': '  '})
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'PENDIENTE')
        cliente.post(ruta, {'motivo': 'Revisar fechas'})
        cliente.post(ruta, {'motivo': 'Otro motivo'})
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'RECHAZADA')
        self.assertEqual(solicitud.motivo_rechazo, 'Revisar fechas')
        self.assertEqual(solicitud.revisado_por, admin)
        self.assertEqual(NotificacionSocio.objects.filter(socio=self.socio, tipo='CONGELAMIENTO_RECHAZADO').count(), 1)
        self.membresia.refresh_from_db()
        self.assertEqual(self.membresia.estado, 'ACTIVA')

    def test_aprobacion_programa_pausa_y_extiende_una_sola_vez(self):
        from gestion.services import solicitar_congelamiento_socio
        from gestion.models import NotificacionSocio
        inicio = self.hoy + timedelta(days=1)
        solicitud = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, inicio, inicio + timedelta(days=2))
        admin = User.objects.create_superuser('admin_aprueba', 'aprueba@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        ruta = f'/solicitudes/congelamiento/{solicitud.pk}/aprobar/'
        self.assertEqual(cliente.get(ruta).status_code, 405)
        propuesta = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['propuesta']
        cliente.post(ruta, {'propuesta': propuesta})
        cliente.post(ruta, {'propuesta': propuesta})
        solicitud.refresh_from_db()
        self.membresia.refresh_from_db()
        self.assertEqual(solicitud.estado, 'APROBADA')
        self.assertEqual(self.membresia.fecha_fin, self.hoy + timedelta(days=32))
        self.assertIsNotNone(self.socio.membresia_para_fecha(self.hoy))
        self.assertIsNone(self.socio.membresia_para_fecha(inicio))
        self.assertIsNotNone(self.socio.membresia_para_fecha(inicio + timedelta(days=3)))
        self.assertEqual(NotificacionSocio.objects.filter(socio=self.socio, tipo='CONGELAMIENTO_APROBADO').count(), 1)

    def test_aprobacion_no_altera_reservas_o_renovaciones_con_conflicto(self):
        from gestion.services import solicitar_congelamiento_socio
        solicitud = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=2))
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=self.hoy + timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        reserva = Reserva.objects.create(socio=self.socio, clase=clase, estado='CONFIRMADA')
        admin = User.objects.create_superuser('admin_conflictos', 'conflicto@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        ruta = f'/solicitudes/congelamiento/{solicitud.pk}/aprobar/'
        propuesta = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['propuesta']
        reserva.estado = 'CANCELADA'
        reserva.save(update_fields=['estado'])
        respuesta = cliente.post(ruta, {'propuesta': propuesta}, follow=True)
        self.assertContains(respuesta, 'Las reservas afectadas cambiaron')
        futura = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.membresia.fecha_fin + timedelta(days=1), fecha_fin=self.membresia.fecha_fin + timedelta(days=30))
        clase_futura = Clase.objects.create(nombre='Clase de renovación', profesor=profesor, fecha=futura.fecha_inicio, hora_inicio=time(12), hora_fin=time(13), capacidad_maxima=10)
        reserva_futura = Reserva.objects.create(socio=self.socio, clase=clase_futura, estado='CONFIRMADA', membresia=futura)
        propuesta = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['propuesta']
        reserva_futura.estado = 'CANCELADA'
        reserva_futura.save(update_fields=['estado'])
        respuesta = cliente.post(ruta, {'propuesta': propuesta}, follow=True)
        self.assertContains(respuesta, 'Las reservas de la renovación cambiaron')
        solicitud.refresh_from_db()
        self.membresia.refresh_from_db()
        self.assertEqual(solicitud.estado, 'PENDIENTE')
        self.assertEqual(self.membresia.fecha_fin, self.hoy + timedelta(days=29))

    def test_aprobar_pausa_ajusta_renovacion_sin_perder_condiciones(self):
        from gestion.services import solicitar_congelamiento_socio
        from gestion.models import NotificacionSocio
        inicio = self.hoy + timedelta(days=1)
        solicitud = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, inicio, inicio + timedelta(days=2))
        futura = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.membresia.fecha_fin + timedelta(days=1), fecha_fin=self.membresia.fecha_fin + timedelta(days=30))
        inicio_original, fin_original, precio = futura.fecha_inicio, futura.fecha_fin, futura.precio_contratado
        admin = User.objects.create_superuser('admin_renovacion_pausa', 'adminrenueva@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        revision = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]
        self.assertEqual(revision['futuras'][0]['inicio_nuevo'], inicio_original + timedelta(days=3))
        cliente.post(f'/solicitudes/congelamiento/{solicitud.pk}/aprobar/', {'propuesta': revision['propuesta']})
        futura.refresh_from_db()
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'APROBADA')
        self.assertEqual(futura.fecha_inicio, inicio_original + timedelta(days=3))
        self.assertEqual(futura.fecha_fin, fin_original + timedelta(days=3))
        self.assertEqual(futura.precio_contratado, precio)
        self.assertEqual(solicitud.renovacion_inicio_anterior, inicio_original)
        self.assertTrue(NotificacionSocio.objects.filter(socio=self.socio, tipo='RENOVACION_AJUSTADA').exists())

    def test_aprobar_pausa_cancela_reserva_fuera_de_renovacion(self):
        from gestion.services import solicitar_congelamiento_socio
        from gestion.models import NotificacionSocio
        pausa = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=2))
        futura = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.membresia.fecha_fin + timedelta(days=1), fecha_fin=self.membresia.fecha_fin + timedelta(days=30))
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga renovación afectada', profesor=profesor, fecha=futura.fecha_inicio, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        reserva = Reserva.objects.create(socio=self.socio, clase=clase, estado='CONFIRMADA', membresia=futura, consume_clase=True)
        admin = User.objects.create_superuser('admin_ren_reserva', 'ren-reserva@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        revision = cliente.get('/solicitudes/congelamiento/')
        self.assertContains(revision, 'Yoga renovación afectada')
        propuesta = revision.context['revisiones'][0]['propuesta']
        cliente.post(f'/solicitudes/congelamiento/{pausa.pk}/aprobar/', {'propuesta': propuesta})
        pausa.refresh_from_db()
        reserva.refresh_from_db()
        self.assertEqual(pausa.estado, 'APROBADA')
        self.assertEqual(reserva.estado, 'CANCELADA')
        self.assertFalse(reserva.consume_clase)
        self.assertTrue(NotificacionSocio.objects.filter(socio=self.socio, reserva=reserva, tipo='PAUSA_RENUEVA_CANCELA').exists())

    def test_cancelacion_pausa_restablece_renovacion_y_notifica(self):
        from gestion.services import solicitar_congelamiento_socio
        from gestion.models import NotificacionSocio
        inicio = self.hoy + timedelta(days=2)
        solicitud = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, inicio, inicio + timedelta(days=2))
        futura = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.membresia.fecha_fin + timedelta(days=1), fecha_fin=self.membresia.fecha_fin + timedelta(days=30))
        fecha_inicial, fecha_final = futura.fecha_inicio, futura.fecha_fin
        admin = User.objects.create_superuser('admin_retorno_renovacion', 'retorno@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        propuesta = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['propuesta']
        cliente.post(f'/solicitudes/congelamiento/{solicitud.pk}/aprobar/', {'propuesta': propuesta})
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'APROBADA')
        self.usuario.groups.add(Group.objects.get_or_create(name='Socio')[0])
        cliente.force_login(self.usuario)
        ruta = f'/socio/congelamiento/{solicitud.pk}/cancelar-pausa/'
        revision = cliente.get(ruta)
        self.assertContains(revision, 'volvería al')
        cliente.post(ruta, {'propuesta': revision.context['propuesta']})
        futura.refresh_from_db()
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'CANCELADA')
        self.assertEqual((futura.fecha_inicio, futura.fecha_fin), (fecha_inicial, fecha_final))
        self.assertTrue(NotificacionSocio.objects.filter(socio=self.socio, tipo='RENOVACION_RESTAURADA').exists())

    def test_cancelacion_pausa_preserva_reserva_en_nueva_vigencia(self):
        from gestion.services import solicitar_congelamiento_socio
        inicio = self.hoy + timedelta(days=2)
        solicitud = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, inicio, inicio + timedelta(days=2))
        futura = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.membresia.fecha_fin + timedelta(days=1), fecha_fin=self.membresia.fecha_fin + timedelta(days=30))
        admin = User.objects.create_superuser('admin_reserva_renovada', 'reservarenovada@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        propuesta = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['propuesta']
        cliente.post(f'/solicitudes/congelamiento/{solicitud.pk}/aprobar/', {'propuesta': propuesta})
        futura.refresh_from_db()
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga renovación', profesor=profesor, fecha=futura.fecha_inicio, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        reserva = Reserva.objects.create(socio=self.socio, clase=clase, estado='CONFIRMADA', membresia=futura)
        self.usuario.groups.add(Group.objects.get_or_create(name='Socio')[0])
        cliente.force_login(self.usuario)
        ruta = f'/socio/congelamiento/{solicitud.pk}/cancelar-pausa/'
        revision = cliente.get(ruta)
        self.assertNotContains(revision, 'Yoga renovación')
        cliente.post(ruta, {'propuesta': revision.context['propuesta']})
        solicitud.refresh_from_db()
        reserva.refresh_from_db()
        self.assertEqual(solicitud.estado, 'CANCELADA')
        self.assertEqual(reserva.estado, 'CONFIRMADA')

    def test_cancelacion_pausa_cancela_reserva_fuera_de_renovacion(self):
        from gestion.services import solicitar_congelamiento_socio
        from gestion.models import NotificacionSocio
        inicio = self.hoy + timedelta(days=2)
        pausa = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, inicio, inicio + timedelta(days=2))
        futura = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.membresia.fecha_fin + timedelta(days=1), fecha_fin=self.membresia.fecha_fin + timedelta(days=30))
        admin = User.objects.create_superuser('admin_cancelar_renovacion', 'cancelar-ren@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        propuesta = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['propuesta']
        cliente.post(f'/solicitudes/congelamiento/{pausa.pk}/aprobar/', {'propuesta': propuesta})
        futura.refresh_from_db()
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga fuera de vigencia', profesor=profesor, fecha=futura.fecha_fin, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        reserva = Reserva.objects.create(socio=self.socio, clase=clase, estado='CONFIRMADA', membresia=futura, consume_clase=True)
        self.usuario.groups.add(Group.objects.get_or_create(name='Socio')[0])
        cliente.force_login(self.usuario)
        ruta = f'/socio/congelamiento/{pausa.pk}/cancelar-pausa/'
        revision = cliente.get(ruta)
        self.assertContains(revision, 'Yoga fuera de vigencia')
        cliente.post(ruta, {'propuesta': revision.context['propuesta']})
        pausa.refresh_from_db()
        reserva.refresh_from_db()
        self.assertEqual(pausa.estado, 'CANCELADA')
        self.assertEqual(reserva.estado, 'CANCELADA')
        self.assertFalse(reserva.consume_clase)
        self.assertTrue(NotificacionSocio.objects.filter(socio=self.socio, reserva=reserva, tipo='CANCELA_PAUSA_RESERVA').exists())

    def test_reactivacion_anticipada_cuenta_solo_dias_transcurridos(self):
        from unittest.mock import patch
        from gestion.services import solicitar_congelamiento_socio, aprobar_congelamiento_sin_conflictos
        from gestion.models import NotificacionSocio
        pausa = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=6))
        admin = User.objects.create_superuser('admin_reactivacion', 'reactivar@example.org', 'clave-prueba')
        aprobar_congelamiento_sin_conflictos(pausa.pk, admin)
        self.usuario.groups.add(Group.objects.get_or_create(name='Socio')[0])
        cliente = Client()
        with patch('django.utils.timezone.localdate', return_value=self.hoy + timedelta(days=3)):
            self.assertIsNone(self.socio.membresia_vigente())
            cliente.force_login(self.usuario)
            ruta = f'/socio/congelamiento/{pausa.pk}/solicitar-reactivacion/'
            self.assertEqual(cliente.get(ruta).status_code, 405)
            cliente.post(ruta)
            pausa.refresh_from_db()
            self.assertTrue(pausa.reactivacion_pendiente)
            cliente.force_login(admin)
            revision = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['reactivacion']
            cliente.post(f'/solicitudes/congelamiento/{pausa.pk}/aprobar-reactivacion/', {'propuesta': revision['propuesta']})
            pausa.refresh_from_db()
            self.membresia.refresh_from_db()
            self.assertEqual(pausa.fecha_reactivacion, self.hoy + timedelta(days=3))
            self.assertEqual(pausa.dias_comprometidos, 3)
            self.assertEqual(self.membresia.fecha_fin, self.hoy + timedelta(days=32))
            self.assertIsNotNone(self.socio.membresia_vigente())
            self.assertTrue(NotificacionSocio.objects.filter(socio=self.socio, tipo='REACTIVACION_APROBADA').exists())

    def test_reactivacion_no_revisada_vence_despues_de_la_pausa_sin_duplicar_aviso(self):
        from unittest.mock import patch
        from gestion.services import solicitar_congelamiento_socio, aprobar_congelamiento_sin_conflictos, solicitar_reactivacion, procesar_reactivaciones_vencidas
        from gestion.models import NotificacionSocio
        pausa = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=2))
        admin = User.objects.create_superuser('admin_vencimiento_pausa', 'vencimiento-pausa@example.org', 'clave-prueba')
        aprobar_congelamiento_sin_conflictos(pausa.pk, admin)
        solicitar_reactivacion(pausa.pk, self.usuario)
        with patch('django.utils.timezone.localdate', return_value=self.hoy + timedelta(days=2)):
            self.assertEqual(procesar_reactivaciones_vencidas(), 0)
            pausa.refresh_from_db()
            self.assertTrue(pausa.reactivacion_pendiente)
        with patch('django.utils.timezone.localdate', return_value=self.hoy + timedelta(days=3)):
            self.assertEqual(procesar_reactivaciones_vencidas(), 1)
            self.assertEqual(procesar_reactivaciones_vencidas(), 0)
            pausa.refresh_from_db()
            self.membresia.refresh_from_db()
            self.assertFalse(pausa.reactivacion_pendiente)
            self.assertIsNotNone(pausa.reactivacion_expirada_en)
            self.assertEqual(pausa.dias_comprometidos, 3)
            self.assertIsNotNone(self.socio.membresia_vigente())
            self.assertEqual(self.membresia.fecha_fin, self.hoy + timedelta(days=32))
            self.assertEqual(NotificacionSocio.objects.filter(socio=self.socio, tipo='REACTIVACION_EXPIRADA').count(), 1)

    def test_comando_procesa_reactivacion_vencida(self):
        from unittest.mock import patch
        from gestion.services import solicitar_congelamiento_socio, aprobar_congelamiento_sin_conflictos, solicitar_reactivacion
        pausa = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy)
        admin = User.objects.create_superuser('admin_comando_pausa', 'cmd-pausa@example.org', 'clave-prueba')
        aprobar_congelamiento_sin_conflictos(pausa.pk, admin)
        solicitar_reactivacion(pausa.pk, self.usuario)
        salida = StringIO()
        with patch('django.utils.timezone.localdate', return_value=self.hoy + timedelta(days=1)):
            call_command('procesar_reactivaciones_vencidas', stdout=salida)
        self.assertIn('Solicitudes cerradas: 1', salida.getvalue())
        pausa.refresh_from_db()
        self.assertFalse(pausa.reactivacion_pendiente)

    def test_reactivacion_conserva_reserva_dentro_de_renovacion(self):
        from unittest.mock import patch
        from gestion.services import solicitar_congelamiento_socio
        pausa = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=6))
        futura = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.membresia.fecha_fin + timedelta(days=1), fecha_fin=self.membresia.fecha_fin + timedelta(days=30))
        admin = User.objects.create_superuser('admin_reactivacion_conflicto', 'reactivar-conflicto@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        revision = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['propuesta']
        cliente.post(f'/solicitudes/congelamiento/{pausa.pk}/aprobar/', {'propuesta': revision})
        futura.refresh_from_db()
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga renovación', profesor=profesor, fecha=futura.fecha_inicio, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        Reserva.objects.create(socio=self.socio, clase=clase, estado='CONFIRMADA', membresia=futura)
        self.usuario.groups.add(Group.objects.get_or_create(name='Socio')[0])
        with patch('django.utils.timezone.localdate', return_value=self.hoy + timedelta(days=3)):
            cliente.force_login(self.usuario)
            cliente.post(f'/socio/congelamiento/{pausa.pk}/solicitar-reactivacion/')
            cliente.force_login(admin)
            propuesta = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['reactivacion']['propuesta']
            respuesta = cliente.post(f'/solicitudes/congelamiento/{pausa.pk}/aprobar-reactivacion/', {'propuesta': propuesta}, follow=True)
            self.assertContains(respuesta, 'Reactivación aprobada')
        pausa.refresh_from_db()
        self.assertEqual(pausa.fecha_reactivacion, self.hoy + timedelta(days=3))
        self.assertEqual(Reserva.objects.get(clase=clase, socio=self.socio).estado, 'CONFIRMADA')

    def test_reactivacion_cancela_reserva_fuera_de_renovacion(self):
        from unittest.mock import patch
        from gestion.services import solicitar_congelamiento_socio
        from gestion.models import NotificacionSocio
        pausa = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=6))
        futura = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.membresia.fecha_fin + timedelta(days=1), fecha_fin=self.membresia.fecha_fin + timedelta(days=30))
        admin = User.objects.create_superuser('admin_reserva_reactivar', 'reserva-react@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        revision = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['propuesta']
        cliente.post(f'/solicitudes/congelamiento/{pausa.pk}/aprobar/', {'propuesta': revision})
        futura.refresh_from_db()
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga fuera de período', profesor=profesor, fecha=futura.fecha_fin, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        reserva = Reserva.objects.create(socio=self.socio, clase=clase, estado='CONFIRMADA', membresia=futura, consume_clase=True)
        self.usuario.groups.add(Group.objects.get_or_create(name='Socio')[0])
        with patch('django.utils.timezone.localdate', return_value=self.hoy + timedelta(days=3)):
            cliente.force_login(self.usuario)
            cliente.post(f'/socio/congelamiento/{pausa.pk}/solicitar-reactivacion/')
            cliente.force_login(admin)
            revision = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['reactivacion']
            self.assertEqual([r.pk for r in revision['reservas_futuras']], [reserva.pk])
            cliente.post(f'/solicitudes/congelamiento/{pausa.pk}/aprobar-reactivacion/', {'propuesta': revision['propuesta']})
        pausa.refresh_from_db()
        reserva.refresh_from_db()
        self.assertIsNotNone(pausa.fecha_reactivacion)
        self.assertEqual(reserva.estado, 'CANCELADA')
        self.assertFalse(reserva.consume_clase)
        self.assertTrue(NotificacionSocio.objects.filter(socio=self.socio, reserva=reserva, tipo='REACTIVA_CANCELA_RESERVA').exists())

    def test_reactivacion_ajusta_renovacion_y_conserva_duracion(self):
        from unittest.mock import patch
        from gestion.services import solicitar_congelamiento_socio
        pausa = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=6))
        futura = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.membresia.fecha_fin + timedelta(days=1), fecha_fin=self.membresia.fecha_fin + timedelta(days=30))
        inicio_anterior, fin_anterior, precio = futura.fecha_inicio, futura.fecha_fin, futura.precio_contratado
        admin = User.objects.create_superuser('admin_reactivacion_renovacion', 'react-ren@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        revision = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['propuesta']
        cliente.post(f'/solicitudes/congelamiento/{pausa.pk}/aprobar/', {'propuesta': revision})
        self.usuario.groups.add(Group.objects.get_or_create(name='Socio')[0])
        with patch('django.utils.timezone.localdate', return_value=self.hoy + timedelta(days=3)):
            cliente.force_login(self.usuario)
            cliente.post(f'/socio/congelamiento/{pausa.pk}/solicitar-reactivacion/')
            cliente.force_login(admin)
            revision = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['reactivacion']
            self.assertEqual(revision['renovacion'][3], (inicio_anterior + timedelta(days=3)).isoformat())
            cliente.post(f'/solicitudes/congelamiento/{pausa.pk}/aprobar-reactivacion/', {'propuesta': revision['propuesta']})
            futura.refresh_from_db()
            pausa.refresh_from_db()
            self.assertEqual(pausa.fecha_reactivacion, self.hoy + timedelta(days=3))
            self.assertEqual(futura.fecha_inicio, inicio_anterior + timedelta(days=3))
            self.assertEqual(futura.fecha_fin, fin_anterior + timedelta(days=3))
            self.assertEqual(futura.precio_contratado, precio)

    def test_aprobacion_cancela_reservas_devuelve_saldo_y_promueve_espera(self):
        from gestion.services import solicitar_congelamiento_socio
        from gestion.models import NotificacionSocio
        solicitud = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=2))
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=self.hoy + timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=1)
        reserva = Reserva.objects.create(socio=self.socio, clase=clase, estado='CONFIRMADA', membresia=self.membresia, consume_clase=True)
        otro = Socio.objects.create(rut='12345679-3', nombres='Otra', apellidos='Socia', email='espera-pausa@example.org', telefono='123456789')
        Membresia.objects.create(socio=otro, plan=self.plan, fecha_inicio=self.hoy, fecha_fin=self.hoy + timedelta(days=29))
        espera = Reserva.objects.create(socio=otro, clase=clase, estado='LISTA_ESPERA')
        admin = User.objects.create_superuser('admin_reserva_pausa', 'adminreserva@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        propuesta = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['propuesta']
        cliente.post(f'/solicitudes/congelamiento/{solicitud.pk}/aprobar/', {'propuesta': propuesta})
        reserva.refresh_from_db()
        espera.refresh_from_db()
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'APROBADA')
        self.assertEqual(reserva.estado, 'CANCELADA')
        self.assertFalse(reserva.consume_clase)
        self.assertEqual(espera.estado, 'CONFIRMADA')
        self.assertTrue(NotificacionSocio.objects.filter(socio=self.socio, reserva=reserva, tipo='PAUSA_CANCELA_RESERVA').exists())

    def test_cancelar_pausa_aprobada_pide_confirmacion_y_restaurar_fecha(self):
        from gestion.services import solicitar_congelamiento_socio, aprobar_congelamiento_sin_conflictos
        inicio = self.hoy + timedelta(days=2)
        solicitud = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, inicio, inicio + timedelta(days=2))
        admin = User.objects.create_superuser('admin_cancelar_pausa', 'admincancelar@example.org', 'clave-prueba')
        aprobar_congelamiento_sin_conflictos(solicitud.pk, admin)
        self.usuario.groups.add(Group.objects.get_or_create(name='Socio')[0])
        cliente = Client()
        cliente.force_login(self.usuario)
        ruta = f'/socio/congelamiento/{solicitud.pk}/cancelar-pausa/'
        self.assertContains(cliente.get('/socio/membresias/solicitudes-congelamiento/'), 'Revisar cancelación de pausa')
        respuesta = cliente.get(ruta)
        self.assertContains(respuesta, 'Si la cancelas, el nuevo vencimiento')
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'APROBADA')
        respuesta = cliente.post(ruta, {'propuesta': respuesta.context['propuesta']}, follow=True)
        self.assertContains(respuesta, 'pausa programada fue cancelada')
        solicitud.refresh_from_db()
        self.membresia.refresh_from_db()
        self.assertEqual(solicitud.estado, 'CANCELADA')
        self.assertEqual(solicitud.cancelada_por, self.usuario)
        self.assertEqual(self.membresia.fecha_fin, self.hoy + timedelta(days=29))
        self.assertIsNotNone(self.socio.membresia_para_fecha(inicio))

    def test_cancelacion_pausa_cancela_reservas_fuera_de_vigencia(self):
        from gestion.services import solicitar_congelamiento_socio, aprobar_congelamiento_sin_conflictos
        inicio = self.hoy + timedelta(days=2)
        solicitud = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, inicio, inicio + timedelta(days=2))
        admin = User.objects.create_superuser('admin_pausa_reserva', 'pausareserva@example.org', 'clave-prueba')
        aprobar_congelamiento_sin_conflictos(solicitud.pk, admin)
        self.membresia.refresh_from_db()
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga en extensión', profesor=profesor, fecha=self.membresia.fecha_fin, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        reserva = Reserva.objects.create(socio=self.socio, clase=clase, estado='CONFIRMADA', membresia=self.membresia)
        self.usuario.groups.add(Group.objects.get_or_create(name='Socio')[0])
        cliente = Client()
        cliente.force_login(self.usuario)
        ruta = f'/socio/congelamiento/{solicitud.pk}/cancelar-pausa/'
        respuesta = cliente.get(ruta)
        self.assertContains(respuesta, 'Yoga en extensión')
        self.assertContains(respuesta, 'se cancelarán sin descontar clases')
        cliente.post(ruta, {'propuesta': respuesta.context['propuesta']})
        solicitud.refresh_from_db()
        reserva.refresh_from_db()
        self.assertEqual(solicitud.estado, 'CANCELADA')
        self.assertEqual(reserva.estado, 'CANCELADA')
        self.assertFalse(reserva.consume_clase)

    def test_reactivacion_cancela_reserva_fuera_de_membresia_actual(self):
        from unittest.mock import patch
        from gestion.services import solicitar_congelamiento_socio, aprobar_congelamiento_sin_conflictos
        from gestion.models import NotificacionSocio
        pausa = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy + timedelta(days=6))
        admin = User.objects.create_superuser('admin_reactivar_actual', 'react-actual@example.org', 'clave-prueba')
        aprobar_congelamiento_sin_conflictos(pausa.pk, admin)
        self.membresia.refresh_from_db()
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga fuera de nueva vigencia', profesor=profesor, fecha=self.membresia.fecha_fin, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        reserva = Reserva.objects.create(socio=self.socio, clase=clase, estado='CONFIRMADA', membresia=self.membresia, consume_clase=True)
        self.usuario.groups.add(Group.objects.get_or_create(name='Socio')[0])
        cliente = Client()
        with patch('django.utils.timezone.localdate', return_value=self.hoy + timedelta(days=3)):
            cliente.force_login(self.usuario)
            cliente.post(f'/socio/congelamiento/{pausa.pk}/solicitar-reactivacion/')
            cliente.force_login(admin)
            revision = cliente.get('/solicitudes/congelamiento/').context['revisiones'][0]['reactivacion']
            self.assertEqual([r.pk for r in revision['reservas']], [reserva.pk])
            cliente.post(f'/solicitudes/congelamiento/{pausa.pk}/aprobar-reactivacion/', {'propuesta': revision['propuesta']})
        pausa.refresh_from_db()
        reserva.refresh_from_db()
        self.assertIsNotNone(pausa.fecha_reactivacion)
        self.assertEqual(reserva.estado, 'CANCELADA')
        self.assertFalse(reserva.consume_clase)
        self.assertTrue(NotificacionSocio.objects.filter(socio=self.socio, reserva=reserva, tipo='REACTIVA_VIGENCIA_CANCELA').exists())

    def test_socio_cancela_solicitud_pendiente_propia(self):
        from gestion.services import solicitar_congelamiento_socio
        solicitud = solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy)
        self.usuario.groups.add(Group.objects.get_or_create(name='Socio')[0])
        otro_usuario = User.objects.create_user('otro_socio_cancelar', password='clave-prueba')
        otro_usuario.groups.add(Group.objects.get(name='Socio'))
        Socio.objects.create(usuario=otro_usuario, rut='12345679-3', nombres='Otro', apellidos='Socio', email='otro-cancelar@example.org', telefono='123456789')
        ruta = f'/socio/congelamiento/{solicitud.pk}/cancelar/'
        cliente = Client()
        cliente.force_login(otro_usuario)
        self.assertEqual(cliente.post(ruta).status_code, 404)
        cliente.force_login(self.usuario)
        self.assertEqual(cliente.get(ruta).status_code, 405)
        self.assertRedirects(cliente.post(ruta), '/socio/membresias/')
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'CANCELADA')
        self.assertTrue(solicitar_congelamiento_socio(self.membresia.pk, self.usuario, self.hoy, self.hoy))


class SaldoClasesTestCase(TestCase):
    def setUp(self):
        from datetime import datetime
        self.hoy = timezone.localdate()
        self.plan = Plan.objects.create(nombre='Una clase', duracion_dias=30, precio=35000, permite_clases=True, limite_clases=1)
        self.socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='saldo@example.org', telefono='123456789')
        self.membresia = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.hoy, fecha_fin=self.hoy+timedelta(days=29))
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        self.clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=self.hoy+timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=2)
        self.otra = Clase.objects.create(nombre='Pilates', profesor=profesor, fecha=self.clase.fecha, hora_inicio=time(12), hora_fin=time(13), capacidad_maxima=2)
        self.inicio = timezone.make_aware(datetime.combine(self.clase.fecha, self.clase.hora_inicio))

    def test_reserva_consume_y_cancelacion_oportuna_devuelve_saldo(self):
        from unittest.mock import patch
        from gestion.services import saldo_disponible
        with patch('gestion.services.timezone.localtime', return_value=self.inicio-timedelta(hours=3)):
            ok, _, reserva = gestionar_reserva_clase(self.clase.pk, self.socio.pk)
            self.assertTrue(ok)
            self.assertEqual(saldo_disponible(self.membresia), 0)
            self.assertFalse(gestionar_reserva_clase(self.otra.pk, self.socio.pk)[0])
            self.assertTrue(cancelar_reserva_clase(reserva.pk)[0])
            self.assertEqual(saldo_disponible(self.membresia), 1)

    def test_cancelacion_tardia_conserva_consumo(self):
        from unittest.mock import patch
        from gestion.services import saldo_disponible
        with patch('gestion.services.timezone.localtime', return_value=self.inicio-timedelta(hours=3)):
            _, _, reserva = gestionar_reserva_clase(self.clase.pk, self.socio.pk)
        with patch('gestion.services.timezone.localtime', return_value=self.inicio-timedelta(hours=1)):
            self.assertTrue(cancelar_reserva_clase(reserva.pk)[0])
        self.assertEqual(saldo_disponible(self.membresia), 0)


class ConsultaSaldoClasesTestCase(TestCase):
    def test_membresia_muestra_saldo_e_identifica_condiciones_antiguas(self):
        hoy = timezone.localdate()
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='consulta@example.org', telefono='123456789')
        plan = Plan.objects.create(nombre='Ocho clases', duracion_dias=30, precio=35000, permite_clases=True, limite_clases=8)
        membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=29))
        recepcion = User.objects.create_user('recepcion_saldo', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        cliente = Client()
        cliente.force_login(recepcion)
        self.assertContains(cliente.get('/gestion/membresias/'), '8 de 8 disponibles')
        self.assertContains(cliente.get('/gestion/planes/'), '8 por período')
        Membresia.objects.filter(pk=membresia.pk).update(limite_clases_registrado=False)
        self.assertContains(cliente.get('/gestion/membresias/'), 'Límite histórico por revisar')


class RenovacionPropuestaTestCase(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='renovacion@example.org', telefono='123456789')
        self.plan = Plan.objects.create(nombre='Renovación', duracion_dias=30, precio=35000, permite_clases=True)
        self.actual = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=self.hoy, fecha_fin=self.hoy+timedelta(days=29))

    def test_formulario_propone_dia_siguiente_y_admite_otro_plan(self):
        from gestion.forms import MembresiaForm
        form = MembresiaForm()
        self.assertIn(f'data-inicio-renovacion="{(self.actual.fecha_fin+timedelta(days=1)).isoformat()}"', str(form['socio']))
        otro = Plan.objects.create(nombre='Otro plan', duracion_dias=30, precio=45000, permite_clases=True)
        nuevo = MembresiaForm({'socio': self.socio.pk, 'plan': otro.pk, 'fecha_inicio': (self.actual.fecha_fin+timedelta(days=1)).isoformat(), 'estado': 'ACTIVA'})
        self.assertTrue(nuevo.is_valid(), nuevo.errors)
        self.assertEqual(nuevo.save().plan, otro)

    def test_filtro_distingue_programada_de_vigente(self):
        inicio = self.actual.fecha_fin+timedelta(days=1)
        futura = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=inicio, fecha_fin=inicio+timedelta(days=29))
        admin = User.objects.create_superuser('admin_filtro', 'admin@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        activas = list(cliente.get('/gestion/membresias/', {'estado_m': 'ACTIVA'}).context['objetos'])
        programadas = list(cliente.get('/gestion/membresias/', {'estado_m': 'PROGRAMADA'}).context['objetos'])
        self.assertIn(self.actual, activas)
        self.assertNotIn(futura, activas)
        self.assertIn(futura, programadas)


class GestionSalasTestCase(TestCase):
    def test_administracion_puede_crear_sala_y_recepcion_no(self):
        from gestion.models import Sala
        admin = User.objects.create_superuser('admin_salas', 'admin@example.org', 'clave-prueba')
        recepcion = User.objects.create_user('recepcion_salas', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        cliente = Client()
        cliente.force_login(recepcion)
        self.assertEqual(cliente.get('/gestion/salas/').status_code, 403)
        self.assertEqual(cliente.post('/gestion/salas/nuevo/', {'nombre': 'Sala funcional', 'capacidad': 20}).status_code, 403)
        cliente.force_login(admin)
        self.assertEqual(cliente.post('/gestion/salas/nuevo/', {'nombre': 'Sala funcional', 'capacidad': 20}).status_code, 302)
        self.assertTrue(Sala.objects.filter(nombre='Sala funcional', capacidad=20).exists())
        self.assertContains(cliente.get('/gestion/salas/'), 'Sala funcional')
        self.assertEqual(cliente.post('/gestion/salas/nuevo/', {'nombre': 'Sala vacía', 'capacidad': 0}).status_code, 200)
        self.assertFalse(Sala.objects.filter(nombre='Sala vacía').exists())


class AvisosRecepcionTestCase(TestCase):
    def setUp(self):
        from gestion.models import NotificacionRecepcion
        self.modelo = NotificacionRecepcion
        grupo = Group.objects.create(name='Recepcion')
        self.primera = User.objects.create_user('recepcion_aviso_1', password='clave-prueba')
        self.segunda = User.objects.create_user('recepcion_aviso_2', password='clave-prueba')
        self.primera.groups.add(grupo)
        self.segunda.groups.add(grupo)
        self.profesor_user = User.objects.create_user('profe_aviso_recepcion', password='clave-prueba')
        self.profesor_user.groups.add(Group.objects.create(name='Profesor'))
        self.profesor = Profesor.objects.create(usuario=self.profesor_user, nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        self.clase = Clase.objects.create(nombre='Yoga avisos', profesor=self.profesor, fecha=timezone.localdate()+timedelta(days=3), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)

    def test_indisponibilidad_se_avisa_y_cada_usuario_lee_por_separado(self):
        profesor = Client(); profesor.force_login(self.profesor_user)
        ruta = f'/profesor/clases/{self.clase.pk}/avisar-indisponibilidad/'
        profesor.post(ruta, {'motivo': 'Información privada'})
        profesor.post(ruta, {'motivo': 'Información privada'})
        self.assertEqual(self.modelo.objects.count(), 1)
        self.clase.refresh_from_db(); self.assertTrue(self.clase.activo)
        primera = Client(); primera.force_login(self.primera)
        segunda = Client(); segunda.force_login(self.segunda)
        resumen = primera.get('/recepcion/notificaciones/resumen/').json()
        self.assertTrue(resumen['sin_leer'])
        self.assertIn('continúa programada', resumen['avisos'][0]['mensaje'])
        self.assertNotIn('Información privada', resumen['avisos'][0]['mensaje'])
        self.assertContains(primera.get('/recepcion/notificaciones/'), 'Yoga avisos')
        self.assertFalse(primera.get('/recepcion/notificaciones/resumen/').json()['sin_leer'])
        self.assertTrue(segunda.get('/recepcion/notificaciones/resumen/').json()['sin_leer'])
        self.assertEqual(profesor.get('/recepcion/notificaciones/resumen/').status_code, 403)
        self.assertEqual(profesor.get('/recepcion/notificaciones/').status_code, 403)

    def test_cancelacion_registra_un_aviso_sin_duplicar(self):
        admin = User.objects.create_superuser('admin_aviso_recepcion', 'admin@example.org', 'clave-prueba')
        cliente = Client(); cliente.force_login(admin)
        ruta = f'/clases/{self.clase.pk}/cancelar/'
        cliente.post(ruta); cliente.post(ruta)
        self.assertEqual(self.modelo.objects.count(), 1)
        self.assertIn('canceló', self.modelo.objects.get().mensaje)


class ContratacionRecepcionTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('staff_compra', password='clave-prueba')
        self.user.groups.add(Group.objects.create(name='Recepcion'))
        self.client.force_login(self.user)
        self.socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='contratacion-staff@example.org', telefono='123456789')
        self.plan = Plan.objects.create(nombre='Plan prueba recepción', duracion_dias=30, precio=35000, dias_congelamiento=5, permite_clases=True, limite_clases=8)

    def datos(self):
        return {'socio': self.socio.pk, 'plan': self.plan.pk, 'fecha_inicio': timezone.localdate().isoformat(), 'rut': self.socio.rut, 'metodo_pago': 'EFECTIVO'}

    def test_busqueda_y_confirmacion_unica(self):
        self.assertContains(self.client.get('/recepcion/membresias/contratar/', {'q': 'Camila'}), 'Camila Prueba')
        self.assertEqual(self.client.get('/recepcion/socios/buscar/', {'q': '12345678'}).json()['socios'][0]['id'], self.socio.pk)
        self.assertEqual(self.client.get('/recepcion/socios/buscar/', {'q': 'Camila Prueba'}).json()['socios'][0]['id'], self.socio.pk)
        self.assertEqual(self.client.get('/recepcion/socios/buscar/', {'q': '12.345.678-5'}).json()['socios'][0]['id'], self.socio.pk)
        self.assertNotEqual(Client().get('/recepcion/socios/buscar/', {'q':'Camila'}).status_code, 200)
        respuesta = self.client.post('/recepcion/membresias/contratar/', self.datos())
        self.assertContains(respuesta, 'Revisa la contratación')
        self.assertFalse(Membresia.objects.filter(socio=self.socio).exists())
        token = respuesta.context['token']
        confirmada = self.client.post('/recepcion/membresias/confirmar/', {'propuesta': token})
        self.assertEqual(confirmada.status_code, 302)
        pago = Pago.objects.get(membresia__socio=self.socio)
        self.assertEqual(pago.metodo_pago, 'EFECTIVO')
        self.assertContains(self.client.get(confirmada.url), 'Comprobante')
        self.client.post('/recepcion/membresias/confirmar/', {'propuesta': token})
        self.assertEqual(Pago.objects.filter(membresia__socio=self.socio).count(), 1)

    def test_renovacion_precarga_plan_y_fecha_correcta(self):
        hoy = timezone.localdate()
        actual = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=9))
        pantalla = self.client.get('/recepcion/membresias/contratar/', {'socio': self.socio.pk})
        self.assertContains(pantalla, 'Cambiar plan')
        self.assertEqual(pantalla.context['inicio_sugerido'], actual.fecha_fin+timedelta(days=1))
        datos = {**self.datos(), 'fecha_inicio': (actual.fecha_fin+timedelta(days=1)).isoformat()}
        revision = self.client.post('/recepcion/membresias/contratar/', datos)
        self.assertEqual(revision.status_code, 200)
        self.assertContains(revision, 'Revisa la contratación')
        self.client.post('/recepcion/membresias/confirmar/', {'propuesta': revision.context['token']})
        self.assertEqual(Membresia.objects.filter(socio=self.socio).count(), 2)

    def test_rechaza_rut_y_cambio_de_precio(self):
        respuesta = self.client.post('/recepcion/membresias/contratar/', {**self.datos(), 'rut':'99999999-9'})
        self.assertContains(respuesta, 'RUT no coincide')
        token = self.client.post('/recepcion/membresias/contratar/', self.datos()).context['token']
        self.plan.precio = 39000; self.plan.save(update_fields=['precio'])
        self.client.post('/recepcion/membresias/confirmar/', {'propuesta':token})
        self.assertFalse(Membresia.objects.filter(socio=self.socio).exists())

    def test_nueva_ficha_ofrece_contratar_y_plan_no_aparece_en_menu_recepcion(self):
        respuesta = self.client.get('/gestion/membresias/')
        self.assertContains(respuesta, 'Nueva membresía')
        self.assertNotContains(respuesta, '>Planes</a>')
        datos = {'rut':'12345679-3','nombres':'Otra','apellidos':'Persona','email':'otra-staff@example.org','telefono':'987654321'}
        respuesta = self.client.post('/socios/nuevo/', datos, follow=True)
        self.assertContains(respuesta, '¿Desea contratar una membresía ahora?')


class FichaPagosRecepcionTestCase(TestCase):
    def setUp(self):
        self.recepcion = User.objects.create_user('recepcion_ficha_pagos', password='clave-prueba')
        self.recepcion.groups.add(Group.objects.create(name='Recepcion'))
        self.admin = User.objects.create_superuser('admin_ficha_pagos', 'admin@example.org', 'clave-prueba')
        self.socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Ficha', email='ficha-pagos@example.org', telefono='123456789')
        self.otro = Socio.objects.create(rut='12345679-3', nombres='Otra', apellidos='Persona', email='otra-ficha@example.org', telefono='123456789')
        self.plan = Plan.objects.create(nombre='Plan ficha propio', duracion_dias=30, precio=35000)
        self.plan_ajeno = Plan.objects.create(nombre='Plan ficha ajeno', duracion_dias=30, precio=45000)
        hoy = timezone.localdate()
        self.membresia = Membresia.objects.create(socio=self.socio, plan=self.plan, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=29))
        self.ajena = Membresia.objects.create(socio=self.otro, plan=self.plan_ajeno, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=29))
        self.cliente = Client(); self.cliente.force_login(self.recepcion)

    def test_historial_por_socio_y_menu_segun_rol(self):
        Pago.objects.create(membresia=self.membresia, monto=35000, metodo_pago='EFECTIVO')
        Pago.objects.create(membresia=self.ajena, monto=45000, metodo_pago='EFECTIVO')
        self.membresia.soft_delete()
        respuesta = self.cliente.get(f'/socios/{self.socio.pk}/')
        self.assertContains(respuesta, 'Historial de pagos')
        self.assertContains(respuesta, 'Plan ficha propio')
        self.assertContains(respuesta, 'Registrado')
        self.assertNotContains(respuesta, 'Plan ficha ajeno')
        self.assertNotContains(respuesta, '>Pagos</a>')
        self.assertRedirects(self.cliente.get('/gestion/pagos/'), '/socios/')
        self.assertEqual(self.cliente.get('/gestion/pagos/nuevo/').status_code, 403)
        self.cliente.force_login(self.admin)
        self.assertContains(self.cliente.get('/gestion/pagos/'), 'Plan ficha propio')
        self.assertNotEqual(Client().get(f'/socios/{self.socio.pk}/').status_code, 200)

    def test_pago_sin_movimientos_y_prevencion_de_duplicados(self):
        ruta = f'/socios/{self.socio.pk}/membresias/{self.membresia.pk}/registrar-pago/'
        self.assertContains(self.cliente.get(f'/socios/{self.socio.pk}/'), 'no hay pagos registrados')
        self.assertContains(self.cliente.get(ruta), 'Registrar pago')
        self.assertEqual(self.cliente.get(f'/socios/{self.otro.pk}/membresias/{self.membresia.pk}/registrar-pago/').status_code, 404)
        respuesta = self.cliente.post(ruta, {'membresia': self.membresia.pk, 'monto': 1, 'metodo_pago': 'EFECTIVO'})
        self.assertRedirects(respuesta, f'/socios/{self.socio.pk}/')
        self.assertEqual(Pago.objects.get(membresia=self.membresia).monto, 35000)
        self.cliente.post(ruta, {'membresia': self.membresia.pk, 'monto': 1, 'metodo_pago': 'EFECTIVO'})
        self.assertEqual(Pago.objects.filter(membresia=self.membresia).count(), 1)

    def test_membresia_antigua_exige_monto(self):
        Membresia.objects.filter(pk=self.membresia.pk).update(precio_contratado=None)
        ruta = f'/socios/{self.socio.pk}/membresias/{self.membresia.pk}/registrar-pago/'
        respuesta = self.cliente.post(ruta, {'membresia':self.membresia.pk, 'metodo_pago':'EFECTIVO'})
        self.assertContains(respuesta, 'no tiene precio contratado')
        self.assertFalse(Pago.objects.filter(membresia=self.membresia).exists())
        self.cliente.post(ruta, {'membresia':self.membresia.pk, 'metodo_pago':'EFECTIVO', 'monto':'32000'})
        self.assertEqual(Pago.objects.get(membresia=self.membresia).monto, 32000)


class SociosRecepcionVisualTestCase(TestCase):
    def test_listado_formulario_y_acciones_respetan_rol(self):
        recepcion = User.objects.create_user('recepcion_socios_ui', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='camila-ui@example.org', telefono='123456789')
        cliente = Client(); cliente.force_login(recepcion)
        listado = cliente.get('/socios/', {'q': 'Camila'})
        self.assertContains(listado, 'Camila Prueba')
        ficha = cliente.get(f'/socios/{socio.pk}/')
        self.assertContains(ficha, 'Renovar código')
        self.assertContains(ficha, 'Activar cuenta')
        self.assertNotContains(listado, 'Desactivar socio')
        self.assertContains(cliente.get('/socios/nuevo/'), 'Guardar socio')
        self.assertContains(cliente.get(f'/socios/{socio.pk}/editar/'), 'Camila')
        self.assertContains(cliente.get(f'/socios/{socio.pk}/renovar-codigo/'), 'identidad')
        self.assertContains(cliente.get(f'/socios/{socio.pk}/generar-activacion/'), '24 horas')
        self.assertNotEqual(Client().get('/socios/').status_code, 200)


class ClasesRecepcionVisualTestCase(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user('recepcion_clases_ui', password='clave-prueba')
        self.usuario.groups.add(Group.objects.create(name='Recepcion'))
        self.client.force_login(self.usuario)
        self.socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='clases-ui@example.org', telefono='123456789')
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Profesora', especialidad='Yoga')
        self.fecha = timezone.localdate() + timedelta(days=2)
        self.clase = Clase.objects.create(nombre='Yoga visual', profesor=profesor, fecha=self.fecha, hora_inicio=time(12), hora_fin=time(13), capacidad_maxima=3)

    def test_busqueda_y_listado_preservan_acciones_y_permisos(self):
        reserva = Reserva.objects.create(clase=self.clase, socio=self.socio, estado='CONFIRMADA')
        respuesta = self.client.get('/clases/', {'clase':self.clase.pk, 'q':'12.345.678-5'})
        self.assertContains(respuesta, 'Yoga visual')
        self.assertContains(respuesta, 'Camila Prueba')
        self.assertContains(respuesta, '1 de 3 cupos reservados')
        self.assertContains(respuesta, 'Disponible desde 30 minutos antes')
        self.assertNotContains(respuesta, 'Confirmar asistencia</button>')
        self.assertContains(respuesta, 'Cancelar cupo')
        self.assertNotContains(respuesta, '+ Nueva clase')
        self.assertNotContains(respuesta, 'Seleccionar socio...')
        self.assertNotEqual(Client().get('/clases/').status_code, 200)

    def test_busqueda_y_reserva_para_socio(self):
        plan = Plan.objects.create(nombre='Plan clases UI', duracion_dias=30, precio=25000, permite_clases=True)
        Membresia.objects.create(socio=self.socio, plan=plan, fecha_inicio=timezone.localdate(), fecha_fin=self.fecha+timedelta(days=1))
        self.assertContains(self.client.get('/clases/', {'clase':self.clase.pk, 'q':'Camila Prueba'}), 'Reservar')
        respuesta = self.client.post(f'/clases/{self.clase.pk}/reservar/', {'socio_id':self.socio.pk})
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(Reserva.objects.filter(clase=self.clase, socio=self.socio, estado='CONFIRMADA').exists())
        self.assertEqual(self.client.post(f'/clases/{self.clase.pk}/reservar/', {'socio_id':'no-valido'}).status_code, 302)


class InicioRecepcionVisualTestCase(TestCase):
    def test_panel_muestra_datos_y_conserva_acceso_protegido(self):
        visitante = Client()
        self.assertEqual(visitante.get('/recepcion-dashboard/').status_code, 302)
        usuario = User.objects.create_user('recepcion_inicio', password='ClaveRecepcion123!')
        usuario.groups.add(Group.objects.create(name='Recepcion'))
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(usuario)
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Docente', especialidad='Yoga')
        hoy = timezone.localdate()
        Clase.objects.create(nombre='Yoga de hoy', profesor=profesor, fecha=hoy, hora_inicio=time(0, 0), hora_fin=time(23, 59), capacidad_maxima=12)
        Clase.objects.create(nombre='Clase de mañana', profesor=profesor, fecha=hoy + timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        respuesta = cliente.get('/recepcion-dashboard/')
        self.assertContains(respuesta, 'Yoga de hoy')
        self.assertNotContains(respuesta, 'Clase de mañana')
        self.assertContains(respuesta, 'form-simulador-qr')
        self.assertContains(respuesta, 'aforo-widget')
        self.assertContains(respuesta, 'tabla-ultimos-accesos-body')
        self.assertIn('csrftoken', respuesta.cookies)
        self.assertNotContains(respuesta, 'Personal de recepción')


class PortalSocioTestCase(TestCase):
    def datos_registro(self):
        return {'nombres': 'Camila', 'apellidos': 'Prueba', 'rut': '12.345.678-5', 'email': 'Camila@example.org', 'telefono': '987654321', 'clave': 'MontanaSegura789!', 'confirmacion': 'MontanaSegura789!', 'pregunta': 'MASCOTA', 'respuesta': 'Pelusa'}

    def test_efecto_cancelacion_considera_plazo_y_promocion_tardia(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from gestion.views import efecto_cancelacion_socio
        inicio = timezone.now() + timedelta(hours=3)
        reserva = SimpleNamespace(estado='LISTA_ESPERA', clase=object(), promocion_tardia=False, consume_clase=True, membresia_id=1)
        with patch('gestion.views.inicio_clase_local', return_value=inicio):
            self.assertEqual(efecto_cancelacion_socio(reserva, inicio - timedelta(hours=1)), 'espera')
            reserva.estado = 'CONFIRMADA'
            self.assertEqual(efecto_cancelacion_socio(reserva, inicio - timedelta(hours=2)), 'devuelve')
            self.assertEqual(efecto_cancelacion_socio(reserva, inicio - timedelta(hours=1)), 'descuenta')
            reserva.promocion_tardia = True
            self.assertEqual(efecto_cancelacion_socio(reserva, inicio - timedelta(hours=1)), 'devuelve')

    def test_inicio_muestra_solo_dias_de_ingreso_del_socio_y_membresia(self):
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        otro = Socio.objects.create(rut='12345679-3', nombres='Otra', apellidos='Persona', email='otra-progress@example.org', telefono='123456789')
        hoy = timezone.localdate()
        plan = Plan.objects.create(nombre='Plan Plus', precio=35000, duracion_dias=30)
        Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy + timedelta(days=29))
        for tipo, resultado in [('ENTRADA', 'PERMITIDO'), ('ENTRADA', 'PERMITIDO'), ('SALIDA', 'PERMITIDO'), ('ENTRADA', 'DENEGADO')]:
            RegistroAcceso.objects.create(socio=socio, tipo=tipo, resultado=resultado)
        RegistroAcceso.objects.create(socio=otro, tipo='ENTRADA', resultado='PERMITIDO')
        respuesta = cliente.get('/socio/mi-cuenta/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['dias_restantes'], 30)
        self.assertEqual(respuesta.context['visitas_mes'], 1)
        self.assertEqual(respuesta.context['grafico_visitas'][-1]['cantidad'], 1)
        self.assertContains(respuesta, 'Plan Plus')

    def test_inicio_sin_membresia_y_mes_invalido(self):
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        respuesta = cliente.get('/socio/mi-cuenta/?mes=9999-99')
        self.assertContains(respuesta, 'No tienes una membresía vigente')
        self.assertEqual(respuesta.context['visitas_mes'], 0)

    def test_socio_solicita_congelamiento_y_solo_accede_a_su_membresia(self):
        from gestion.models import SolicitudCongelamiento
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        otro = Socio.objects.create(rut='12345679-3', nombres='Otro', apellidos='Socio', email='otro-pausa@example.org', telefono='123456789')
        plan = Plan.objects.create(nombre='Plan congelamiento web', duracion_dias=30, precio=35000, dias_congelamiento=7)
        hoy = timezone.localdate()
        membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy + timedelta(days=29))
        ajena = Membresia.objects.create(socio=otro, plan=plan, fecha_inicio=hoy, fecha_fin=hoy + timedelta(days=29))
        ruta = f'/socio/membresias/{membresia.pk}/solicitar-congelamiento/'
        self.assertContains(cliente.get('/socio/membresias/'), 'Solicitar congelamiento')
        self.assertEqual(cliente.get(f'/socio/membresias/{ajena.pk}/solicitar-congelamiento/').status_code, 404)
        self.assertEqual(cliente.post(f'/socio/membresias/{ajena.pk}/solicitar-congelamiento/', {'fecha_inicio': hoy, 'fecha_fin': hoy}).status_code, 404)
        self.assertContains(cliente.post(ruta, {'fecha_inicio': hoy, 'fecha_fin': hoy + timedelta(days=7), 'motivo': 'Viaje'}), 'supera los días')
        self.assertFalse(SolicitudCongelamiento.objects.exists())
        self.assertIn('motivo', cliente.post(ruta, {'fecha_inicio': hoy, 'fecha_fin': hoy + timedelta(days=2)}).context['form'].errors)
        respuesta = cliente.post(ruta, {'fecha_inicio': hoy, 'fecha_fin': hoy + timedelta(days=2), 'motivo': 'Viaje'}, follow=True)
        self.assertContains(respuesta, 'pendiente de revisión')
        self.assertContains(respuesta, 'Pendiente')
        self.assertContains(respuesta, 'Viaje')
        self.assertEqual(SolicitudCongelamiento.objects.get().membresia, membresia)
        self.assertEqual(SolicitudCongelamiento.objects.get().motivo, 'Viaje')
        self.assertEqual(Client().get(ruta).status_code, 302)

    def test_pagina_pagos_solo_muestra_historial_del_socio(self):
        cliente = Client()
        self.assertEqual(cliente.get('/socio/pagos/').status_code, 302)
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        self.assertContains(cliente.get('/socio/pagos/'), 'Aún no tienes pagos registrados')
        otro = Socio.objects.create(rut='12345679-3', nombres='Otro', apellidos='Socio', email='otro-pagos@example.org', telefono='123456789')
        plan = Plan.objects.create(nombre='Plan propio pagos', precio=35000, duracion_dias=30)
        plan_ajeno = Plan.objects.create(nombre='Plan privado pagos', precio=45000, duracion_dias=30)
        hoy = timezone.localdate()
        propia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=29))
        ajena = Membresia.objects.create(socio=otro, plan=plan_ajeno, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=29))
        Pago.objects.create(membresia=propia, monto=35000, metodo_pago='EFECTIVO', anulado=True)
        Pago.objects.create(membresia=ajena, monto=45000, metodo_pago='EFECTIVO')
        propia.soft_delete()
        respuesta = cliente.get('/socio/pagos/')
        self.assertContains(respuesta, 'Plan propio pagos')
        self.assertContains(respuesta, 'Anulado')
        self.assertNotContains(respuesta, 'Plan privado pagos')

    def test_historial_membresias_y_pagos_solo_muestra_registros_propios(self):
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        otro = Socio.objects.create(rut='12345679-3', nombres='Otro', apellidos='Socio', email='otrohistorial@example.org', telefono='123456789')
        plan = Plan.objects.create(nombre='Plan personal', precio=35000, duracion_dias=30, permite_clases=True, limite_clases=8)
        plan_ajeno = Plan.objects.create(nombre='Plan privado ajeno', precio=99999, duracion_dias=30)
        hoy = timezone.localdate()
        membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=29))
        Pago.objects.create(membresia=membresia, monto=35000, metodo_pago='TARJETA_DEBITO')
        Membresia.objects.create(socio=otro, plan=plan_ajeno, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=29))
        membresia.soft_delete()
        respuesta = cliente.get('/socio/membresias/')
        self.assertContains(respuesta, 'Plan personal')
        self.assertContains(respuesta, 'Desactivada')
        self.assertContains(respuesta, '35 000 CLP')
        self.assertContains(cliente.get('/socio/pagos/'), 'Registrado')
        self.assertNotContains(respuesta, 'Plan privado ajeno')
        self.assertNotEqual(Client().get('/socio/membresias/').status_code, 200)

    def test_pagos_muestra_comprobante_y_estado_de_anulacion(self):
        from gestion.models import SolicitudAnulacionPago
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        plan = Plan.objects.create(nombre='Plan recibo', precio=35000, duracion_dias=30)
        hoy = timezone.localdate()
        membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy + timedelta(days=29))
        pago = Pago.objects.create(membresia=membresia, monto=35000, metodo_pago='TARJETA_DEBITO')
        solicitud = SolicitudAnulacionPago.objects.create(pago=pago, solicitante=socio.usuario, origen='SOCIO', motivo='Error', estado='RECHAZADA', comentario='Pago revisado')
        pagina = cliente.get('/socio/pagos/')
        self.assertContains(pagina, 'Ver comprobante')
        self.assertContains(pagina, 'Rechazada')
        self.assertContains(pagina, 'Pago revisado')
        comprobante = cliente.get(f'/socio/pagos/{pago.pk}/comprobante/?origen=pagos')
        self.assertContains(comprobante, 'Comprobante de pago')
        self.assertNotContains(comprobante, 'Procesando tu contratación')

    def test_nuevo_flujo_comprobante_privado_y_sin_duplicar(self):
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        plan = Plan.objects.create(nombre='Plan flujo nuevo', duracion_dias=30, precio=35000, dias_congelamiento=7)
        datos = {'plan': plan.pk, 'fecha_inicio': timezone.localdate().isoformat(), 'metodo_pago': 'TARJETA_DEBITO', 'rut': socio.rut}
        token = cliente.post('/socio/membresias/contratar/', datos).context['token']
        respuesta = cliente.post('/socio/membresias/confirmar/', {'propuesta': token, 'flujo': 'portal'})
        pago = Pago.objects.get(membresia__socio=socio)
        self.assertRedirects(respuesta, f'/socio/pagos/{pago.pk}/comprobante/')
        self.assertContains(cliente.get(respuesta.url), 'Plan flujo nuevo')
        cliente.post('/socio/membresias/confirmar/', {'propuesta': token, 'flujo': 'portal'})
        self.assertEqual(Pago.objects.filter(membresia__socio=socio).count(), 1)
        otro_usuario = User.objects.create_user('otro_comprobante', password='clave-prueba')
        otro_usuario.groups.add(Group.objects.get(name='Socio'))
        Socio.objects.create(usuario=otro_usuario, rut='12345679-3', nombres='Otro', apellidos='Socio', email='otro-recibo@example.org', telefono='123456789')
        cliente.force_login(otro_usuario)
        self.assertEqual(cliente.get(respuesta.url).status_code, 404)

    def test_resumen_notificaciones_es_privado_y_no_marca_leido(self):
        from gestion.models import NotificacionSocio
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        otro = Socio.objects.create(rut='12345679-3', nombres='Otro', apellidos='Socio', email='otro-resumen@example.org', telefono='123456789')
        NotificacionSocio.objects.create(socio=socio, tipo='PRUEBA', mensaje='Mensaje propio')
        NotificacionSocio.objects.create(socio=otro, tipo='PRUEBA', mensaje='Mensaje privado ajeno')
        respuesta = cliente.get('/socio/notificaciones/resumen/')
        self.assertTrue(respuesta.json()['sin_leer'])
        self.assertEqual([a['mensaje'] for a in respuesta.json()['avisos']], ['Mensaje propio'])
        self.assertFalse(socio.notificaciones.get().leida)
        self.assertEqual(Client().get('/socio/notificaciones/resumen/').status_code, 302)

    def test_contratacion_socio_revisa_calcula_fecha_y_pago_sin_duplicar(self):
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        plan = Plan.objects.create(nombre='Plan contratación', duracion_dias=30, precio=35000, permite_clases=True, limite_clases=8, dias_congelamiento=7)
        inicio = timezone.localdate() + timedelta(days=2)
        datos = {'plan': plan.pk, 'fecha_inicio': inicio.isoformat(), 'metodo_pago': 'TARJETA_DEBITO', 'rut': socio.rut}
        respuesta = cliente.post('/socio/membresias/contratar/', datos)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context['fin'], inicio + timedelta(days=29))
        self.assertFalse(Membresia.objects.filter(socio=socio).exists())
        token = respuesta.context['token']
        self.assertRedirects(cliente.post('/socio/membresias/confirmar/', {'propuesta': token}), '/socio/mi-cuenta/')
        membresia = Membresia.objects.get(socio=socio)
        self.assertEqual(membresia.fecha_fin, inicio + timedelta(days=29))
        self.assertEqual(membresia.precio_contratado, plan.precio)
        self.assertEqual(membresia.limite_clases_contratado, 8)
        self.assertEqual(membresia.pagos.get().monto, plan.precio)
        self.assertEqual(membresia.pagos.get().metodo_pago, 'TARJETA_DEBITO')
        cliente.post('/socio/membresias/confirmar/', {'propuesta': token})
        self.assertEqual(Membresia.objects.filter(socio=socio).count(), 1)
        self.assertEqual(Pago.objects.filter(membresia__socio=socio).count(), 1)
        self.assertContains(cliente.get('/socio/mi-cuenta/'), 'Membresía programada')

    def test_renovacion_cambio_de_plan_y_condiciones_modificadas(self):
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        plan = Plan.objects.create(nombre='Plan actual', duracion_dias=30, precio=25000, permite_clases=False)
        nuevo = Plan.objects.create(nombre='Plan nuevo', duracion_dias=30, precio=35000, permite_clases=True, limite_clases=8, dias_congelamiento=7)
        hoy = timezone.localdate()
        actual = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=9))
        datos = {'plan': nuevo.pk, 'fecha_inicio': (actual.fecha_fin+timedelta(days=1)).isoformat(), 'metodo_pago': 'TARJETA_CREDITO', 'rut': socio.rut}
        respuesta = cliente.post('/socio/membresias/contratar/', {**datos, 'fecha_inicio': hoy.isoformat()})
        self.assertContains(respuesta, 'día siguiente')
        token = cliente.post('/socio/membresias/contratar/', datos).context['token']
        nuevo.precio = 40000
        nuevo.save(update_fields=['precio'])
        cliente.post('/socio/membresias/confirmar/', {'propuesta': token})
        self.assertEqual(Membresia.objects.filter(socio=socio).count(), 1)
        token = cliente.post('/socio/membresias/contratar/', datos).context['token']
        cliente.post('/socio/membresias/confirmar/', {'propuesta': token})
        futura = Membresia.objects.get(socio=socio, plan=nuevo)
        self.assertEqual(futura.fecha_inicio, actual.fecha_fin + timedelta(days=1))
        self.assertEqual(futura.precio_contratado, 40000)
        self.assertEqual(futura.pagos.get().metodo_pago, 'TARJETA_CREDITO')

    def test_contratacion_exige_definir_congelamiento_y_revisa_cambios(self):
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        plan = Plan.objects.create(nombre='Plan sin configurar', duracion_dias=30, precio=25000)
        datos = {'plan': plan.pk, 'fecha_inicio': timezone.localdate().isoformat(), 'metodo_pago': 'TARJETA_DEBITO', 'rut': socio.rut}
        self.assertContains(cliente.post('/socio/membresias/contratar/', datos), 'no tiene definidos sus días de congelamiento')
        plan.dias_congelamiento = 5
        plan.save(update_fields=['dias_congelamiento'])
        respuesta = cliente.post('/socio/membresias/contratar/', datos)
        self.assertContains(respuesta, 'Congelamiento: 5 días')
        token = respuesta.context['token']
        plan.dias_congelamiento = 7
        plan.save(update_fields=['dias_congelamiento'])
        cliente.post('/socio/membresias/confirmar/', {'propuesta': token})
        self.assertFalse(Membresia.objects.filter(socio=socio).exists())

    def test_recuperacion_asistida_un_uso_y_sin_exponer_respuesta(self):
        from django.contrib.auth.hashers import check_password
        from gestion.models import RecuperacionAsistidaSocio
        socio_cliente = Client()
        socio_cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        recepcion = User.objects.create_user('recepcion_asistida', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        personal = Client()
        personal.force_login(recepcion)
        ruta = f'/socios/{socio.pk}/generar-recuperacion/'
        self.assertEqual(personal.post(ruta, {}).status_code, 200)
        self.assertFalse(RecuperacionAsistidaSocio.objects.exists())
        primero = personal.post(ruta, {'identidad_verificada': 'si'}).context['codigo']
        segundo = personal.post(ruta, {'identidad_verificada': 'si'}).context['codigo']
        self.assertNotEqual(primero, segundo)
        recuperacion = RecuperacionAsistidaSocio.objects.get(socio=socio)
        self.assertNotIn(segundo, recuperacion.codigo_hash)
        self.assertEqual(recuperacion.generado_por, recepcion)
        cliente = Client()
        datos = {'rut': socio.rut, 'codigo': primero, 'clave': 'BosqueSegura234!', 'confirmacion': 'BosqueSegura234!', 'pregunta': 'CIUDAD', 'respuesta': 'Santiago'}
        self.assertContains(cliente.post('/recuperar-cuenta/codigo/', datos), 'No se pudo recuperar')
        datos['codigo'] = segundo
        self.assertRedirects(cliente.post('/recuperar-cuenta/codigo/', datos), '/login/')
        socio.refresh_from_db()
        self.assertTrue(socio.usuario.check_password('BosqueSegura234!'))
        self.assertEqual(socio.pregunta_seguridad, 'CIUDAD')
        self.assertTrue(check_password('santiago', socio.respuesta_seguridad_hash))
        self.assertContains(cliente.post('/recuperar-cuenta/codigo/', datos), 'No se pudo recuperar')
        self.assertEqual(socio_cliente.get('/socio/mi-cuenta/').status_code, 302)

    def test_recuperacion_asistida_bloqueo_y_vencimiento(self):
        from gestion.models import RecuperacionAsistidaSocio
        creado = Client()
        creado.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        recepcion = User.objects.create_user('recepcion_asistida_limite', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        personal = Client()
        personal.force_login(recepcion)
        codigo = personal.post(f'/socios/{socio.pk}/generar-recuperacion/', {'identidad_verificada': 'si'}).context['codigo']
        cliente = Client()
        datos = {'rut': socio.rut, 'codigo': 'incorrecto', 'clave': 'BosqueSegura234!', 'confirmacion': 'BosqueSegura234!', 'pregunta': 'CIUDAD', 'respuesta': 'Santiago'}
        for _ in range(3):
            cliente.post('/recuperar-cuenta/codigo/', datos)
        datos['codigo'] = codigo
        self.assertContains(cliente.post('/recuperar-cuenta/codigo/', datos), 'Demasiados intentos')
        recuperacion = RecuperacionAsistidaSocio.objects.get(socio=socio)
        recuperacion.vence_en = timezone.now() - timedelta(seconds=1)
        recuperacion.save(update_fields=['vence_en'])
        self.assertContains(cliente.post('/recuperar-cuenta/codigo/', datos), 'No se pudo recuperar')
        socio.usuario.refresh_from_db()
        self.assertTrue(socio.usuario.check_password('MontanaSegura789!'))

    def test_recuperacion_pregunta_cambia_clave_y_no_permite_reutilizacion(self):
        creado = Client()
        creado.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        cliente = Client()
        nueva = {'clave': 'BosqueSegura234!', 'confirmacion': 'BosqueSegura234!'}
        self.assertEqual(cliente.post('/recuperar-cuenta/nueva-clave/', nueva).status_code, 302)
        self.assertRedirects(cliente.post('/recuperar-cuenta/', {'email': socio.email}), '/recuperar-cuenta/pregunta/')
        self.assertContains(cliente.get('/recuperar-cuenta/pregunta/'), socio.get_pregunta_seguridad_display())
        self.assertRedirects(cliente.post('/recuperar-cuenta/pregunta/', {'respuesta': 'PELUSA'}), '/recuperar-cuenta/nueva-clave/')
        self.assertRedirects(cliente.post('/recuperar-cuenta/nueva-clave/', nueva), '/login/')
        socio.usuario.refresh_from_db()
        self.assertTrue(socio.usuario.check_password(nueva['clave']))
        self.assertFalse(socio.usuario.check_password('MontanaSegura789!'))
        self.assertRedirects(cliente.get('/recuperar-cuenta/nueva-clave/'), '/recuperar-cuenta/')

    def test_bloqueo_recuperacion_no_bloquea_inicio_de_sesion(self):
        creado = Client()
        creado.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        cliente = Client()
        cliente.post('/recuperar-cuenta/', {'email': socio.email})
        for _ in range(3):
            cliente.post('/recuperar-cuenta/pregunta/', {'respuesta': 'incorrecta'})
        socio.refresh_from_db()
        self.assertIsNotNone(socio.recuperacion_bloqueada_hasta)
        self.assertContains(cliente.post('/recuperar-cuenta/pregunta/', {'respuesta': 'Pelusa'}), 'Demasiados intentos')
        self.assertTrue(Client().login(username=socio.usuario.username, password='MontanaSegura789!'))
        socio.recuperacion_bloqueada_hasta = timezone.now() - timedelta(seconds=1)
        socio.save(update_fields=['recuperacion_bloqueada_hasta'])
        self.assertRedirects(cliente.post('/recuperar-cuenta/pregunta/', {'respuesta': 'Pelusa'}), '/recuperar-cuenta/nueva-clave/')

    def test_cambio_de_pregunta_invalida_recuperacion_previamente_verificada(self):
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        recuperacion = Client()
        recuperacion.post('/recuperar-cuenta/', {'email': socio.email})
        recuperacion.post('/recuperar-cuenta/pregunta/', {'respuesta': 'Pelusa'})
        cliente.post('/socio/perfil/seguridad/', {'clave_actual': 'MontanaSegura789!', 'pregunta': 'CIUDAD', 'respuesta': 'Santiago'})
        self.assertRedirects(recuperacion.post('/recuperar-cuenta/nueva-clave/', {'clave': 'BosqueSegura234!', 'confirmacion': 'BosqueSegura234!'}), '/recuperar-cuenta/')
        socio.usuario.refresh_from_db()
        self.assertTrue(socio.usuario.check_password('MontanaSegura789!'))

    def test_recepcion_genera_activacion_socio_sin_duplicar_ficha(self):
        from django.contrib.auth.hashers import check_password
        from gestion.models import ActivacionSocio
        socio = Socio.objects.create(rut='12.345.678-5', nombres='Camila', apellidos='Prueba', email='camila@example.org', telefono='123456789')
        recepcion = User.objects.create_user('recepcion_activacion_socio', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        personal = Client()
        personal.force_login(recepcion)
        ruta = f'/socios/{socio.pk}/generar-activacion/'
        self.assertEqual(personal.post(ruta, {}).status_code, 200)
        self.assertFalse(ActivacionSocio.objects.exists())
        primero = personal.post(ruta, {'identidad_verificada': 'si'}).context['codigo']
        segundo = personal.post(ruta, {'identidad_verificada': 'si'}).context['codigo']
        self.assertNotEqual(primero, segundo)
        activacion = ActivacionSocio.objects.get(socio=socio)
        self.assertNotIn(segundo, activacion.codigo_hash)
        cliente = Client()
        datos = {'rut': '12345678-5', 'codigo': primero, 'clave': 'MontanaSegura789!', 'confirmacion': 'MontanaSegura789!', 'pregunta': 'MASCOTA', 'respuesta': 'Pelusa'}
        self.assertEqual(cliente.post('/activar-cuenta-socio/', datos).status_code, 200)
        datos['codigo'] = segundo
        self.assertRedirects(cliente.post('/activar-cuenta-socio/', datos), '/login/')
        socio.refresh_from_db()
        self.assertEqual(Socio.all_objects.count(), 1)
        self.assertEqual(socio.usuario.username, 'camila@example.org')
        self.assertTrue(socio.usuario.check_password(datos['clave']))
        self.assertTrue(check_password('pelusa', socio.respuesta_seguridad_hash))
        self.assertTrue(cliente.login(username='camila@example.org', password=datos['clave']))
        self.assertEqual(personal.post(ruta, {'identidad_verificada': 'si'}).status_code, 302)
        activacion.refresh_from_db()
        self.assertIsNotNone(activacion.usado_en)

    def test_activacion_socio_bloquea_intentos_y_no_acepta_codigo_vencido(self):
        from gestion.models import ActivacionSocio
        socio = Socio.objects.create(rut='12.345.678-5', nombres='Camila', apellidos='Prueba', email='camila@example.org', telefono='123456789')
        recepcion = User.objects.create_user('recepcion_limite_socio', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        personal = Client()
        personal.force_login(recepcion)
        codigo = personal.post(f'/socios/{socio.pk}/generar-activacion/', {'identidad_verificada': 'si'}).context['codigo']
        cliente = Client()
        datos = {'rut': socio.rut, 'codigo': 'incorrecto', 'clave': 'MontanaSegura789!', 'confirmacion': 'MontanaSegura789!', 'pregunta': 'MASCOTA', 'respuesta': 'Pelusa'}
        for _ in range(3):
            cliente.post('/activar-cuenta-socio/', datos)
        datos['codigo'] = codigo
        self.assertContains(cliente.post('/activar-cuenta-socio/', datos), 'Demasiados intentos')
        activacion = ActivacionSocio.objects.get(socio=socio)
        activacion.vence_en = timezone.now() - timedelta(seconds=1)
        activacion.save(update_fields=['vence_en'])
        self.assertContains(cliente.post('/activar-cuenta-socio/', datos), 'No se pudo activar')
        socio.refresh_from_db()
        self.assertIsNone(socio.usuario_id)

    def test_recepcion_renueva_codigo_tras_confirmar_identidad(self):
        from gestion.models import CambioCodigoAcceso
        from gestion.services import validar_y_registrar_acceso
        cliente_socio = Client()
        cliente_socio.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        anterior = socio.codigo_qr
        ruta = f'/socios/{socio.pk}/renovar-codigo/'
        cliente_socio.post(ruta, {'identidad_verificada': 'si'})
        socio.refresh_from_db()
        self.assertEqual(socio.codigo_qr, anterior)
        recepcion = User.objects.create_user('recepcion_cambio_qr', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        cliente = Client()
        cliente.force_login(recepcion)
        self.assertEqual(cliente.get(ruta).status_code, 200)
        self.assertEqual(cliente.post(ruta, {}).status_code, 200)
        socio.refresh_from_db()
        self.assertEqual(socio.codigo_qr, anterior)
        self.assertFalse(CambioCodigoAcceso.objects.exists())
        self.assertEqual(cliente.post(ruta, {'identidad_verificada': 'si'}).status_code, 302)
        socio.refresh_from_db()
        self.assertNotEqual(socio.codigo_qr, anterior)
        self.assertEqual(CambioCodigoAcceso.objects.get(socio=socio).realizado_por, recepcion)
        self.assertFalse(validar_y_registrar_acceso(anterior)['autorizado'])
        self.assertIsNone(validar_y_registrar_acceso(anterior)['socio'])
        self.assertContains(cliente_socio.get('/socio/codigo-acceso/'), socio.codigo_qr)
        self.assertNotContains(cliente_socio.get('/socio/codigo-acceso/'), anterior)

    def test_codigo_acceso_es_personal_y_no_se_guarda_en_cache(self):
        from io import BytesIO
        import qrcode
        import qrcode.image.svg
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        respuesta = cliente.get('/socio/codigo-acceso/')
        self.assertContains(respuesta, socio.codigo_qr)
        self.assertIn('no-store', respuesta['Cache-Control'])
        qr = cliente.get('/socio/codigo-acceso/qr/')
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr['Content-Type'], 'image/svg+xml')
        self.assertIn('no-store', qr['Cache-Control'])
        esperado = BytesIO()
        qrcode.make(socio.codigo_qr, image_factory=qrcode.image.svg.SvgPathImage).save(esperado)
        self.assertEqual(qr.content, esperado.getvalue())
        self.assertNotEqual(Client().get('/socio/codigo-acceso/qr/').status_code, 200)
        socio.soft_delete()
        self.assertEqual(cliente.get('/socio/codigo-acceso/').status_code, 403)
        self.assertEqual(cliente.get('/socio/codigo-acceso/qr/').status_code, 403)

    def test_socio_reserva_y_cancela_solo_sus_propias_clases(self):
        from gestion.services import saldo_disponible
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        otro = Socio.objects.create(rut='12345679-3', nombres='Otro', apellidos='Socio', email='otroreserva@example.org', telefono='123456789')
        profesor = Profesor.objects.create(nombres='ProfesoraSecreta', apellidos='Prueba', especialidad='Yoga')
        fecha = timezone.localdate() + timedelta(days=2)
        clase = Clase.objects.create(nombre='Yoga socio', profesor=profesor, fecha=fecha, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=2, sala='SalaSecreta')
        plan = Plan.objects.create(nombre='Plan socio reserva', duracion_dias=30, precio=25000, permite_clases=True, limite_clases=2)
        membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=timezone.localdate(), fecha_fin=fecha)
        ruta = f'/socio/clases/{clase.pk}/reservar/'
        self.assertContains(cliente.get('/socio/clases/'), 'ProfesoraSecreta')
        self.assertNotContains(cliente.get('/socio/clases/'), 'SalaSecreta')
        self.assertEqual(cliente.get(ruta).status_code, 405)
        self.assertEqual(cliente.post(ruta, {'socio_id': otro.pk}).status_code, 302)
        reserva = Reserva.objects.get(clase=clase, socio=socio)
        self.assertEqual(reserva.estado, 'CONFIRMADA')
        self.assertFalse(Reserva.objects.filter(clase=clase, socio=otro).exists())
        self.assertEqual(saldo_disponible(membresia), 1)
        self.assertContains(cliente.get('/socio/reservas/'), 'ProfesoraSecreta')
        otra = Reserva.objects.create(clase=clase, socio=otro, estado='LISTA_ESPERA')
        self.assertEqual(cliente.post(f'/socio/reservas/{otra.pk}/cancelar/').status_code, 404)
        self.assertEqual(cliente.get(f'/socio/reservas/{reserva.pk}/cancelar/').status_code, 200)
        self.assertEqual(cliente.post(f'/socio/reservas/{reserva.pk}/cancelar/').status_code, 302)
        reserva.refresh_from_db()
        self.assertEqual(reserva.estado, 'CANCELADA')
        self.assertEqual(saldo_disponible(membresia), 2)

    def test_socio_sin_membresia_no_puede_reservar(self):
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Profesora', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=timezone.localdate()+timedelta(days=2), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=1)
        respuesta = cliente.post(f'/socio/clases/{clase.pk}/reservar/', follow=True)
        self.assertContains(respuesta, 'membresía activa')
        self.assertFalse(Reserva.objects.filter(clase=clase).exists())

    def test_historial_notificaciones_lectura_privacidad_y_borrado(self):
        from gestion.models import NotificacionSocio
        from gestion.services import notificar_reserva
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        otro_usuario = User.objects.create_user('otro_socio_aviso', password='clave-prueba')
        otro_usuario.groups.add(Group.objects.get(name='Socio'))
        otro = Socio.objects.create(usuario=otro_usuario, rut='12345679-3', nombres='Otro', apellidos='Socio', email='otroaviso@example.org', telefono='123456789')
        profesor = Profesor.objects.create(nombres='Profesor', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=timezone.localdate()+timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=5)
        reserva = Reserva.objects.create(socio=socio, clase=clase, estado='CONFIRMADA')
        otra_reserva = Reserva.objects.create(socio=otro, clase=clase, estado='CONFIRMADA')
        notificar_reserva(reserva, 'PRUEBA', 'Aviso privado Camila')
        notificar_reserva(otra_reserva, 'PRUEBA', 'Aviso privado de otra persona')
        self.assertContains(cliente.get('/socio/mi-cuenta/'), 'Tienes notificaciones sin leer')
        respuesta = cliente.get('/socio/notificaciones/')
        self.assertContains(respuesta, 'Aviso privado Camila')
        self.assertNotContains(respuesta, 'Aviso privado de otra persona')
        self.assertTrue(NotificacionSocio.objects.get(reserva=reserva).leida)
        self.assertFalse(NotificacionSocio.objects.get(reserva=otra_reserva).leida)
        self.assertNotContains(cliente.get('/socio/mi-cuenta/'), 'Tienes notificaciones sin leer')
        self.assertEqual(cliente.get('/socio/notificaciones/limpiar/').status_code, 200)
        self.assertFalse(NotificacionSocio.objects.get(reserva=reserva).oculta)
        cliente.post('/socio/notificaciones/limpiar/')
        self.assertTrue(NotificacionSocio.objects.get(reserva=reserva).oculta)
        self.assertFalse(NotificacionSocio.objects.get(reserva=otra_reserva).oculta)
        self.assertNotContains(cliente.get('/socio/notificaciones/'), 'Aviso privado Camila')
        notificar_reserva(reserva, 'PRUEBA', 'Aviso privado Camila')
        self.assertEqual(NotificacionSocio.objects.filter(reserva=reserva, tipo='PRUEBA').count(), 1)
        self.assertNotContains(cliente.get('/socio/mi-cuenta/'), 'Tienes notificaciones sin leer')

    def test_cambio_pregunta_seguridad_requiere_clave_y_guarda_respuesta_protegida(self):
        from django.contrib.auth.hashers import check_password
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        anterior = socio.respuesta_seguridad_hash
        ruta = '/socio/perfil/seguridad/'
        datos = {'clave_actual': 'incorrecta', 'pregunta': 'CIUDAD', 'respuesta': 'Santiago'}
        self.assertContains(cliente.post(ruta, datos), 'contraseña actual no es correcta')
        socio.refresh_from_db()
        self.assertEqual(socio.respuesta_seguridad_hash, anterior)
        datos['clave_actual'] = 'MontanaSegura789!'
        self.assertRedirects(cliente.post(ruta, datos), '/socio/perfil/')
        socio.refresh_from_db()
        self.assertEqual(socio.pregunta_seguridad, 'CIUDAD')
        self.assertTrue(check_password('santiago', socio.respuesta_seguridad_hash))
        self.assertFalse(check_password('pelusa', socio.respuesta_seguridad_hash))
        self.assertNotContains(cliente.get(ruta), 'Santiago')
        recepcion = User.objects.create_user('recepcion_seguridad_socio', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        cliente.force_login(recepcion)
        self.assertEqual(cliente.post(ruta, datos).status_code, 403)

    def test_login_admite_correo_y_usuario_existente(self):
        usuario = User.objects.create_user('usuario_antiguo', email='persona@example.org', password='ClaveSegura123!')
        cliente = Client()
        respuesta = cliente.post('/login/', {'username': 'PERSONA@EXAMPLE.ORG', 'password': 'ClaveSegura123!'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(int(cliente.session['_auth_user_id']), usuario.pk)
        cliente.logout()
        respuesta = cliente.post('/login/', {'username': 'usuario_antiguo', 'password': 'ClaveSegura123!'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(int(cliente.session['_auth_user_id']), usuario.pk)
        cliente.logout()
        User.objects.create_user('otro_usuario', email='persona@example.org', password='OtraClave123!')
        cliente.post('/login/', {'username': 'persona@example.org', 'password': 'ClaveSegura123!'})
        self.assertNotIn('_auth_user_id', cliente.session)

    def test_socio_actualiza_contacto_y_usa_nuevo_correo_para_ingresar(self):
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        respuesta = cliente.post('/socio/perfil/', {'email': 'nuevo@example.org', 'telefono': '999999999', 'nombres': 'Alterado', 'rut': '99999999-9'})
        self.assertRedirects(respuesta, '/socio/perfil/')
        socio.refresh_from_db()
        socio.usuario.refresh_from_db()
        self.assertEqual(socio.nombres, 'Camila')
        self.assertEqual(socio.rut, '12345678-5')
        self.assertEqual(socio.telefono, '999999999')
        self.assertEqual(socio.usuario.username, 'nuevo@example.org')
        self.assertEqual(cliente.get('/socio/mi-cuenta/').status_code, 200)
        self.assertTrue(Client().login(username='nuevo@example.org', password='MontanaSegura789!'))

    def test_socio_rechaza_correo_ajeno_y_cambia_clave(self):
        cliente = Client()
        cliente.post('/crear-cuenta/', self.datos_registro())
        socio = Socio.objects.get(email='camila@example.org')
        Socio.objects.create(rut='12345679-3', nombres='Otro', apellidos='Socio', email='otro@example.org', telefono='123456789')
        respuesta = cliente.post('/socio/perfil/', {'email': 'OTRO@example.org', 'telefono': '123456789'})
        self.assertEqual(respuesta.status_code, 200)
        socio.refresh_from_db()
        self.assertEqual(socio.email, 'camila@example.org')
        ruta = '/socio/perfil/cambiar-clave/'
        datos = {'old_password': 'incorrecta', 'new_password1': 'BosqueSegura234!', 'new_password2': 'BosqueSegura234!'}
        self.assertEqual(cliente.post(ruta, datos).status_code, 200)
        datos['old_password'] = 'MontanaSegura789!'
        self.assertRedirects(cliente.post(ruta, datos), '/socio/perfil/')
        self.assertEqual(cliente.get('/socio/mi-cuenta/').status_code, 200)
        self.assertTrue(Client().login(username='camila@example.org', password='BosqueSegura234!'))

    def test_registro_crea_cuenta_socio_sin_membresia_y_protege_respuesta(self):
        from django.contrib.auth.hashers import check_password
        cliente = Client()
        self.assertRedirects(cliente.post('/crear-cuenta/', self.datos_registro()), '/socio/mi-cuenta/')
        socio = Socio.objects.get(email='camila@example.org')
        self.assertEqual(socio.rut, '12345678-5')
        self.assertTrue(socio.usuario.check_password('MontanaSegura789!'))
        self.assertTrue(check_password('pelusa', socio.respuesta_seguridad_hash))
        self.assertNotIn('Pelusa', socio.respuesta_seguridad_hash)
        self.assertFalse(socio.membresias.exists())
        self.assertContains(cliente.get('/socio/mi-cuenta/'), 'Camila')

    def test_registro_rechaza_rut_duplicado_con_otro_formato_y_datos_invalidos(self):
        socio = Socio.objects.create(rut='12.345.678-5', nombres='Ficha', apellidos='Existente', email='otro@example.org', telefono='123456789')
        socio.soft_delete()
        cliente = Client()
        respuesta = cliente.post('/crear-cuenta/', {**self.datos_registro(), 'rut': '12345678-5'})
        self.assertContains(respuesta, 'Ya existe una ficha con este RUT')
        self.assertEqual(Socio.all_objects.count(), 1)
        respuesta = cliente.post('/crear-cuenta/', {**self.datos_registro(), 'rut': '12345678-0'})
        self.assertContains(respuesta, 'dígito verificador')
        respuesta = cliente.post('/crear-cuenta/', {**self.datos_registro(), 'rut': '12345678-5', 'confirmacion': 'Diferente123!'})
        self.assertContains(respuesta, 'Las contraseñas no coinciden')
        self.assertFalse(User.objects.filter(username='camila@example.org').exists())

    def test_portal_personal_sin_membresia_y_acceso_restringido(self):
        grupo = Group.objects.create(name='Socio')
        usuario = User.objects.create_user('socio_portal', password='clave-prueba')
        usuario.groups.add(grupo)
        socio = Socio.objects.create(usuario=usuario, rut='12345678-5', nombres='Camila', apellidos='Prueba', email='portal@example.org', telefono='123456789')
        cliente = Client()
        cliente.force_login(usuario)
        self.assertRedirects(cliente.get('/dashboard/'), '/socio/mi-cuenta/')
        self.assertContains(cliente.get('/socio/mi-cuenta/'), 'No tienes una membresía vigente')
        self.assertNotEqual(cliente.get('/admin-dashboard/').status_code, 200)
        socio.soft_delete()
        self.assertEqual(cliente.get('/socio/mi-cuenta/').status_code, 403)
        anonimo = Client()
        respuesta = anonimo.post('/login/', {'username': 'socio_portal', 'password': 'clave-prueba'})
        self.assertContains(respuesta, 'acceso de socio está desactivado')
        self.assertNotIn('_auth_user_id', anonimo.session)


class ClaseConSalaTestCase(TestCase):
    def setUp(self):
        from gestion.models import Sala
        self.sala = Sala.objects.create(nombre='Funcional', capacidad=20)
        from gestion.models import Actividad
        self.actividad = Actividad.objects.create(nombre='Yoga')
        self.profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        self.fecha = timezone.localdate()+timedelta(days=1)
        self.datos = {'actividad': self.actividad.pk, 'nombre': 'Yoga', 'profesor': self.profesor.pk, 'fecha': self.fecha.isoformat(), 'hora_inicio': '10:00', 'hora_fin': '11:00', 'capacidad_maxima': 20, 'sala_asignada': self.sala.pk}

    def test_editar_clase_avisa_solo_si_hay_cambios(self):
        from gestion.models import NotificacionRecepcion
        from gestion.forms import ClaseForm
        form = ClaseForm(self.datos)
        self.assertTrue(form.is_valid(), form.errors)
        clase = form.save()
        admin = User.objects.create_superuser('admin_cambio_recepcion', 'admin@example.org', 'clave-prueba')
        cliente = Client(); cliente.force_login(admin)
        ruta = f'/gestion/clases/{clase.pk}/editar/'
        cliente.post(ruta, self.datos)
        self.assertEqual(NotificacionRecepcion.objects.count(), 0)
        cliente.post(ruta, {**self.datos, 'hora_inicio': '11:00', 'hora_fin': '12:00'})
        self.assertEqual(NotificacionRecepcion.objects.count(), 1)
        self.assertIn('actualizaron', NotificacionRecepcion.objects.get().mensaje)

    def test_sitio_publico_muestra_oferta_sin_datos_internos(self):
        from gestion.models import Actividad
        Plan.objects.create(nombre='Plan visible', precio=25000, duracion_dias=30, permite_clases=False)
        oculto = Plan.objects.create(nombre='Plan oculto', precio=15000, duracion_dias=30)
        oculto.soft_delete()
        Actividad.objects.create(nombre='Próxima actividad', visible_sin_horario=True)
        Actividad.objects.create(nombre='Actividad oculta', visible_sin_horario=False)
        Clase.objects.create(nombre='Sesión privada', actividad=self.actividad, profesor=self.profesor, sala_asignada=self.sala, sala=self.sala.nombre, fecha=self.fecha, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        respuesta = Client().get('/')
        self.assertContains(respuesta, 'Plan visible')
        self.assertNotContains(respuesta, 'Plan oculto')
        self.assertContains(respuesta, 'Próxima actividad')
        self.assertContains(respuesta, 'Próximamente')
        self.assertNotContains(respuesta, 'Actividad oculta')
        self.assertContains(respuesta, 'Yoga')
        self.assertContains(respuesta, '10:00')
        self.assertNotContains(respuesta, self.profesor.nombres)
        self.assertNotContains(respuesta, self.sala.nombre)
        self.assertNotContains(respuesta, self.fecha.strftime('%d/%m/%Y'))

    def test_restaurar_clase_verifica_recursos_y_cruces(self):
        from gestion.forms import ClaseForm
        form = ClaseForm(self.datos)
        self.assertTrue(form.is_valid(), form.errors)
        clase = form.save()
        clase.soft_delete()
        admin = User.objects.create_superuser('admin_restaurar_clase', 'admin@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        ruta = f'/gestion/clases/{clase.pk}/restaurar/'
        self.profesor.soft_delete()
        cliente.post(ruta)
        clase.refresh_from_db()
        self.assertFalse(clase.activo)
        self.profesor.restore()
        otra = Clase.objects.create(nombre='Otra clase', profesor=self.profesor, fecha=self.fecha, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10, sala_asignada=self.sala, sala=self.sala.nombre)
        cliente.post(ruta)
        clase.refresh_from_db()
        self.assertFalse(clase.activo)
        otra.soft_delete()
        cliente.post(ruta)
        clase.refresh_from_db()
        self.assertTrue(clase.activo)

    def test_cancelacion_de_clase_devuelve_saldo_y_avisa_sin_promover_espera(self):
        from gestion.models import NotificacionSocio, NotificacionProfesor
        from gestion.services import saldo_disponible
        usuario = User.objects.create_user('profe_cancelacion', password='clave-prueba')
        self.profesor.usuario = usuario
        self.profesor.save()
        clase = Clase.objects.create(nombre='Yoga cancelación', profesor=self.profesor, fecha=self.fecha, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=1)
        socio = Socio.objects.create(rut='12345678-5', nombres='Ana', apellidos='Socio', email='ana@example.org', telefono='123456789')
        otro = Socio.objects.create(rut='12345679-3', nombres='Luis', apellidos='Socio', email='luis@example.org', telefono='123456789')
        plan = Plan.objects.create(nombre='Una clase cancelación', duracion_dias=30, precio=10000, permite_clases=True, limite_clases=1)
        membresia = Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=timezone.localdate(), fecha_fin=self.fecha)
        confirmada = Reserva.objects.create(clase=clase, socio=socio, membresia=membresia, estado='CONFIRMADA', consume_clase=True)
        espera = Reserva.objects.create(clase=clase, socio=otro, estado='LISTA_ESPERA')
        admin = User.objects.create_superuser('admin_cancelacion', 'admin@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        ruta = f'/clases/{clase.pk}/cancelar/'
        self.assertContains(cliente.get(ruta), 'Ana Socio')
        self.assertEqual(saldo_disponible(membresia), 0)
        self.assertEqual(cliente.post(ruta).status_code, 302)
        confirmada.refresh_from_db()
        espera.refresh_from_db()
        clase.refresh_from_db()
        self.assertFalse(clase.activo)
        self.assertEqual(confirmada.estado, 'CANCELADA')
        self.assertEqual(espera.estado, 'CANCELADA')
        self.assertEqual(saldo_disponible(membresia), 1)
        self.assertEqual(NotificacionSocio.objects.filter(tipo='CLASE_CANCELADA').count(), 2)
        self.assertEqual(NotificacionProfesor.objects.filter(clase=clase).count(), 1)
        cliente.post(ruta)
        self.assertEqual(NotificacionSocio.objects.filter(tipo='CLASE_CANCELADA').count(), 2)
        self.assertEqual(NotificacionProfesor.objects.filter(clase=clase).count(), 1)

    def test_cancelacion_no_permite_recepcion_ni_clases_pasadas(self):
        clase = Clase.objects.create(nombre='Clase pasada', profesor=self.profesor, fecha=timezone.localdate()-timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=1)
        ruta = f'/clases/{clase.pk}/cancelar/'
        recepcion = User.objects.create_user('recepcion_cancelacion', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        cliente = Client()
        cliente.force_login(recepcion)
        cliente.post(ruta)
        clase.refresh_from_db()
        self.assertTrue(clase.activo)
        admin = User.objects.create_superuser('admin_cancelacion_pasada', 'admin@example.org', 'clave-prueba')
        cliente.force_login(admin)
        cliente.post(ruta)
        clase.refresh_from_db()
        self.assertTrue(clase.activo)

    def test_avisos_al_desactivar_y_restaurar_una_clase(self):
        from gestion.forms import ClaseForm
        from gestion.models import NotificacionProfesor
        usuario = User.objects.create_user('profe_aviso_baja', password='clave-prueba')
        self.profesor.usuario = usuario
        self.profesor.save()
        form = ClaseForm(self.datos)
        self.assertTrue(form.is_valid(), form.errors)
        clase = form.save()
        admin = User.objects.create_superuser('admin_aviso_baja', 'admin@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        baja = f'/gestion/clases/{clase.pk}/desactivar/'
        alta = f'/gestion/clases/{clase.pk}/restaurar/'
        cliente.post(baja)
        cliente.post(baja)
        self.assertEqual(NotificacionProfesor.objects.filter(usuario=usuario, clase=clase).count(), 1)
        self.assertIn('desactivada', NotificacionProfesor.objects.get(usuario=usuario, clase=clase).mensaje)
        cliente.post(alta)
        cliente.post(alta)
        self.assertEqual(NotificacionProfesor.objects.filter(usuario=usuario, clase=clase).count(), 2)
        self.assertTrue(NotificacionProfesor.objects.filter(usuario=usuario, clase=clase, mensaje__contains='volvió').exists())

    def test_notificaciones_asignacion_cambios_y_reasignacion(self):
        from gestion.models import NotificacionProfesor
        grupo = Group.objects.create(name='Profesor')
        usuario = User.objects.create_user('profesor_aviso_clase', password='clave-prueba')
        usuario.groups.add(grupo)
        self.profesor.usuario = usuario
        self.profesor.save()
        otro_usuario = User.objects.create_user('profesor_reemplazo', password='clave-prueba')
        otro_usuario.groups.add(grupo)
        reemplazo = Profesor.objects.create(usuario=otro_usuario, nombres='Luis', apellidos='Prueba', especialidad='Yoga')
        admin = User.objects.create_superuser('admin_aviso_clase', 'admin@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        self.assertEqual(cliente.post('/gestion/clases/nuevo/', self.datos).status_code, 302)
        clase = Clase.objects.get(nombre='Yoga')
        self.assertEqual(NotificacionProfesor.objects.filter(usuario=usuario, clase=clase).count(), 1)
        ruta = f'/gestion/clases/{clase.pk}/editar/'
        self.assertEqual(cliente.post(ruta, self.datos).status_code, 302)
        self.assertEqual(NotificacionProfesor.objects.filter(usuario=usuario, clase=clase).count(), 1)
        datos_cambio = {**self.datos, 'hora_inicio': '12:00', 'hora_fin': '13:00'}
        self.assertEqual(cliente.post(ruta, datos_cambio).status_code, 302)
        self.assertEqual(NotificacionProfesor.objects.filter(usuario=usuario, clase=clase).count(), 2)
        self.assertEqual(cliente.post(ruta, {**datos_cambio, 'profesor': reemplazo.pk}).status_code, 302)
        self.assertEqual(NotificacionProfesor.objects.filter(usuario=usuario, clase=clase).count(), 3)
        self.assertEqual(NotificacionProfesor.objects.filter(usuario=otro_usuario, clase=clase).count(), 1)

    def test_capacidad_y_guardado_de_sala(self):
        from gestion.forms import ClaseForm
        form = ClaseForm({**self.datos, 'capacidad_maxima': 21})
        self.assertFalse(form.is_valid())
        self.assertIn('capacidad_maxima', form.errors)
        form = ClaseForm(self.datos)
        self.assertTrue(form.is_valid(), form.errors)
        clase = form.save()
        self.assertEqual(clase.actividad, self.actividad)
        self.assertEqual(clase.sala_asignada, self.sala)
        self.assertEqual(clase.sala, 'Funcional')

    def test_no_admite_cruces_y_si_clases_consecutivas(self):
        from gestion.forms import ClaseForm
        primera = ClaseForm(self.datos)
        self.assertTrue(primera.is_valid(), primera.errors)
        primera.save()
        form = ClaseForm({**self.datos, 'nombre': 'Otra', 'hora_inicio': '10:30', 'hora_fin': '11:30'})
        self.assertFalse(form.is_valid())
        self.assertIn('sala_asignada', form.errors)
        self.assertIn('profesor', form.errors)
        form = ClaseForm({**self.datos, 'nombre': 'Otra', 'hora_inicio': '11:00', 'hora_fin': '12:00'})
        self.assertTrue(form.is_valid(), form.errors)


class CoherenciaSalaTestCase(TestCase):
    def setUp(self):
        from gestion.models import Sala
        self.sala = Sala.objects.create(nombre='Sala funcional', capacidad=20)
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        self.clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=timezone.localdate()+timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=18, sala_asignada=self.sala, sala='Nombre anterior')

    def test_no_reducir_sala_por_debajo_de_su_clase(self):
        from gestion.forms import SalaForm
        form = SalaForm({'nombre': self.sala.nombre, 'capacidad': 17}, instance=self.sala)
        self.assertFalse(form.is_valid())
        self.assertIn('capacidad', form.errors)
        form = SalaForm({'nombre': self.sala.nombre, 'capacidad': 18}, instance=self.sala)
        self.assertTrue(form.is_valid(), form.errors)

    def test_api_muestra_sala_y_no_permite_editar_clase(self):
        admin = User.objects.create_superuser('admin_api_salas', 'admin@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        respuesta = cliente.get(f'/api/clases/{self.clase.pk}/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['sala'], self.sala.nombre)
        self.assertEqual(respuesta.json()['sala_asignada'], self.sala.pk)
        self.assertEqual(cliente.patch(f'/api/clases/{self.clase.pk}/', data='{}', content_type='application/json').status_code, 405)


class ProgramacionSimultaneaTestCase(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_dos_clases_simultaneas_no_comparten_sala(self):
        from gestion.models import Sala
        sala = Sala.objects.create(nombre='Sala concurrente', capacidad=20)
        from gestion.models import Actividad
        actividad = Actividad.objects.create(nombre='Yoga concurrente')
        profesores = [Profesor.objects.create(nombres=f'Profe {i}', apellidos='Prueba', especialidad='Yoga') for i in range(2)]
        admin = User.objects.create_superuser('admin_clases_simultaneas', 'admin@example.org', 'clave-prueba')
        fecha = (timezone.localdate()+timedelta(days=1)).isoformat()
        barrera = Barrier(2)

        def crear(i):
            close_old_connections()
            try:
                cliente = Client()
                cliente.force_login(admin)
                barrera.wait(timeout=10)
                respuesta = cliente.post('/gestion/clases/nuevo/', {'nombre': f'Clase {i}', 'profesor': profesores[i].pk, 'fecha': fecha, 'hora_inicio': '10:00', 'hora_fin': '11:00', 'capacidad_maxima': 10, 'sala_asignada': sala.pk, 'actividad': actividad.pk})
                return respuesta.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            estados = list(executor.map(crear, range(2)))
        self.assertEqual(sorted(estados), [200, 302])
        self.assertEqual(Clase.objects.filter(sala_asignada=sala).count(), 1)


class GestionActividadesTestCase(TestCase):
    def test_crear_actividad_y_convertirla_en_permanente(self):
        from gestion.models import Actividad
        admin = User.objects.create_superuser('admin_actividades', 'admin@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        datos = {'nombre': 'Kickboxing', 'descripcion': 'Clase grupal', 'tipo': 'TEMPORADA', 'visible_sin_horario': 'on'}
        self.assertEqual(cliente.post('/gestion/actividades/nuevo/', datos).status_code, 302)
        actividad = Actividad.objects.get(nombre='Kickboxing')
        self.assertEqual(actividad.tipo, 'TEMPORADA')
        self.assertEqual(cliente.post(f'/gestion/actividades/{actividad.pk}/editar/', {**datos, 'tipo': 'PERMANENTE'}).status_code, 302)
        actividad.refresh_from_db()
        self.assertEqual(actividad.tipo, 'PERMANENTE')
        self.assertContains(cliente.get('/gestion/actividades/'), 'Kickboxing')
        recepcion = User.objects.create_user('recepcion_actividades', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        cliente.force_login(recepcion)
        self.assertEqual(cliente.get('/gestion/actividades/').status_code, 403)
        self.assertEqual(cliente.post('/gestion/actividades/nuevo/', datos).status_code, 403)


class ActividadConSesionesTestCase(TestCase):
    def test_no_desactiva_actividad_con_sesion_futura(self):
        from gestion.models import Actividad, Sala
        actividad = Actividad.objects.create(nombre='Kickboxing', tipo='TEMPORADA')
        sala = Sala.objects.create(nombre='Sala funcional', capacidad=20)
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Kickboxing')
        Clase.objects.create(actividad=actividad, sala_asignada=sala, nombre='Kickboxing tarde', profesor=profesor, fecha=timezone.localdate()+timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=20)
        admin = User.objects.create_superuser('admin_actividad_clase', 'admin@example.org', 'clave-prueba')
        cliente = Client()
        cliente.force_login(admin)
        cliente.post(f'/gestion/actividades/{actividad.pk}/desactivar/')
        actividad.refresh_from_db()
        self.assertTrue(actividad.activo)


class ProteccionClaseConReservasTestCase(TestCase):
    def setUp(self):
        from gestion.models import Actividad, Sala
        self.actividad = Actividad.objects.create(nombre='Yoga')
        self.sala = Sala.objects.create(nombre='Sala yoga', capacidad=20)
        self.profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        self.socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='clases@example.org', telefono='123456789')
        self.fecha = timezone.localdate()+timedelta(days=1)
        self.clase = Clase.objects.create(actividad=self.actividad, sala_asignada=self.sala, sala=self.sala.nombre, nombre='Yoga tarde', profesor=self.profesor, fecha=self.fecha, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        self.reserva = Reserva.objects.create(socio=self.socio, clase=self.clase, estado='CONFIRMADA')
        self.datos = {'actividad': self.actividad.pk, 'nombre': self.clase.nombre, 'profesor': self.profesor.pk, 'fecha': self.fecha.isoformat(), 'hora_inicio': '10:00', 'hora_fin': '11:00', 'capacidad_maxima': 10, 'sala_asignada': self.sala.pk}

    def test_no_cambia_horario_si_hay_reservas(self):
        from gestion.forms import ClaseForm
        form = ClaseForm({**self.datos, 'hora_inicio': '11:00', 'hora_fin': '12:00'}, instance=self.clase)
        self.assertFalse(form.is_valid())
        self.assertIn('hora_inicio', form.errors)
        self.clase.refresh_from_db()
        self.assertEqual(self.clase.hora_inicio, time(10))

    def test_permite_ampliar_cupos_sin_cambiar_horario(self):
        from gestion.forms import ClaseForm
        form = ClaseForm({**self.datos, 'capacidad_maxima': 15}, instance=self.clase)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().capacidad_maxima, 15)

    def test_permite_cambiar_horario_sin_solicitudes_activas(self):
        from gestion.forms import ClaseForm
        self.reserva.estado = 'CANCELADA'
        self.reserva.save()
        form = ClaseForm({**self.datos, 'hora_inicio': '11:00', 'hora_fin': '12:00'}, instance=self.clase)
        self.assertTrue(form.is_valid(), form.errors)


class AsistenciaRecepcionTestCase(TestCase):
    def setUp(self):
        from datetime import datetime
        self.hoy = timezone.localdate()
        self.inicio = timezone.make_aware(datetime.combine(self.hoy+timedelta(days=1), time(10)))
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='asistencia@example.org', telefono='123456789')
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=self.inicio.date(), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=5)
        self.reserva = Reserva.objects.create(socio=socio, clase=clase, estado='CONFIRMADA')
        self.usuario = User.objects.create_user('recepcion_asistencia', password='clave-prueba')
        self.usuario.groups.add(Group.objects.create(name='Recepcion'))
        self.entrada = RegistroAcceso.objects.create(socio=socio, tipo='ENTRADA', resultado='PERMITIDO', motivo='Prueba', fecha_hora=self.inicio-timedelta(minutes=20))

    def test_confirma_con_entrada_reciente_y_no_dos_veces(self):
        from unittest.mock import patch
        from gestion.services import confirmar_asistencia_recepcion
        with patch('gestion.services.timezone.now', return_value=self.inicio-timedelta(minutes=10)):
            self.assertTrue(confirmar_asistencia_recepcion(self.reserva.pk, self.usuario)[0])
            self.assertFalse(confirmar_asistencia_recepcion(self.reserva.pk, self.usuario)[0])
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.asistencia, 'ASISTIO')
        self.assertEqual(self.reserva.asistencia_registrada_por, self.usuario)

    def test_no_confirma_si_entrada_fue_muy_temprano_o_no_esta_dentro(self):
        from unittest.mock import patch
        from gestion.services import confirmar_asistencia_recepcion
        self.entrada.fecha_hora = self.inicio-timedelta(hours=2)
        self.entrada.save()
        with patch('gestion.services.timezone.now', return_value=self.inicio-timedelta(minutes=10)):
            self.assertFalse(confirmar_asistencia_recepcion(self.reserva.pk, self.usuario)[0])
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.asistencia, 'PENDIENTE')

    def test_confirma_al_iniciar_y_cierra_cinco_minutos_despues(self):
        from unittest.mock import patch
        from gestion.services import confirmar_asistencia_recepcion
        with patch('gestion.services.timezone.now', return_value=self.inicio+timedelta(minutes=5, seconds=1)):
            self.assertFalse(confirmar_asistencia_recepcion(self.reserva.pk, self.usuario)[0])
        with patch('gestion.services.timezone.now', return_value=self.inicio+timedelta(minutes=1)):
            self.assertTrue(confirmar_asistencia_recepcion(self.reserva.pk, self.usuario)[0])

    def test_solo_personal_autorizado_puede_confirmar(self):
        usuario = User.objects.create_user('sin_permiso', password='clave-prueba')
        cliente = Client()
        cliente.force_login(usuario)
        respuesta = cliente.post(f'/reservas/{self.reserva.pk}/asistencia/')
        self.assertNotEqual(respuesta.status_code, 200)
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.asistencia, 'PENDIENTE')


class AccesoCodigoYSalasTestCase(TestCase):
    def setUp(self):
        self.socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='codigo-unico@example.org', telefono='123456789')
        plan = Plan.objects.create(nombre='Plan código único', duracion_dias=30, precio=35000)
        hoy = timezone.localdate()
        Membresia.objects.create(socio=self.socio, plan=plan, fecha_inicio=hoy, fecha_fin=hoy+timedelta(days=29))
        self.usuario = User.objects.create_user('recepcion_salas_codigo', password='clave-prueba')
        self.usuario.groups.add(Group.objects.create(name='Recepcion'))
        self.cliente = Client(); self.cliente.force_login(self.usuario)

    def test_solo_codigo_valida_acceso(self):
        for identificador in (self.socio.rut, str(self.socio.pk)):
            respuesta = self.cliente.post('/api/acceso/validar/', {'identificador': identificador, 'tipo': 'ENTRADA'}, content_type='application/json')
            self.assertFalse(respuesta.json()['autorizado'])
        self.assertTrue(self.cliente.post('/api/acceso/validar/', {'identificador': self.socio.codigo_qr, 'tipo': 'ENTRADA'}, content_type='application/json').json()['autorizado'])
        pagina = self.cliente.get('/control-acceso/')
        self.assertContains(pagina, 'Código de acceso')
        self.assertNotContains(pagina, 'Selecciona un socio')

    def test_cupos_son_reservas_y_solo_personal_puede_consultarlos(self):
        from gestion.models import Sala
        sala = Sala.objects.create(nombre='Sala yoga', capacidad=20)
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga cupos', profesor=profesor, fecha=timezone.localdate(), hora_inicio=time(0), hora_fin=time(23,59), capacidad_maxima=10, sala_asignada=sala)
        Reserva.objects.create(socio=self.socio, clase=clase, estado='CONFIRMADA')
        datos = self.cliente.get('/api/salas/disponibilidad/').json()['clases']
        self.assertEqual((datos[0]['reservados'], datos[0]['disponibles'], datos[0]['capacidad']), (1, 9, 10))
        self.assertNotEqual(Client().get('/api/salas/disponibilidad/').status_code, 200)


class FlujoEntradaAsistenciaTestCase(TestCase):
    def test_entrada_muestra_clase_sin_marcar_asistencia(self):
        from datetime import datetime
        ahora = timezone.localtime()
        inicio = (ahora+timedelta(minutes=20)).replace(second=0, microsecond=0)
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='flujo@example.org', telefono='123456789')
        plan = Plan.objects.create(nombre='Plan flujo', duracion_dias=30, precio=35000, permite_clases=True)
        Membresia.objects.create(socio=socio, plan=plan, fecha_inicio=ahora.date(), fecha_fin=ahora.date()+timedelta(days=29))
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=inicio.date(), hora_inicio=inicio.time().replace(tzinfo=None), hora_fin=(inicio+timedelta(minutes=45)).time().replace(tzinfo=None), capacidad_maxima=5)
        reserva = Reserva.objects.create(socio=socio, clase=clase, estado='CONFIRMADA')
        recepcion = User.objects.create_user('recepcion_flujo', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        cliente = Client()
        cliente.force_login(recepcion)
        respuesta = cliente.post('/api/acceso/validar/', data={'identificador': socio.codigo_qr, 'tipo': 'ENTRADA'}, content_type='application/json')
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()['autorizado'])
        self.assertEqual(respuesta.json()['clases_proximas'][0]['reserva_id'], reserva.pk)
        reserva.refresh_from_db()
        self.assertEqual(reserva.asistencia, 'PENDIENTE')
        respuesta = cliente.post(f'/api/reservas/{reserva.pk}/confirmar-asistencia/')
        self.assertTrue(respuesta.json()['exito'])
        reserva.refresh_from_db()
        self.assertEqual(reserva.asistencia, 'ASISTIO')

    def test_asistencia_api_exige_autenticacion_y_post(self):
        cliente = Client()
        self.assertEqual(cliente.post('/api/reservas/999/confirmar-asistencia/').status_code, 302)
        recepcion = User.objects.create_user('recepcion_flujo_2', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        cliente.force_login(recepcion)
        self.assertEqual(cliente.get('/api/reservas/999/confirmar-asistencia/').status_code, 405)


class PortalProfesorTestCase(TestCase):
    def test_profesor_ve_solo_sus_clases_y_no_accede_a_recepcion(self):
        grupo = Group.objects.create(name='Profesor')
        usuario = User.objects.create_user('profe_usuario', password='clave-prueba')
        usuario.groups.add(grupo)
        profesor = Profesor.objects.create(usuario=usuario, nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        otro = Profesor.objects.create(nombres='Luis', apellidos='Prueba', especialidad='Yoga')
        fecha = timezone.localdate()+timedelta(days=1)
        Clase.objects.create(nombre='Clase propia', profesor=profesor, fecha=fecha, hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=5)
        Clase.objects.create(nombre='Clase ajena', profesor=otro, fecha=fecha, hora_inicio=time(12), hora_fin=time(13), capacidad_maxima=5)
        cliente = Client()
        cliente.force_login(usuario)
        self.assertRedirects(cliente.get('/dashboard/'), '/profesor/clases/')
        respuesta = cliente.get('/profesor/clases/')
        self.assertContains(respuesta, 'Clase propia')
        self.assertNotContains(respuesta, 'Clase ajena')
        self.assertNotContains(respuesta, 'Panel de recepción')
        self.assertNotEqual(cliente.get('/socios/').status_code, 200)
        self.assertNotEqual(cliente.get('/admin/').status_code, 200)

    def test_cuenta_sin_perfil_de_profesor_no_accede(self):
        usuario = User.objects.create_user('profe_sin_perfil', password='clave-prueba')
        usuario.groups.add(Group.objects.create(name='Profesor'))
        cliente = Client()
        cliente.force_login(usuario)
        self.assertEqual(cliente.get('/profesor/clases/').status_code, 403)
        self.assertEqual(cliente.get('/dashboard/').status_code, 403)


class ProfesorAsistenciaTestCase(TestCase):
    def setUp(self):
        grupo = Group.objects.create(name='Profesor')
        self.usuario = User.objects.create_user('profe_asistencia', password='clave-prueba')
        self.usuario.groups.add(grupo)
        self.profesor = Profesor.objects.create(usuario=self.usuario, nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        otro_usuario = User.objects.create_user('otro_profe', password='clave-prueba')
        otro_usuario.groups.add(grupo)
        self.otro_profesor = Profesor.objects.create(usuario=otro_usuario, nombres='Luis', apellidos='Prueba', especialidad='Yoga')
        self.clase = Clase.objects.create(nombre='Yoga propia', profesor=self.profesor, fecha=timezone.localdate(), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=5)
        self.ajena = Clase.objects.create(nombre='Yoga ajena', profesor=self.otro_profesor, fecha=timezone.localdate(), hora_inicio=time(12), hora_fin=time(13), capacidad_maxima=5)
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='profesor@example.org', telefono='123456789')
        self.reserva = Reserva.objects.create(socio=socio, clase=self.clase, estado='CONFIRMADA')
        self.cliente = Client()
        self.cliente.force_login(self.usuario)

    def test_puede_marcar_y_corregir_con_historial(self):
        from gestion.models import HistorialAsistencia
        self.assertContains(self.cliente.get(f'/profesor/clases/{self.clase.pk}/'), 'Camila')
        url = f'/profesor/clases/{self.clase.pk}/reservas/{self.reserva.pk}/asistencia/'
        self.assertEqual(self.cliente.post(url, {'asistencia': 'ASISTIO'}).status_code, 302)
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.asistencia, 'ASISTIO')
        self.assertEqual(self.cliente.post(url, {'asistencia': 'PENDIENTE'}).status_code, 302)
        self.assertEqual(HistorialAsistencia.objects.filter(reserva=self.reserva, origen='PROFESOR').count(), 2)

    def test_no_accede_a_clase_ajena_y_no_marca_ausencia_directamente(self):
        self.assertEqual(self.cliente.get(f'/profesor/clases/{self.ajena.pk}/').status_code, 404)
        url = f'/profesor/clases/{self.clase.pk}/reservas/{self.reserva.pk}/asistencia/'
        self.assertEqual(self.cliente.post(url, {'asistencia': 'NO_ASISTIO'}).status_code, 403)
        self.reserva.refresh_from_db()
        self.assertEqual(self.reserva.asistencia, 'PENDIENTE')


class CierreAsistenciaProfesorTestCase(TestCase):
    def setUp(self):
        grupo = Group.objects.create(name='Profesor')
        self.usuario = User.objects.create_user('profe_cierre', password='clave-prueba')
        self.usuario.groups.add(grupo)
        profesor = Profesor.objects.create(usuario=self.usuario, nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        self.clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=timezone.localdate()-timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=5)
        self.reservas = []
        for i in range(2):
            socio = Socio.objects.create(rut=f'1234567{i}-5', nombres='Socio', apellidos=str(i), email=f'cierre{i}@example.org', telefono='123456789')
            self.reservas.append(Reserva.objects.create(socio=socio, clase=self.clase, estado='CONFIRMADA', asistencia='ASISTIO' if i == 0 else 'PENDIENTE'))
        self.cliente = Client()
        self.cliente.force_login(self.usuario)

    def test_aviso_de_indisponibilidad_y_revision_por_administracion(self):
        from gestion.models import AvisoIndisponibilidad
        self.clase.fecha = timezone.localdate() + timedelta(days=3)
        self.clase.save()
        ruta = f'/profesor/clases/{self.clase.pk}/avisar-indisponibilidad/'
        self.cliente.post(ruta, {'motivo': '  '})
        self.assertFalse(AvisoIndisponibilidad.objects.exists())
        self.cliente.post(ruta, {'motivo': 'No podré asistir'})
        self.cliente.post(ruta, {'motivo': 'Aviso duplicado'})
        self.assertEqual(AvisoIndisponibilidad.objects.count(), 1)
        aviso = AvisoIndisponibilidad.objects.get()
        self.assertTrue(Clase.objects.filter(pk=self.clase.pk).exists())
        self.assertEqual(self.cliente.get('/gestion/avisos-profesores/').status_code, 302)
        self.assertEqual(self.cliente.post(f'/gestion/avisos-profesores/{aviso.pk}/revisar/').status_code, 302)
        aviso.refresh_from_db()
        self.assertIsNone(aviso.revisado_en)
        admin = User.objects.create_superuser('admin_aviso_profesor', 'admin@example.org', 'clave-prueba')
        self.cliente.force_login(admin)
        self.assertContains(self.cliente.get('/admin-dashboard/'), 'Hay avisos de profesores pendientes')
        self.assertContains(self.cliente.get('/gestion/avisos-profesores/'), 'No podré asistir')
        self.assertEqual(self.cliente.post(f'/gestion/avisos-profesores/{aviso.pk}/revisar/').status_code, 302)
        aviso.refresh_from_db()
        self.assertEqual(aviso.revisado_por, admin)
        self.assertIsNotNone(aviso.revisado_en)
        self.assertNotContains(self.cliente.get('/admin-dashboard/'), 'Hay avisos de profesores pendientes')

    def test_aviso_solo_clases_propias_y_futuras(self):
        from gestion.models import AvisoIndisponibilidad
        ruta = f'/profesor/clases/{self.clase.pk}/avisar-indisponibilidad/'
        self.cliente.post(ruta, {'motivo': 'Clase pasada'})
        self.assertFalse(AvisoIndisponibilidad.objects.exists())
        otro = User.objects.create_user('otro_aviso_profesor', password='clave-prueba')
        otro.groups.add(Group.objects.get(name='Profesor'))
        Profesor.objects.create(usuario=otro, nombres='Otro', apellidos='Profesor', especialidad='Yoga')
        self.cliente.force_login(otro)
        self.assertEqual(self.cliente.post(ruta, {'motivo': 'No asignado'}).status_code, 404)

    def test_desactivar_y_restaurar_profesor_controla_su_acceso(self):
        profesor = Profesor.objects.get(usuario=self.usuario)
        admin = User.objects.create_superuser('admin_baja_profesor', 'admin@example.org', 'clave-prueba')
        admin_cliente = Client()
        admin_cliente.force_login(admin)
        baja = f'/gestion/profesores/{profesor.pk}/desactivar/'
        restaurar = f'/gestion/profesores/{profesor.pk}/restaurar/'

        # Primero se deben resolver las clases que siguen programadas.
        self.clase.fecha = timezone.localdate() + timedelta(days=1)
        self.clase.save()
        admin_cliente.post(baja)
        profesor.refresh_from_db()
        self.assertTrue(profesor.activo)

        self.clase.fecha = timezone.localdate() - timedelta(days=1)
        self.clase.save()
        admin_cliente.post(baja)
        profesor.refresh_from_db()
        self.assertFalse(profesor.activo)
        self.assertEqual(self.cliente.get('/profesor/perfil/').status_code, 403)
        nuevo_cliente = Client()
        respuesta = nuevo_cliente.post('/login/', {'username': self.usuario.username, 'password': 'clave-prueba'})
        self.assertContains(respuesta, 'acceso de profesor está desactivado')
        self.assertNotIn('_auth_user_id', nuevo_cliente.session)

        admin_cliente.post(restaurar)
        profesor.refresh_from_db()
        self.assertTrue(profesor.activo)
        self.assertEqual(nuevo_cliente.post('/login/', {'username': self.usuario.username, 'password': 'clave-prueba'}).status_code, 302)
        self.assertEqual(nuevo_cliente.get('/profesor/perfil/').status_code, 200)

    def test_cambio_de_clave_exige_actual_y_conserva_sesion(self):
        ruta = '/profesor/perfil/cambiar-clave/'
        datos = {'old_password': 'incorrecta', 'new_password1': 'MontanaSegura789!', 'new_password2': 'MontanaSegura789!'}
        self.assertEqual(self.cliente.post(ruta, datos).status_code, 200)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password('clave-prueba'))
        datos['old_password'] = 'clave-prueba'
        datos['new_password2'] = 'DistintaSegura987!'
        self.assertEqual(self.cliente.post(ruta, datos).status_code, 200)
        datos['new_password2'] = datos['new_password1']
        self.assertRedirects(self.cliente.post(ruta, datos), '/profesor/perfil/')
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password('MontanaSegura789!'))
        self.assertEqual(self.cliente.get('/profesor/perfil/').status_code, 200)
        otro = User.objects.create_user('recepcion_clave', password='clave-prueba')
        otro.groups.add(Group.objects.create(name='Recepcion'))
        self.cliente.force_login(otro)
        self.assertEqual(self.cliente.get(ruta).status_code, 403)

    def test_profesor_actualiza_solo_su_contacto(self):
        perfil = Profesor.objects.get(usuario=self.usuario)
        respuesta = self.cliente.post('/profesor/perfil/', {'email': 'nuevo@example.org', 'telefono': '987654321', 'nombres': 'Nombre cambiado'})
        self.assertEqual(respuesta.status_code, 302)
        perfil.refresh_from_db()
        self.assertEqual(perfil.email, 'nuevo@example.org')
        self.assertEqual(perfil.telefono, '987654321')
        self.assertEqual(perfil.nombres, 'Ana')
        respuesta = self.cliente.post('/profesor/perfil/', {'email': 'correo-invalido', 'telefono': '123'})
        self.assertEqual(respuesta.status_code, 200)
        perfil.refresh_from_db()
        self.assertEqual(perfil.email, 'nuevo@example.org')
        otro = User.objects.create_user('recepcion_perfil', password='clave-prueba')
        otro.groups.add(Group.objects.create(name='Recepcion'))
        self.cliente.force_login(otro)
        self.assertEqual(self.cliente.get('/profesor/perfil/').status_code, 403)

    def test_cierre_confirma_solo_pendientes_y_bloquea_cambios(self):
        from gestion.models import HistorialAsistencia
        url = f'/clases/{self.clase.pk}/cerrar-asistencia/'
        self.assertContains(self.cliente.get(url), 'Socio')
        self.assertEqual(self.cliente.post(url, {'confirmar': 'si'}).status_code, 302)
        self.clase.refresh_from_db()
        self.assertIsNotNone(self.clase.asistencia_cerrada_en)
        self.reservas[0].refresh_from_db()
        self.reservas[1].refresh_from_db()
        self.assertEqual(self.reservas[0].asistencia, 'ASISTIO')
        self.assertEqual(self.reservas[1].asistencia, 'NO_ASISTIO')
        self.assertEqual(HistorialAsistencia.objects.filter(reserva=self.reservas[1], origen='CIERRE').count(), 1)
        self.cliente.post(url, {'confirmar': 'si'})
        self.assertEqual(HistorialAsistencia.objects.filter(reserva=self.reservas[1], origen='CIERRE').count(), 1)
        self.cliente.post(f'/profesor/clases/{self.clase.pk}/reservas/{self.reservas[0].pk}/asistencia/', {'asistencia': 'PENDIENTE'})
        self.reservas[0].refresh_from_db()
        self.assertEqual(self.reservas[0].asistencia, 'ASISTIO')

    def test_no_cierra_antes_de_terminar_y_recepcion_no_puede_cerrar(self):
        self.clase.fecha = timezone.localdate()+timedelta(days=1)
        self.clase.save()
        url = f'/clases/{self.clase.pk}/cerrar-asistencia/'
        self.cliente.post(url, {'confirmar': 'si'})
        self.clase.refresh_from_db()
        self.assertIsNone(self.clase.asistencia_cerrada_en)
        recepcion = User.objects.create_user('recepcion_cierre', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        self.cliente.force_login(recepcion)
        self.assertEqual(self.cliente.post(url, {'confirmar': 'si'}).status_code, 403)


class SolicitudCorreccionTestCase(TestCase):
    def setUp(self):
        from gestion.models import SolicitudCorreccionAsistencia
        grupo = Group.objects.create(name='Profesor')
        self.profe = User.objects.create_user('profe_solicitud', password='clave-prueba')
        self.profe.groups.add(grupo)
        profesor = Profesor.objects.create(usuario=self.profe, nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        self.admin = User.objects.create_superuser('admin_solicitud', 'admin@example.org', 'clave-prueba')
        self.clase = Clase.objects.create(nombre='Yoga', profesor=profesor, fecha=timezone.localdate()-timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=5, asistencia_cerrada_en=timezone.now())
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='solicitud@example.org', telefono='123456789')
        self.reserva = Reserva.objects.create(socio=socio, clase=self.clase, estado='CONFIRMADA', asistencia='NO_ASISTIO')
        self.cliente = Client()
        self.cliente.force_login(self.profe)
        self.url = f'/profesor/clases/{self.clase.pk}/reservas/{self.reserva.pk}/solicitar-correccion/'

    def test_notificaciones_son_privadas_y_se_leen_al_abrir(self):
        from gestion.models import SolicitudCorreccionAsistencia, NotificacionProfesor
        self.cliente.post(self.url, {'estado_solicitado': 'ASISTIO', 'motivo': 'Revisión de asistencia'})
        solicitud = SolicitudCorreccionAsistencia.objects.get()
        self.cliente.force_login(self.admin)
        self.cliente.post(f'/solicitudes/asistencia/{solicitud.pk}/resolver/', {'decision': 'APROBADA'})
        aviso = NotificacionProfesor.objects.get(solicitud=solicitud)
        self.assertEqual(self.cliente.get('/profesor/notificaciones/').status_code, 403)
        otro = User.objects.create_user('otro_profesor_aviso', password='clave-prueba')
        otro.groups.add(Group.objects.get(name='Profesor'))
        Profesor.objects.create(usuario=otro, nombres='Otro', apellidos='Profesor', especialidad='Yoga')
        self.cliente.force_login(otro)
        self.assertNotContains(self.cliente.get('/profesor/notificaciones/'), aviso.mensaje)
        aviso.refresh_from_db()
        self.assertFalse(aviso.leida)
        self.cliente.force_login(self.profe)
        self.assertContains(self.cliente.get('/profesor/clases/'), 'Hay notificaciones sin leer')
        self.assertContains(self.cliente.get('/profesor/notificaciones/'), aviso.mensaje)
        aviso.refresh_from_db()
        self.assertTrue(aviso.leida)
        self.assertNotContains(self.cliente.get('/profesor/clases/'), 'Hay notificaciones sin leer')
        self.assertContains(self.cliente.get('/profesor/notificaciones/'), aviso.mensaje)

    def test_solicitar_y_aprobar_con_historial(self):
        from gestion.models import SolicitudCorreccionAsistencia, HistorialAsistencia
        self.cliente.post(self.url, {'estado_solicitado': 'ASISTIO', 'motivo': 'Se comprobó su presencia'})
        solicitud = SolicitudCorreccionAsistencia.objects.get()
        self.cliente.post(self.url, {'estado_solicitado': 'ASISTIO', 'motivo': 'Duplicada'})
        self.assertEqual(SolicitudCorreccionAsistencia.objects.count(), 1)
        self.assertEqual(self.cliente.post(f'/solicitudes/asistencia/{solicitud.pk}/resolver/', {'decision': 'APROBADA'}).status_code, 302)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'PENDIENTE')
        self.cliente.force_login(self.admin)
        self.assertContains(self.cliente.get('/solicitudes/asistencia/'), 'Se comprobó su presencia')
        self.cliente.post(f'/solicitudes/asistencia/{solicitud.pk}/resolver/', {'decision': 'APROBADA'})
        solicitud.refresh_from_db()
        self.reserva.refresh_from_db()
        self.assertEqual(solicitud.estado, 'APROBADA')
        self.assertEqual(self.reserva.asistencia, 'ASISTIO')
        self.assertTrue(HistorialAsistencia.objects.filter(reserva=self.reserva, origen='ADMINISTRACION').exists())
        from gestion.models import NotificacionProfesor
        self.assertEqual(NotificacionProfesor.objects.filter(usuario=self.profe, solicitud=solicitud, leida=False).count(), 1)
        self.cliente.force_login(self.profe)
        self.assertContains(self.cliente.get('/profesor/solicitudes/'), 'Aprobada')

    def test_rechazo_no_modifica_asistencia_y_exige_motivo(self):
        from gestion.models import SolicitudCorreccionAsistencia
        self.cliente.post(self.url, {'estado_solicitado': 'ASISTIO', 'motivo': '  '})
        self.assertFalse(SolicitudCorreccionAsistencia.objects.exists())
        self.cliente.post(self.url, {'estado_solicitado': 'ASISTIO', 'motivo': 'Solicita revisión'})
        solicitud = SolicitudCorreccionAsistencia.objects.get()
        self.cliente.force_login(self.admin)
        self.cliente.post(f'/solicitudes/asistencia/{solicitud.pk}/resolver/', {'decision': 'RECHAZADA'})
        self.reserva.refresh_from_db()
        solicitud.refresh_from_db()
        self.assertEqual(self.reserva.asistencia, 'NO_ASISTIO')
        self.assertEqual(solicitud.estado, 'RECHAZADA')
        from gestion.models import NotificacionProfesor
        self.assertTrue(NotificacionProfesor.objects.filter(usuario=self.profe, solicitud=solicitud).exists())
        self.cliente.force_login(self.profe)
        self.assertContains(self.cliente.get('/profesor/solicitudes/'), 'Rechazada')


class PrivacidadSolicitudesProfesorTestCase(TestCase):
    def test_profesor_no_ve_solicitudes_de_otros(self):
        from gestion.models import SolicitudCorreccionAsistencia
        grupo = Group.objects.create(name='Profesor')
        usuarios = [User.objects.create_user(f'profe_privacidad_{i}', password='clave-prueba') for i in range(2)]
        for usuario in usuarios:
            usuario.groups.add(grupo)
        profesores = [Profesor.objects.create(usuario=usuarios[i], nombres=f'Profe {i}', apellidos='Prueba', especialidad='Yoga') for i in range(2)]
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='privacidad@example.org', telefono='123456789')
        clase = Clase.objects.create(nombre='Clase ajena privada', profesor=profesores[1], fecha=timezone.localdate(), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=5)
        reserva = Reserva.objects.create(socio=socio, clase=clase, estado='CONFIRMADA', asistencia='NO_ASISTIO')
        SolicitudCorreccionAsistencia.objects.create(reserva=reserva, solicitante=usuarios[1], estado_solicitado='ASISTIO', motivo='Motivo privado')
        cliente = Client()
        cliente.force_login(usuarios[0])
        self.assertNotContains(cliente.get('/profesor/solicitudes/'), 'Motivo privado')
        self.assertNotEqual(cliente.get('/solicitudes/asistencia/').status_code, 200)


class CuentasPersonalRutTestCase(TestCase):
    def test_recepcion_activa_y_recupera_por_pregunta(self):
        from gestion.models import PerfilPersonal
        admin = User.objects.create_superuser('admin_personal', password='clave-admin')
        cliente = Client()
        cliente.force_login(admin)
        creado = cliente.post('/gestion/recepcionistas/nuevo/', {'usuario': 'recepcion_nueva', 'rut': '12345678-5', 'nombres': 'Ana', 'apellidos': 'Prueba', 'correo': 'ana.personal@example.org', 'telefono': '912345678'})
        self.assertEqual(creado.status_code, 200)
        codigo = creado.context['codigo']
        usuario = User.objects.get(username='recepcion_nueva')
        anonimo = Client()
        datos = {'rut': '12.345.678-5', 'codigo': codigo, 'clave': 'MontanaSegura789!', 'confirmacion': 'MontanaSegura789!', 'pregunta': 'MASCOTA', 'respuesta': 'Copito'}
        self.assertEqual(anonimo.post('/activar-recepcion/', datos).status_code, 302)
        perfil = PerfilPersonal.objects.get(usuario=usuario)
        self.assertNotIn('Copito', perfil.respuesta_seguridad_hash)
        self.assertEqual(anonimo.post('/recuperar-cuenta/', {'email': usuario.email}).status_code, 302)
        self.assertEqual(anonimo.post('/recuperar-cuenta/pregunta/', {'respuesta': 'Copito'}).status_code, 302)
        self.assertEqual(anonimo.post('/recuperar-cuenta/nueva-clave/', {'clave': 'NuevaClaveSegura123!', 'confirmacion': 'NuevaClaveSegura123!'}).status_code, 302)
        usuario.refresh_from_db()
        self.assertTrue(usuario.check_password('NuevaClaveSegura123!'))

    def test_profesor_sin_rut_no_puede_crear_acceso(self):
        admin = User.objects.create_superuser('admin_profesor_rut', password='clave-admin')
        profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        cliente = Client()
        cliente.force_login(admin)
        self.assertEqual(cliente.post(f'/profesores/{profesor.pk}/crear-cuenta/', {'usuario': 'profe'}).status_code, 302)
        profesor.refresh_from_db()
        self.assertIsNone(profesor.usuario_id)


class ActivacionRecepcionTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin_recepcion', password='clave-administrador')
        self.cliente = Client()
        self.cliente.force_login(self.admin)

    def test_creacion_activacion_y_restriccion_de_administracion(self):
        from gestion.models import ActivacionRecepcion
        respuesta = self.cliente.post('/gestion/recepcionistas/nuevo/', {'usuario': 'recepcion', 'nombres': 'Ana', 'apellidos': 'Prueba', 'correo': 'ana@example.org', 'rut': '12345678-5', 'telefono': '912345678'})
        self.assertEqual(respuesta.status_code, 200)
        codigo = respuesta.context['codigo']
        usuario = User.objects.get(username='recepcion')
        self.assertFalse(usuario.has_usable_password())
        self.assertFalse(usuario.is_staff)
        self.assertNotIn(codigo, ActivacionRecepcion.objects.get(usuario=usuario).codigo_hash)
        visitante = Client()
        datos = {'rut': '12345678-5', 'pregunta': 'MASCOTA', 'respuesta': 'Copito', 'codigo': codigo, 'clave': 'MontanaSegura789!', 'confirmacion': 'MontanaSegura789!'}
        self.assertEqual(visitante.post('/activar-recepcion/', datos).status_code, 302)
        self.assertEqual(visitante.post('/activar-recepcion/', datos).status_code, 200)
        self.assertTrue(visitante.login(username='recepcion', password=datos['clave']))
        self.assertRedirects(visitante.get('/dashboard/'), '/recepcion-dashboard/')
        self.assertNotEqual(visitante.get('/gestion/recepcionistas/').status_code, 200)
        self.assertNotEqual(visitante.post('/gestion/recepcionistas/nuevo/', {}).status_code, 200)

    def test_regeneracion_y_bloqueo_de_intentos(self):
        from gestion.models import ActivacionRecepcion
        codigo = self.cliente.post('/gestion/recepcionistas/nuevo/', {'usuario': 'auxiliar', 'nombres': 'Ana', 'apellidos': 'Prueba', 'correo': 'auxiliar@example.org', 'rut': '12345678-5', 'telefono': '912345678'}).context['codigo']
        usuario = User.objects.get(username='auxiliar')
        ruta = f'/gestion/recepcionistas/{usuario.pk}/codigo/'
        nuevo = self.cliente.post(ruta).context['codigo']
        visitante = Client()
        datos = {'rut': '12345678-5', 'pregunta': 'MASCOTA', 'respuesta': 'Copito', 'codigo': codigo, 'clave': 'MontanaSegura789!', 'confirmacion': 'MontanaSegura789!'}
        for _ in range(3):
            visitante.post('/activar-recepcion/', datos)
        self.assertIsNotNone(ActivacionRecepcion.objects.get(usuario=usuario).bloqueado_hasta)
        datos['codigo'] = nuevo
        self.assertContains(visitante.post('/activar-recepcion/', datos), 'Demasiados intentos')
        nuevo = self.cliente.post(ruta).context['codigo']
        datos['codigo'] = nuevo
        self.assertEqual(visitante.post('/activar-recepcion/', datos).status_code, 302)


class ActivacionProfesorTestCase(TestCase):
    def setUp(self):
        self.profesor = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga', rut='12345678-5', email='profesor@example.org')
        self.admin = User.objects.create_superuser('admin_activacion', 'admin@example.org', 'clave-prueba')
        self.cliente = Client()
        self.cliente.force_login(self.admin)

    def test_regenerar_invalida_codigo_anterior_y_permite_activacion(self):
        from gestion.models import ActivacionProfesor
        anterior = self.cliente.post(f'/profesores/{self.profesor.pk}/crear-cuenta/', {'usuario': 'ana_profe'}).context['codigo']
        ruta = f'/profesores/{self.profesor.pk}/regenerar-codigo/'
        self.assertEqual(self.cliente.get(ruta).status_code, 200)
        nuevo = self.cliente.post(ruta).context['codigo']
        self.assertNotEqual(anterior, nuevo)
        anonimo = Client()
        datos = {'rut': '12345678-5', 'pregunta': 'MASCOTA', 'respuesta': 'Copito', 'codigo': anterior, 'clave': 'MontanaSegura789!', 'confirmacion': 'MontanaSegura789!'}
        self.assertEqual(anonimo.post('/activar-cuenta/', datos).status_code, 200)
        datos['codigo'] = nuevo
        self.assertEqual(anonimo.post('/activar-cuenta/', datos).status_code, 302)
        self.assertEqual(self.cliente.post(ruta).status_code, 302)
        self.assertIsNotNone(ActivacionProfesor.objects.get(profesor=self.profesor).usado_en)

    def test_regenerar_no_autorizado_y_restablece_bloqueo(self):
        from gestion.models import ActivacionProfesor
        self.cliente.post(f'/profesores/{self.profesor.pk}/crear-cuenta/', {'usuario': 'ana_profe'})
        ruta = f'/profesores/{self.profesor.pk}/regenerar-codigo/'
        recepcion = User.objects.create_user('recepcion_regenerar', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        otro = Client()
        otro.force_login(recepcion)
        self.assertNotEqual(otro.post(ruta).status_code, 200)
        activacion = ActivacionProfesor.objects.get(profesor=self.profesor)
        activacion.intentos_fallidos = 3
        activacion.bloqueado_hasta = timezone.now() + timedelta(minutes=15)
        activacion.save()
        self.assertEqual(self.cliente.post(ruta).status_code, 200)
        activacion.refresh_from_db()
        self.assertEqual(activacion.intentos_fallidos, 0)
        self.assertIsNone(activacion.bloqueado_hasta)

    def test_crea_codigo_una_vez_y_profesor_activa_su_cuenta(self):
        from gestion.models import ActivacionProfesor
        respuesta = self.cliente.post(f'/profesores/{self.profesor.pk}/crear-cuenta/', {'usuario': 'ana_profe'})
        self.assertEqual(respuesta.status_code, 200)
        codigo = respuesta.context['codigo']
        activacion = ActivacionProfesor.objects.get(profesor=self.profesor)
        self.assertNotIn(codigo, activacion.codigo_hash)
        self.profesor.refresh_from_db()
        self.assertFalse(self.profesor.usuario.has_usable_password())
        anonimo = Client()
        datos = {'rut': '12345678-5', 'pregunta': 'MASCOTA', 'respuesta': 'Copito', 'codigo': codigo, 'clave': 'MontanaSegura789!', 'confirmacion': 'MontanaSegura789!'}
        self.assertEqual(anonimo.post('/activar-cuenta/', datos).status_code, 302)
        self.profesor.usuario.refresh_from_db()
        self.assertTrue(self.profesor.usuario.check_password(datos['clave']))
        self.assertEqual(anonimo.post('/activar-cuenta/', datos).status_code, 200)
        self.assertTrue(anonimo.login(username='ana_profe', password=datos['clave']))
        self.assertRedirects(anonimo.get('/dashboard/'), '/profesor/clases/')

    def test_limite_de_intentos_y_permiso_administrador(self):
        from gestion.models import ActivacionProfesor
        respuesta = self.cliente.post(f'/profesores/{self.profesor.pk}/crear-cuenta/', {'usuario': 'ana_profe'})
        codigo = respuesta.context['codigo']
        anonimo = Client()
        datos = {'rut': '12345678-5', 'pregunta': 'MASCOTA', 'respuesta': 'Copito', 'codigo': 'codigo-incorrecto', 'clave': 'MontanaSegura789!', 'confirmacion': 'MontanaSegura789!'}
        for _ in range(3):
            anonimo.post('/activar-cuenta/', datos)
        activacion = ActivacionProfesor.objects.get(profesor=self.profesor)
        self.assertIsNotNone(activacion.bloqueado_hasta)
        datos['codigo'] = codigo
        respuesta = anonimo.post('/activar-cuenta/', datos)
        self.assertContains(respuesta, 'Demasiados intentos')
        self.profesor.refresh_from_db()
        self.profesor.usuario.refresh_from_db()
        self.assertFalse(self.profesor.usuario.has_usable_password())
        recepcion = User.objects.create_user('recepcion_crear_profe', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        self.cliente.force_login(recepcion)
        self.assertNotEqual(self.cliente.post(f'/profesores/{self.profesor.pk}/crear-cuenta/', {'usuario': 'otra'}).status_code, 200)
