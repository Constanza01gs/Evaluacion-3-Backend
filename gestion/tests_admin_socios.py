from django.contrib.auth.models import Group, User
from django.test import TestCase
from gestion.models import Socio

class AdminMembersTest(TestCase):
    def test_admin_consults_and_reception_keeps_operational_actions(self):
        admin=User.objects.create_superuser('admin_members',password='pass')
        reception=User.objects.create_user('reception_members',password='pass')
        reception.groups.add(Group.objects.create(name='Recepcion'))
        socio=Socio.objects.create(rut='12345678-5',nombres='Camila',apellidos='Torres',email='camila@example.com',telefono='912345678')
        self.client.force_login(admin)
        result=self.client.get('/socios/',{'q':'Camila Torres'})
        self.assertContains(result,'Camila Torres');self.assertContains(result,'Ver ficha')
        self.assertNotContains(result,'+ Nuevo socio');self.assertNotContains(result,'Renovar código')
        self.assertContains(self.client.get('/socios/',{'q':'123456785'}),'Camila Torres')
        ficha=self.client.get(f'/socios/{socio.pk}/')
        self.assertContains(ficha,'Editar datos');self.assertContains(ficha,'Desactivar socio')
        self.assertNotContains(ficha,'Contratar membresía')
        self.client.force_login(reception)
        self.assertContains(self.client.get('/socios/'),'+ Nuevo socio')
        self.assertContains(self.client.get(f'/socios/{socio.pk}/'),'Contratar membresía')
