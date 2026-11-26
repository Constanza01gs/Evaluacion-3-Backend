from datetime import timedelta, time
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from gestion.models import Actividad, Profesor, Sala, Clase, Reserva, Socio, ProgramacionClase
from gestion.horarios_fijos import asegurar_horarios

class HorarioFijoTest(TestCase):
    def setUp(self):
        self.admin=User.objects.create_superuser('admin_fijo',password='x')
        self.client.force_login(self.admin)
        self.actividad=Actividad.objects.create(nombre='Yoga')
        self.profesor=Profesor.objects.create(nombres='Ana',apellidos='Pérez',especialidad='Yoga')
        self.sala=Sala.objects.create(nombre='Sala Uno',capacidad=20)
        self.inicio=timezone.localdate()+timedelta(days=1)
        self.dia=self.inicio.weekday()
        self.datos={'nombre':'Yoga fijo','actividad':self.actividad.pk,'profesor':self.profesor.pk,'sala':self.sala.pk,'capacidad':'10','desde':self.inicio.isoformat(),f'dia_{self.dia}':'on',f'inicio_{self.dia}':'10:00',f'fin_{self.dia}':'11:00'}
    def crear(self):
        revision=self.client.post('/clases/horario-fijo/nuevo/',self.datos)
        self.assertEqual(revision.status_code,200)
        self.assertContains(revision,'Confirmar horario fijo')
        resp=self.client.post('/clases/horario-fijo/nuevo/',{'confirmacion':revision.context['confirmacion']})
        self.assertEqual(resp.status_code,302)
        return ProgramacionClase.objects.get()
    def test_generate_cancelled_not_recreated_and_edit_form(self):
        serie=self.crear(); sesiones=Clase.objects.filter(programacion=serie)
        self.assertGreater(sesiones.count(),10)
        clase=sesiones.first(); clase.soft_delete(); asegurar_horarios()
        self.assertEqual(Clase.all_objects.filter(programacion=serie,fecha=clase.fecha).count(),1)
        self.assertFalse(Clase.all_objects.get(pk=clase.pk).activo)
        self.assertContains(self.client.get(f'/gestion/clases/{sesiones.last().pk}/editar/'),'Editar horario fijo')
    def test_conflict_prevents_creation(self):
        Clase.objects.create(nombre='Ocupada',actividad=self.actividad,profesor=self.profesor,sala_asignada=self.sala,fecha=self.inicio,hora_inicio=time(10),hora_fin=time(11),capacidad_maxima=10)
        revision=self.client.post('/clases/horario-fijo/nuevo/',self.datos)
        self.assertContains(revision,'Hay horarios ocupados')
        self.assertNotContains(revision,'Confirmar horario fijo')
        self.assertFalse(ProgramacionClase.objects.exists())
    def test_booking_blocks_future_schedule_change(self):
        serie=self.crear(); clase=Clase.objects.filter(programacion=serie).first()
        socio=Socio.objects.create(nombres='Cata',apellidos='Prueba',rut='12345678-5',email='cata@example.com',telefono='900000001')
        Reserva.objects.create(clase=clase,socio=socio,estado='CONFIRMADA')
        data={**self.datos,f'inicio_{self.dia}':'12:00',f'fin_{self.dia}':'13:00'}
        revision=self.client.post(f'/clases/{clase.pk}/horario-fijo/',data)
        self.assertContains(revision,'Hay reservas que requieren atención')
        self.assertNotContains(revision,'Confirmar horario fijo')
        self.assertTrue(Clase.objects.filter(pk=clase.pk).exists())
    def test_edit_future_series_and_stop_keep_history(self):
        serie=self.crear(); original=Clase.objects.filter(programacion=serie).first()
        data={**self.datos,f'inicio_{self.dia}':'12:00',f'fin_{self.dia}':'13:00'}
        revision=self.client.post(f'/clases/{original.pk}/horario-fijo/',data)
        self.assertContains(revision,'Confirmar horario fijo')
        resultado=self.client.post(f'/clases/{original.pk}/horario-fijo/',{'confirmacion':revision.context['confirmacion']})
        self.assertEqual(resultado.status_code,302)
        serie.refresh_from_db();self.assertFalse(serie.activa)
        nueva=ProgramacionClase.objects.get(activa=True)
        nueva_clase=Clase.objects.filter(programacion=nueva).first()
        self.assertEqual(nueva_clase.hora_inicio,time(12))
        self.assertTrue(Clase.all_objects.filter(pk=original.pk,activo=False).exists())
        revision=self.client.post(f'/clases/{nueva_clase.pk}/horario-fijo/detener/')
        self.assertContains(revision,'Confirmar detención')
        self.client.post(f'/clases/{nueva_clase.pk}/horario-fijo/detener/',{'confirmar':'si'})
        nueva.refresh_from_db();self.assertFalse(nueva.activa)
        asegurar_horarios()
        self.assertFalse(Clase.objects.filter(programacion=nueva).exists())
    def test_single_class_can_configure_schedule(self):
        clase=Clase.objects.create(nombre='Yoga única',actividad=self.actividad,profesor=self.profesor,sala_asignada=self.sala,fecha=self.inicio,hora_inicio=time(8),hora_fin=time(9),capacidad_maxima=10)
        self.assertContains(self.client.get(f'/gestion/clases/{clase.pk}/editar/'),'Configurar horario fijo')
