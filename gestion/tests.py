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


class CargaDemoTestCase(TestCase):
    def test_carga_no_cambia_cuentas_y_no_repite_datos(self):
        user = User.objects.create_superuser('admin', 'admin@example.org', 'clave-privada')
        call_command('cargar_datos_demo', stdout=StringIO())
        user.refresh_from_db()
        self.assertTrue(user.check_password('clave-privada'))
        self.assertEqual(Socio.all_objects.count(), 13)
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
        clase = Clase.objects.create(nombre='Clase carrera', profesor=profesor, fecha=hoy + timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=2, sala='Sala prueba')
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
                return cliente.post(f'/gestion/clases/{clase.pk}/editar/', {'nombre': clase.nombre, 'profesor': profesor.pk, 'fecha': clase.fecha.isoformat(), 'hora_inicio': '10:00', 'hora_fin': '11:00', 'capacidad_maxima': 1, 'sala': clase.sala}).status_code
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
            self.assertEqual(Client().get('/login').status_code, 301)
            admin = User.objects.create_superuser('admin_404', 'admin404@example.org', 'clave-prueba')
            client = Client()
            client.force_login(admin)
            response = client.get('/socios/999999/editar/')
            self.assertContains(response, 'ruta fuera del circuito', status_code=404)

    def test_filtros_membresia_y_tarjetas_recepcion(self):
        usuario = User.objects.create_user('recepcion_filtros', password='clave-prueba')
        usuario.groups.add(Group.objects.create(name='Recepcion'))
        cliente = Client()
        cliente.force_login(usuario)
        respuesta = cliente.get('/gestion/membresias/', {'q': 'Valentina', 'estado_m': 'VENCIDA', 'plan': self.plan_plus.id})
        self.assertContains(respuesta, 'Valentina')
        self.assertNotContains(respuesta, 'Camila Rojas')
        self.assertContains(cliente.get('/gestion/planes/'), 'plan-card')
        self.assertNotContains(cliente.get('/gestion/pagos/'), '<th>Acciones</th>')
    def test_membresia_marcada_vencida_no_se_reactiva_sola(self):
        self.membresia_activa.estado = 'VENCIDA'
        self.membresia_activa.save()
        self.membresia_activa.actualizar_estado()
        self.membresia_activa.refresh_from_db()
        self.assertEqual(self.membresia_activa.estado, 'VENCIDA')
        self.assertFalse(validar_y_registrar_acceso(self.socio_activo.codigo_qr)['autorizado'])

    def test_plan_desactivado_no_autoriza_ingreso(self):
        self.plan_plus.soft_delete()
        self.assertFalse(validar_y_registrar_acceso(self.socio_activo.codigo_qr)['autorizado'])
    def test_anulacion_pago_solo_admin_y_excluye_ingresos(self):
        admin = User.objects.create_superuser('admin_pago', 'admin@example.org', 'clave-prueba')
        recepcion = User.objects.create_user('recepcion_pago', password='clave-prueba')
        recepcion.groups.add(Group.objects.create(name='Recepcion'))
        pago = Pago.objects.create(membresia=self.membresia_activa, monto=40000, metodo_pago='EFECTIVO')
        client = Client()
        client.force_login(recepcion)
        client.post(f'/gestion/pagos/{pago.id}/anular/', {'motivo': 'Error en monto'})
        pago.refresh_from_db()
        self.assertFalse(pago.anulado)
        client.force_login(admin)
        response = client.post(f'/gestion/pagos/{pago.id}/anular/', {'motivo': 'Error en monto'})
        self.assertEqual(response.status_code, 302)
        pago.refresh_from_db()
        self.assertTrue(pago.anulado)
        self.assertEqual(pago.anulado_por, admin)
        self.assertEqual(pago.motivo_anulacion, 'Error en monto')
        self.assertContains(client.get('/gestion/pagos/'), 'Anulado')
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
