from datetime import timedelta
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone
from gestion.models import Socio, Plan, Membresia, SolicitudCongelamiento

class MemberMembershipFilterTest(TestCase):
    def setUp(self):
        self.admin=User.objects.create_superuser('admin_mfilter',password='pass')
        self.reception=User.objects.create_user('reception_mfilter',password='pass')
        self.reception.groups.add(Group.objects.create(name='Recepcion'))
        self.plan=Plan.objects.create(nombre='Plan filtro',precio=25000,duracion_dias=30)
        self.today=timezone.localdate()
    def socio(self,nombre,rut):
        return Socio.objects.create(rut=rut,nombres=nombre,apellidos='Prueba',email=nombre.lower()+'@example.com',telefono='912345678')
    def membership(self,socio,start,end):
        return Membresia.objects.create(socio=socio,plan=self.plan,fecha_inicio=self.today+timedelta(days=start),fecha_fin=self.today+timedelta(days=end))
    def test_filter_selection_priority_and_detail(self):
        active=self.socio('Activa','11111111-1');frozen=self.socio('Congelada','22222222-2');future=self.socio('Programada','33333333-3');expired=self.socio('Vencida','44444444-4');none=self.socio('Sinplan','55555555-5')
        current=self.membership(active,-10,10);self.membership(active,11,40)
        paused=self.membership(frozen,-10,10)
        SolicitudCongelamiento.objects.create(membresia=paused,solicitante=self.admin,fecha_inicio=self.today,fecha_fin=self.today+timedelta(days=2),estado='APROBADA')
        self.membership(future,2,30);self.membership(expired,-60,-30)
        self.client.force_login(self.admin)
        for status,expected in [('ACTIVA','Activa'),('CONGELADA','Congelada'),('PROGRAMADA','Programada'),('VENCIDA','Vencida'),('SIN_MEMBRESIA','Sinplan')]:
            with self.subTest(status=status):
                result=self.client.get('/socios/',{'membresia':status})
                self.assertEqual(result.status_code,200)
                self.assertEqual(list(result.context['socios'].values_list('nombres',flat=True)),[expected])
        ficha=self.client.get(f'/socios/{active.pk}/');self.assertContains(ficha,'Ver detalle')
        detail=self.client.get(f'/socios/{active.pk}/membresias/{current.pk}/');self.assertContains(detail,'Detalle de membresía')
        self.assertEqual(self.client.get(f'/socios/{none.pk}/membresias/{current.pk}/').status_code,404)
        self.client.force_login(self.reception)
        self.assertNotEqual(self.client.get(f'/socios/{active.pk}/membresias/{current.pk}/').status_code,200)
        self.assertContains(self.client.get('/gestion/membresias/'),'Nueva membresía')
