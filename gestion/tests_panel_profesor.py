from datetime import time, timedelta
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from gestion.models import Clase, NotificacionProfesor, Profesor, Reserva, Socio, HistorialAsistencia, SolicitudCorreccionAsistencia, AvisoIndisponibilidad

class ProfesorPanelActualizadoTest(TestCase):
    def setUp(self):
        grupo = Group.objects.create(name='Profesor')
        self.user = User.objects.create_user('profe_nuevo', password='pass')
        self.user.groups.add(grupo)
        self.otro = User.objects.create_user('otro_prof_nuevo', password='pass')
        self.otro.groups.add(grupo)
        self.profe = Profesor.objects.create(usuario=self.user, nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        self.hoy = timezone.localdate()
        self.futura = Clase.objects.create(nombre='Yoga futuro', profesor=self.profe, fecha=self.hoy+timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=8)
        self.pasada = Clase.objects.create(nombre='Pilates anterior', profesor=self.profe, fecha=self.hoy-timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=8)
        self.client.force_login(self.user)

    def test_filtros_de_clases_y_cierre(self):
        lista = self.client.get(reverse('profesor_clases'))
        self.assertContains(lista, 'Yoga futuro')
        self.assertNotContains(lista, 'Pilates anterior')
        self.assertContains(self.client.get(reverse('profesor_clases'), {'vista':'anteriores'}), 'Pilates anterior')
        self.assertNotContains(self.client.get(reverse('profesor_clases'), {'q':'Pilates'}), 'Yoga futuro')
        detalle = self.client.get(reverse('profesor_detalle_clase', args=[self.futura.pk]))
        self.assertContains(detalle, 'La asistencia se podrá cerrar al terminar')
        self.assertNotContains(detalle, 'Revisar y cerrar asistencia</a>')

    def test_borrado_personal_del_historial(self):
        aviso = NotificacionProfesor.objects.create(usuario=self.user, mensaje='Aviso propio')
        ajeno = NotificacionProfesor.objects.create(usuario=self.otro, mensaje='Aviso ajeno')
        url = reverse('profesor_limpiar_notificaciones')
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertContains(self.client.get(reverse('profesor_notificaciones')), 'Aviso propio')
        self.assertEqual(self.client.post(url).status_code, 302)
        aviso.refresh_from_db(); ajeno.refresh_from_db()
        self.assertTrue(aviso.oculta)
        self.assertFalse(ajeno.oculta)
        self.assertNotContains(self.client.get(reverse('profesor_notificaciones')), 'Aviso propio')
        self.assertEqual(self.client.get(reverse('profesor_avisos_resumen')).json()['avisos'], [])

    def test_perfil_y_formularios(self):
        for nombre in ['profesor_perfil', 'profesor_cambiar_clave', 'profesor_solicitudes', 'profesor_notificaciones']:
            with self.subTest(pagina=nombre):
                self.assertEqual(self.client.get(reverse(nombre)).status_code, 200)
        self.assertEqual(self.client.get(reverse('avisar_indisponibilidad', args=[self.futura.pk])).status_code, 200)

    def test_aviso_disponible_hasta_48_horas_antes(self):
        from datetime import datetime
        from unittest.mock import patch
        from gestion.models import AvisoIndisponibilidad
        inicio = timezone.make_aware(datetime.combine(self.futura.fecha, self.futura.hora_inicio), timezone.get_current_timezone())
        limite = inicio - timedelta(hours=48)
        detalle = reverse('profesor_detalle_clase', args=[self.futura.pk])
        aviso = reverse('avisar_indisponibilidad', args=[self.futura.pk])
        with patch('gestion.views.timezone.now', return_value=limite + timedelta(seconds=1)):
            self.assertNotContains(self.client.get(detalle), '>Avisar indisponibilidad</a>')
            self.client.post(aviso, {'motivo': 'No puedo asistir'})
            self.assertFalse(AvisoIndisponibilidad.objects.exists())
        with patch('gestion.views.timezone.now', return_value=limite):
            self.assertContains(self.client.get(detalle), '>Avisar indisponibilidad</a>')
            self.client.post(aviso, {'motivo': 'No puedo asistir'})
            self.assertTrue(AvisoIndisponibilidad.objects.exists())

    def test_solicitudes_filtra_tipos_y_protege_avisos_ajenos(self):
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='filtrar@example.org', telefono='123456789')
        reserva = Reserva.objects.create(socio=socio, clase=self.pasada, estado='CONFIRMADA')
        SolicitudCorreccionAsistencia.objects.create(reserva=reserva, solicitante=self.user, estado_solicitado='ASISTIO', motivo='Motivo corrección propio')
        AvisoIndisponibilidad.objects.create(clase=self.futura, profesor=self.profe, motivo='Aviso personal')
        otro_profesor = Profesor.objects.create(usuario=self.otro, nombres='Otra', apellidos='Persona', especialidad='Yoga')
        AvisoIndisponibilidad.objects.create(clase=self.futura, profesor=otro_profesor, motivo='Aviso privado ajeno')
        url = reverse('profesor_solicitudes')
        pagina = self.client.get(url)
        self.assertContains(pagina, 'Motivo corrección propio')
        self.assertContains(pagina, 'Aviso personal')
        self.assertNotContains(pagina, 'Aviso privado ajeno')
        pagina = self.client.get(url, {'tipo': 'correcciones'})
        self.assertContains(pagina, 'Motivo corrección propio')
        self.assertNotContains(pagina, 'Aviso personal')
        pagina = self.client.get(url, {'tipo': 'indisponibilidad'})
        self.assertContains(pagina, 'Aviso personal')
        self.assertNotContains(pagina, 'Motivo corrección propio')
        self.assertContains(pagina, 'Pendiente de revisión')

    def test_lista_guardar_revisar_cerrar_y_solicitar_correccion(self):
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='check@example.org', telefono='123456789')
        reserva = Reserva.objects.create(socio=socio, clase=self.pasada, estado='CONFIRMADA')
        detalle = reverse('profesor_detalle_clase', args=[self.pasada.pk])
        guardar = reverse('profesor_guardar_asistencia', args=[self.pasada.pk])
        respuesta = self.client.get(detalle)
        self.assertContains(respuesta, 'name="asistio"')
        respuesta = self.client.post(guardar, {'snapshot': respuesta.context['snapshot'], 'asistio': [str(reserva.pk)], 'accion': 'revisar'})
        self.assertRedirects(respuesta, reverse('cerrar_asistencia_clase', args=[self.pasada.pk]))
        reserva.refresh_from_db()
        self.assertEqual(reserva.asistencia, 'ASISTIO')
        self.assertTrue(HistorialAsistencia.objects.filter(reserva=reserva, estado_nuevo='ASISTIO').exists())
        self.client.post(reverse('cerrar_asistencia_clase', args=[self.pasada.pk]), {'confirmar': 'si'})
        respuesta = self.client.get(detalle)
        self.assertNotContains(respuesta, 'name="asistio"')
        self.assertContains(respuesta, 'Solicitar corrección')
        self.client.post(reverse('solicitar_correccion_asistencia', args=[self.pasada.pk, reserva.pk]), {'estado_solicitado': 'NO_ASISTIO', 'motivo': 'Se marcó por error'})
        reserva.refresh_from_db()
        self.assertEqual(reserva.asistencia, 'ASISTIO')
        self.assertTrue(SolicitudCorreccionAsistencia.objects.filter(reserva=reserva, estado_solicitado='NO_ASISTIO').exists())
        self.assertContains(self.client.get(detalle), 'Solicitud pendiente')

    def test_lista_no_sobrescribe_cambios_de_otra_persona(self):
        socio = Socio.objects.create(rut='12345678-5', nombres='Camila', apellidos='Prueba', email='conflicto@example.org', telefono='123456789')
        reserva = Reserva.objects.create(socio=socio, clase=self.pasada, estado='CONFIRMADA')
        snapshot = self.client.get(reverse('profesor_detalle_clase', args=[self.pasada.pk])).context['snapshot']
        reserva.asistencia = 'ASISTIO'
        reserva.save()
        self.client.post(reverse('profesor_guardar_asistencia', args=[self.pasada.pk]), {'snapshot': snapshot})
        reserva.refresh_from_db()
        self.assertEqual(reserva.asistencia, 'ASISTIO')
