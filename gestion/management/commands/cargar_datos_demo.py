"""
Comando de gestión de Django para la carga reproducible de datos realistas de demostración.
Diseñado para la evaluación de FitLife (Evaluación 3 - Backend).

Uso:
    python manage.py cargar_datos_demo
"""

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.utils import timezone
from datetime import timedelta, time
from decimal import Decimal

from gestion.models import (
    Socio, Plan, Membresia, Pago, Profesor,
    Clase, Reserva, RegistroAcceso
)


class Command(BaseCommand):
    help = "Carga datos realistas ficticios adecuados a Chile para recorrer y defender FitLife."

    @transaction.atomic
    def handle(self, *args, **options):
        modelos = (Socio, Plan, Membresia, Pago, Profesor, Clase, Reserva, RegistroAcceso)
        if any((m.all_objects if hasattr(m, 'all_objects') else m.objects).exists() for m in modelos):
            raise CommandError('La carga de demostración solo se ejecuta en una base sin datos de FitLife. No se modificó ningún dato.')
        self.stdout.write(self.style.NOTICE("Iniciando preparación y carga de datos para FitLife..."))

        # ----------------------------------------------------------------------
        # 1. Configuración de Roles y Usuarios de Demostración
        # ----------------------------------------------------------------------
        self.stdout.write("1. Configurando Grupos y Roles de Seguridad...")
        grupo_admin, _ = Group.objects.get_or_create(name='Administrador')
        grupo_recepcion, _ = Group.objects.get_or_create(name='Recepcion')

        # Asignar permisos básicos al personal de Recepción
        permisos_recepcion = Permission.objects.filter(
            codename__in=[
                'view_socio', 'view_plan', 'view_membresia',
                'view_clase', 'add_reserva', 'change_reserva', 'view_reserva',
                'add_registroacceso', 'view_registroacceso', 'add_pago', 'view_pago'
            ]
        )
        grupo_recepcion.permissions.set(permisos_recepcion)

        self.stdout.write(' -> Grupos preparados. Las cuentas existentes y sus contraseñas no se modificaron.')

        # ----------------------------------------------------------------------
        # 2. Creación de Planes
        # ----------------------------------------------------------------------
        self.stdout.write("2. Creando Planes de Gimnasio...")
        planes_datos = [
            {
                'nombre': 'Plan Básico',
                'descripcion': 'Acceso ilimitado a sala de pesas y máquinas cardiovasculares.',
                'duracion_dias': 30,
                'precio': Decimal('25000'),
                'permite_clases': False,
            },
            {
                'nombre': 'Plan Plus',
                'descripcion': 'Acceso completo a salas y todas las clases grupales.',
                'duracion_dias': 30,
                'precio': Decimal('40000'),
                'permite_clases': True,
            },
            {
                'nombre': 'Plan Premium Trimestral',
                'descripcion': 'Plan trimestral con acceso total y evaluación nutricional.',
                'duracion_dias': 90,
                'precio': Decimal('105000'),
                'permite_clases': True,
            },
            {
                'nombre': 'Plan Élite Anual',
                'descripcion': 'Membresía anual completa con estacionamiento y casillero exclusivo.',
                'duracion_dias': 365,
                'precio': Decimal('360000'),
                'permite_clases': True,
            },
        ]

        planes_dict = {}
        for p in planes_datos:
            plan_obj, _ = Plan.objects.get_or_create(
                nombre=p['nombre'],
                defaults=p
            )
            planes_dict[p['nombre']] = plan_obj

        # ----------------------------------------------------------------------
        # 3. Creación de Profesores
        # ----------------------------------------------------------------------
        self.stdout.write("3. Registrando Profesores...")
        profesores_datos = [
            {'nombres': 'Carlos', 'apellidos': 'Morales Vera', 'especialidad': 'Spinning y Ciclismo Indoor', 'email': 'carlos.morales@fitlife.cl', 'telefono': '+56981112233'},
            {'nombres': 'Laura', 'apellidos': 'Gutiérrez Palma', 'especialidad': 'Entrenamiento Funcional y HIIT', 'email': 'laura.gutierrez@fitlife.cl', 'telefono': '+56982223344'},
            {'nombres': 'Ana', 'apellidos': 'Ramos Soto', 'especialidad': 'Pilates y Postura', 'email': 'ana.ramos@fitlife.cl', 'telefono': '+56983334455'},
            {'nombres': 'Marta', 'apellidos': 'Silva Castro', 'especialidad': 'Yoga y Flexibilidad', 'email': 'marta.silva@fitlife.cl', 'telefono': '+56984445566'},
            {'nombres': 'Javier', 'apellidos': 'López Henríquez', 'especialidad': 'Zumba y Ritmos', 'email': 'javier.lopez@fitlife.cl', 'telefono': '+56985556677'},
        ]

        profesores_dict = {}
        for prof in profesores_datos:
            p_obj, _ = Profesor.objects.get_or_create(
                email=prof['email'],
                defaults=prof
            )
            profesores_dict[prof['nombres']] = p_obj

        # ----------------------------------------------------------------------
        # 4. Creación de Socios y Membresías Realistas
        # ----------------------------------------------------------------------
        self.stdout.write("4. Creando Socios y Membresías (Activas, Vencidas y Congeladas)...")
        hoy = timezone.localdate()

        socios_datos = [
            # Socios con Membresía ACTIVA (Casos para estar adentro o entrar)
            {'rut': '18.452.123-4', 'nombres': 'Camila', 'apellidos': 'Rojas Herrera', 'email': 'camila.rojas@ejemplo.cl', 'telefono': '+56991234567', 'qr': 'FL-CAMI1042', 'plan': 'Plan Plus', 'estado_m': 'ACTIVA', 'dias_fin': 25},
            {'rut': '19.876.543-2', 'nombres': 'Matías', 'apellidos': 'Fuentes Alarcón', 'email': 'matias.fuentes@ejemplo.cl', 'telefono': '+56992345678', 'qr': 'FL-MATI2051', 'plan': 'Plan Premium Trimestral', 'estado_m': 'ACTIVA', 'dias_fin': 60},
            {'rut': '17.345.987-1', 'nombres': 'Sofía', 'apellidos': 'Castro Valenzuela', 'email': 'sofia.castro@ejemplo.cl', 'telefono': '+56993456789', 'qr': 'FL-SOFI3062', 'plan': 'Plan Plus', 'estado_m': 'ACTIVA', 'dias_fin': 15},
            {'rut': '20.123.456-7', 'nombres': 'Diego', 'apellidos': 'Ruiz Carrasco', 'email': 'diego.ruiz@ejemplo.cl', 'telefono': '+56994567890', 'qr': 'FL-DIEG4073', 'plan': 'Plan Básico', 'estado_m': 'ACTIVA', 'dias_fin': 20},
            {'rut': '19.456.789-0', 'nombres': 'Javiera', 'apellidos': 'Contreras Bravo', 'email': 'javiera.contreras@ejemplo.cl', 'telefono': '+56995678901', 'qr': 'FL-JAVI5084', 'plan': 'Plan Élite Anual', 'estado_m': 'ACTIVA', 'dias_fin': 280},
            {'rut': '17.890.123-8', 'nombres': 'Felipe', 'apellidos': 'Araya Guzmán', 'email': 'felipe.araya@ejemplo.cl', 'telefono': '+56996789012', 'qr': 'FL-FELI6095', 'plan': 'Plan Plus', 'estado_m': 'ACTIVA', 'dias_fin': 12},
            {'rut': '18.123.789-6', 'nombres': 'Constanza', 'apellidos': 'Muñoz Lagos', 'email': 'constanza.munoz@ejemplo.cl', 'telefono': '+56997890123', 'qr': 'FL-CONS7016', 'plan': 'Plan Plus', 'estado_m': 'ACTIVA', 'dias_fin': 18},
            {'rut': '19.234.890-5', 'nombres': 'Ignacio', 'apellidos': 'Herrera Parra', 'email': 'ignacio.herrera@ejemplo.cl', 'telefono': '+56998901234', 'qr': 'FL-IGNA8027', 'plan': 'Plan Plus', 'estado_m': 'ACTIVA', 'dias_fin': 22},
            {'rut': '20.345.678-9', 'nombres': 'Carla', 'apellidos': 'Méndez Vidal', 'email': 'carla.mendez@ejemplo.cl', 'telefono': '+56999012345', 'qr': 'FL-CARL9038', 'plan': 'Plan Básico', 'estado_m': 'ACTIVA', 'dias_fin': 30},
            {'rut': '16.987.654-1', 'nombres': 'Luis', 'apellidos': 'Ramírez Ortiz', 'email': 'luis.ramirez@ejemplo.cl', 'telefono': '+56990123456', 'qr': 'FL-LUIS1049', 'plan': 'Plan Plus', 'estado_m': 'ACTIVA', 'dias_fin': 8},

            # Caso 1 de prueba: Membresía VENCIDA (Intenta ingresar y se le debe denegar)
            {'rut': '16.789.012-3', 'nombres': 'Valentina', 'apellidos': 'Morales Sepúlveda', 'email': 'valentina.morales@ejemplo.cl', 'telefono': '+56991113355', 'qr': 'FL-VALE9991', 'plan': 'Plan Plus', 'estado_m': 'VENCIDA', 'dias_fin': -5},

            # Caso 2 de prueba: Membresía CONGELADA (Intenta ingresar y se le debe denegar por pausa)
            {'rut': '18.901.234-5', 'nombres': 'Benjamín', 'apellidos': 'Soto Cárdenas', 'email': 'benjamin.soto@ejemplo.cl', 'telefono': '+56992224466', 'qr': 'FL-BENJ9992', 'plan': 'Plan Premium Trimestral', 'estado_m': 'CONGELADA', 'dias_fin': 45, 'motivo': 'Pausa médica por lesión en menisco rodilla derecha'},

            # Caso 3 de prueba: Socio con BAJA LÓGICA (Desactivado en sistema)
            {'rut': '17.654.321-0', 'nombres': 'Nicolás', 'apellidos': 'Tapia Poblete', 'email': 'nicolas.tapia@ejemplo.cl', 'telefono': '+56993335577', 'qr': 'FL-NICO9993', 'plan': 'Plan Plus', 'estado_m': 'ACTIVA', 'dias_fin': 10, 'activo': False},
        ]

        socios_dict = {}
        for s_data in socios_datos:
            socio, _ = Socio.all_objects.get_or_create(
                rut=s_data['rut'],
                defaults={
                    'nombres': s_data['nombres'],
                    'apellidos': s_data['apellidos'],
                    'email': s_data['email'],
                    'telefono': s_data['telefono'],
                    'codigo_qr': s_data['qr'],
                    'activo': s_data.get('activo', True),
                }
            )
            socios_dict[s_data['nombres']] = socio

            # Crear su membresía
            plan = planes_dict[s_data['plan']]
            fecha_fin = hoy + timedelta(days=s_data['dias_fin'])
            fecha_inicio = fecha_fin - timedelta(days=plan.duracion_dias)

            membresia, created_m = Membresia.all_objects.get_or_create(
                socio=socio,
                plan=plan,
                defaults={
                    'fecha_inicio': fecha_inicio,
                    'fecha_fin': fecha_fin,
                    'estado': s_data['estado_m'],
                    'motivo_congelacion': s_data.get('motivo'),
                    'activo': s_data.get('activo', True),
                }
            )

            # Si es nueva, registrar el pago correspondiente
            if created_m:
                Pago.objects.create(
                    membresia=membresia,
                    monto=plan.precio,
                    fecha_pago=timezone.now() - timedelta(days=abs(s_data['dias_fin']) + 2),
                    metodo_pago='TARJETA_DEBITO',
                    numero_comprobante=f"BOL-{socio.id:04d}-{timezone.now().year}"
                )

        # ----------------------------------------------------------------------
        # 5. Creación de Clases Grupales y Escenarios de Ocupación
        # ----------------------------------------------------------------------
        self.stdout.write("5. Programando Clases Grupales y Lista de Espera FIFO...")
        clases_datos = [
            {
                'nombre': 'Spinning Matutino Alta Intensidad',
                'profesor': profesores_dict['Carlos'],
                'fecha': hoy,
                'hora_inicio': time(8, 0),
                'hora_fin': time(8, 50),
                'capacidad_maxima': 6,  # Capacidad pequeña para demostrar sobrecupo y lista de espera
                'sala': 'Sala Ciclo 1',
            },
            {
                'nombre': 'Yoga Restaurativo y Respiración',
                'profesor': profesores_dict['Marta'],
                'fecha': hoy,
                'hora_inicio': time(10, 0),
                'hora_fin': time(11, 0),
                'capacidad_maxima': 15,
                'sala': 'Sala Armonía',
            },
            {
                'nombre': 'Entrenamiento Funcional HIIT',
                'profesor': profesores_dict['Laura'],
                'fecha': hoy,
                'hora_inicio': time(18, 30),
                'hora_fin': time(19, 20),
                'capacidad_maxima': 12,
                'sala': 'Sala Funcional',
            },
            {
                'nombre': 'Pilates Core y Postura',
                'profesor': profesores_dict['Ana'],
                'fecha': hoy,
                'hora_inicio': time(19, 30),
                'hora_fin': time(20, 20),
                'capacidad_maxima': 10,
                'sala': 'Sala Armonía',
            },
        ]

        clases_dict = {}
        for c_data in clases_datos:
            clase, _ = Clase.objects.get_or_create(
                nombre=c_data['nombre'],
                fecha=c_data['fecha'],
                hora_inicio=c_data['hora_inicio'],
                defaults=c_data
            )
            clases_dict[c_data['nombre']] = clase

        # Llenar la clase de Spinning al 100% (6 cupos confirmados) y agregar 2 en LISTA DE ESPERA
        clase_spinning = clases_dict['Spinning Matutino Alta Intensidad']
        socios_confirmados_spinning = [
            socios_dict['Camila'],
            socios_dict['Matías'],
            socios_dict['Sofía'],
            socios_dict['Javiera'],
            socios_dict['Felipe'],
            socios_dict['Luis'],
        ]

        for s in socios_confirmados_spinning:
            Reserva.objects.get_or_create(
                clase=clase_spinning,
                socio=s,
                defaults={'estado': 'CONFIRMADA'}
            )

        # Socios en LISTA DE ESPERA (Constanza Muñoz #1, Ignacio Herrera #2)
        Reserva.objects.get_or_create(
            clase=clase_spinning,
            socio=socios_dict['Constanza'],
            defaults={'estado': 'LISTA_ESPERA'}
        )
        Reserva.objects.get_or_create(
            clase=clase_spinning,
            socio=socios_dict['Ignacio'],
            defaults={'estado': 'LISTA_ESPERA'}
        )

        # Reservas parciales para Yoga
        clase_yoga = clases_dict['Yoga Restaurativo y Respiración']
        for s in [socios_dict['Camila'], socios_dict['Sofía'], socios_dict['Constanza']]:
            Reserva.objects.get_or_create(
                clase=clase_yoga,
                socio=s,
                defaults={'estado': 'CONFIRMADA'}
            )

        # ----------------------------------------------------------------------
        # 6. Registros de Acceso para Aforo Coherente
        # ----------------------------------------------------------------------
        self.stdout.write("6. Generando Entradas y Salidas para Aforo en Tiempo Real...")
        ahora = timezone.now()

        # 4 Socios actualmente adentro (tienen ENTRADA permitida sin salida posterior)
        adentro = [
            socios_dict['Camila'],
            socios_dict['Matías'],
            socios_dict['Diego'],
            socios_dict['Javiera']
        ]

        for i, s in enumerate(adentro):
            RegistroAcceso.objects.create(
                socio=s,
                tipo='ENTRADA',
                resultado='PERMITIDO',
                motivo='Acceso permitido. Membresía activa.',
                fecha_hora=ahora - timedelta(minutes=45 - (i * 10)),
                tiempo_respuesta_ms=31.5 + (i * 2.1),
                metodo='SIMULACION_QR'
            )

        # 1 Socio que ya salió (Luis Ramírez: entró y luego salió)
        s_luis = socios_dict['Luis']
        RegistroAcceso.objects.create(
            socio=s_luis,
            tipo='ENTRADA',
            resultado='PERMITIDO',
            motivo='Acceso permitido. Membresía activa.',
            fecha_hora=ahora - timedelta(hours=2),
            tiempo_respuesta_ms=28.4,
            metodo='SIMULACION_QR'
        )
        RegistroAcceso.objects.create(
            socio=s_luis,
            tipo='SALIDA',
            resultado='PERMITIDO',
            motivo='Salida registrada con éxito.',
            fecha_hora=ahora - timedelta(minutes=30),
            tiempo_respuesta_ms=22.8,
            metodo='SIMULACION_QR'
        )

        # 1 Registro de intento de acceso DENEGADO (Valentina Morales con membresía vencida)
        s_vale = socios_dict['Valentina']
        RegistroAcceso.objects.create(
            socio=s_vale,
            tipo='ENTRADA',
            resultado='DENEGADO',
            motivo=f"Membresía VENCIDA el {(hoy - timedelta(days=5)).strftime('%d/%m/%Y')}. Debe renovar su plan.",
            fecha_hora=ahora - timedelta(minutes=15),
            tiempo_respuesta_ms=35.2,
            metodo='SIMULACION_QR'
        )

        self.stdout.write(self.style.SUCCESS(
            "\n¡Carga de datos completada exitosamente!"
            "\n- Socios: 13 (incluye activos, vencidos, congelados y baja lógica)."
            "\n- Membresías y Pagos creados y vinculados."
            "\n- Clases creadas (Spinning con cupos llenos y 2 personas en lista de espera)."
            "\n- Aforo actual: 4 personas en el gimnasio."
            "\n- Configura las cuentas de administración y recepción por separado."
        ))
