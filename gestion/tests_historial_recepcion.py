from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from gestion.models import NotificacionRecepcion

class HistorialRecepcionTest(TestCase):
    def setUp(self):
        grupo = Group.objects.create(name='Recepcion')
        self.primero = User.objects.create_user('recepcion_primero', password='clave')
        self.segundo = User.objects.create_user('recepcion_segundo', password='clave')
        self.primero.groups.add(grupo)
        self.segundo.groups.add(grupo)
        self.aviso = NotificacionRecepcion.objects.create(mensaje='Aviso compartido')
        self.client.force_login(self.primero)

    def test_borrado_personal_y_nuevos_avisos(self):
        self.assertContains(self.client.get(reverse('recepcion_notificaciones')), 'Aviso compartido')
        self.assertEqual(self.client.get(reverse('recepcion_limpiar_notificaciones')).status_code, 405)
        self.assertEqual(self.client.post(reverse('recepcion_limpiar_notificaciones')).status_code, 302)
        self.assertNotContains(self.client.get(reverse('recepcion_notificaciones')), 'Aviso compartido')
        self.assertEqual(self.client.get(reverse('recepcion_avisos_resumen')).json()['avisos'], [])
        NotificacionRecepcion.objects.create(mensaje='Nuevo aviso')
        self.assertContains(self.client.get(reverse('recepcion_notificaciones')), 'Nuevo aviso')
        self.client.force_login(self.segundo)
        self.assertContains(self.client.get(reverse('recepcion_notificaciones')), 'Aviso compartido')
        self.assertTrue(NotificacionRecepcion.objects.filter(pk=self.aviso.pk).exists())
