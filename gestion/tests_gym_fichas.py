from datetime import timedelta, time
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from gestion.models import Actividad, Clase, Profesor, Sala

class GymFichaTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin_fichas', password='pass')
        self.client.force_login(self.admin)
        self.prof = Profesor.objects.create(nombres='Ana', apellidos='Prueba', especialidad='Yoga')
        self.sala = Sala.objects.create(nombre='Sala Prueba', capacidad=12)
        self.actividad = Actividad.objects.create(nombre='Yoga')
        self.clase = Clase.objects.create(nombre='Yoga de mañana', profesor=self.prof, sala_asignada=self.sala, actividad=self.actividad, fecha=timezone.localdate()+timedelta(days=1), hora_inicio=time(10), hora_fin=time(11), capacidad_maxima=10)
        self.recepcion = User.objects.create_user('recep_fichas', password='pass')
        self.recepcion.groups.add(Group.objects.create(name='Recepcion'))

    def test_listados_fichas_y_formularios(self):
        for modulo, obj in [('profesores',self.prof),('salas',self.sala),('actividades',self.actividad)]:
            listado = self.client.get(reverse('gestion_list', args=[modulo]))
            self.assertContains(listado, reverse('gimnasio_ficha', args=[modulo,obj.pk]))
            ficha = self.client.get(reverse('gimnasio_ficha', args=[modulo,obj.pk]))
            self.assertContains(ficha, 'Yoga de mañana')
            self.assertContains(ficha, reverse('admin_clase_detalle', args=[self.clase.pk]))
            self.assertEqual(self.client.get(reverse('gestion_editar', args=[modulo,obj.pk])).status_code, 200)
            self.assertEqual(self.client.get(reverse('gestion_nuevo', args=[modulo])).status_code, 200)
        self.assertContains(self.client.get(reverse('recepcionistas')), reverse('recepcionista_ficha', args=[self.recepcion.pk]))
        self.assertContains(self.client.get(reverse('recepcionista_ficha', args=[self.recepcion.pk])), 'recep_fichas')
        self.assertEqual(self.client.get(reverse('editar_recepcionista', args=[self.recepcion.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('crear_recepcionista')).status_code, 200)

    def test_recepcion_inactivos_y_cuenta_protegida(self):
        self.recepcion.is_active = False
        self.recepcion.save()
        self.assertNotContains(self.client.get(reverse('recepcionistas')), 'recep_fichas')
        self.assertContains(self.client.get(reverse('recepcionistas')+'?estado=inactivos'), 'recep_fichas')
        self.admin.groups.add(Group.objects.get(name='Recepcion'))
        ficha = self.client.get(reverse('recepcionista_ficha', args=[self.admin.pk]))
        self.assertNotContains(ficha, reverse('editar_recepcionista', args=[self.admin.pk]))

    def test_fichas_requieren_administrador(self):
        self.client.force_login(self.recepcion)
        self.assertNotEqual(self.client.get(reverse('gimnasio_ficha', args=['profesores',self.prof.pk])).status_code, 200)
        self.assertNotEqual(self.client.get(reverse('recepcionista_ficha', args=[self.recepcion.pk])).status_code, 200)

    def test_codigo_profesor_con_cuenta_sin_activar(self):
        from gestion.models import ActivacionProfesor, PerfilPersonal
        usuario = User.objects.create_user('prof_sin_clave', password=None)
        self.prof.usuario = usuario
        self.prof.rut = '12345678-5'
        self.prof.save()
        url = reverse('regenerar_codigo_profesor', args=[self.prof.pk])
        self.assertContains(self.client.get(reverse('gimnasio_ficha', args=['profesores',self.prof.pk])), 'Activar cuenta')
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertTrue(ActivacionProfesor.objects.filter(profesor=self.prof).exists())
        self.assertTrue(PerfilPersonal.objects.filter(usuario=usuario, rut='12345678-5').exists())

    def test_codigo_recepcion_sin_activacion(self):
        from gestion.models import ActivacionRecepcion, PerfilPersonal
        self.recepcion.set_unusable_password()
        self.recepcion.save()
        PerfilPersonal.objects.create(usuario=self.recepcion, rut='12345678-5')
        url = reverse('regenerar_codigo_recepcion', args=[self.recepcion.pk])
        self.assertContains(self.client.get(reverse('recepcionista_ficha', args=[self.recepcion.pk])), 'Activar cuenta')
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertTrue(ActivacionRecepcion.objects.filter(usuario=self.recepcion).exists())
