"""
Capa de Servicios y Lógica de Negocio para FitLife.
Contiene las reglas de negocio aisladas y comprobables para:
1. Control de acceso QR con medición de latencia en milisegundos y consistencia de movimientos.
2. Gestión concurrente de reservas y lista de espera FIFO con promoción automática.
3. Cálculo en tiempo real de aforo y ocupación.
"""

import time
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from .models import Socio, Membresia, RegistroAcceso, Clase, Reserva


# ------------------------------------------------------------------------------
# 1. Servicio de Control de Acceso mediante Código QR
# ------------------------------------------------------------------------------
@transaction.atomic
def validar_y_registrar_acceso(identificador: str, tipo: str = 'ENTRADA', metodo: str = 'SIMULACION_QR') -> dict:
    """
    Valida y registra el acceso de un socio mediante simulación de código QR o ID manual.
    Mide la latencia de respuesta del servidor en milisegundos.
    Aplica controles de consistencia (evita doble entrada consecutiva) y chequeo de membresía.
    """
    t_inicio = time.perf_counter()
    hoy = timezone.localdate()
    identificador_limpio = (identificador or '').strip()

    # 1. Buscar socio por código QR, RUT o ID
    socio = (
        Socio.all_objects.select_for_update().filter(codigo_qr__iexact=identificador_limpio).first() or
        Socio.all_objects.select_for_update().filter(rut__iexact=identificador_limpio).first() or
        (Socio.all_objects.select_for_update().filter(id=identificador_limpio).first() if identificador_limpio.isdigit() else None)
    )

    if not socio:
        t_fin = time.perf_counter()
        latencia_ms = round((t_fin - t_inicio) * 1000.0, 2)
        return {
            'autorizado': False,
            'motivo': f"Código '{identificador_limpio}' no encontrado o no corresponde a un socio activo.",
            'socio': None,
            'tipo': tipo,
            'tiempo_respuesta_ms': latencia_ms,
            'registro_id': None,
        }

    # 2. Verificar si el socio está activo (borrado lógico)
    if not socio.activo and tipo == 'ENTRADA':
        t_fin = time.perf_counter()
        latencia_ms = round((t_fin - t_inicio) * 1000.0, 2)
        registro = RegistroAcceso.objects.create(
            socio=socio,
            tipo=tipo,
            resultado='DENEGADO',
            motivo="El socio ha sido desactivado del sistema (baja lógica).",
            tiempo_respuesta_ms=latencia_ms,
            metodo=metodo
        )
        return {
            'autorizado': False,
            'motivo': "El socio ha sido desactivado del sistema.",
            'socio': socio,
            'tipo': tipo,
            'tiempo_respuesta_ms': latencia_ms,
            'registro_id': registro.id,
        }

    # 3. Control de consistencia de movimientos (Entrada / Salida)
    esta_dentro = socio.esta_dentro()

    if tipo == 'ENTRADA' and esta_dentro:
        t_fin = time.perf_counter()
        latencia_ms = round((t_fin - t_inicio) * 1000.0, 2)
        registro = RegistroAcceso.objects.create(
            socio=socio,
            tipo='ENTRADA',
            resultado='DENEGADO',
            motivo="El socio ya se encuentra dentro del gimnasio. Debe registrar salida antes de volver a ingresar.",
            tiempo_respuesta_ms=latencia_ms,
            metodo=metodo
        )
        return {
            'autorizado': False,
            'motivo': "Acceso denegado: El socio ya registra una entrada activa sin salida previa.",
            'socio': socio,
            'tipo': tipo,
            'tiempo_respuesta_ms': latencia_ms,
            'registro_id': registro.id,
        }

    # 4. Solo se registra una salida cuando existe una entrada previa sin salida.
    if tipo == 'SALIDA':
        t_fin = time.perf_counter()
        latencia_ms = round((t_fin - t_inicio) * 1000.0, 2)
        motivo_salida = "Salida registrada con éxito." if esta_dentro else "No se puede registrar una salida sin una entrada previa."
        registro = RegistroAcceso.objects.create(
            socio=socio,
            tipo='SALIDA',
            resultado='PERMITIDO' if esta_dentro else 'DENEGADO',
            motivo=motivo_salida,
            tiempo_respuesta_ms=latencia_ms,
            metodo=metodo
        )
        return {
            'autorizado': esta_dentro,
            'motivo': motivo_salida,
            'socio': socio,
            'tipo': tipo,
            'tiempo_respuesta_ms': latencia_ms,
            'registro_id': registro.id,
        }

    # 5. En caso de Entrada: validar estado de la membresía
    membresia = socio.membresia_vigente()
    if not membresia:
        # Buscar si tiene membresías vencidas o congeladas para dar un mensaje explicativo exacto
        ultima_m = socio.membresias.order_by('-fecha_fin').first()
        if ultima_m:
            ultima_m.actualizar_estado()
            if not ultima_m.plan.activo:
                motivo = "El plan asociado a la membresía está desactivado."
            elif ultima_m.estado == 'CONGELADA':
                motivo = f"Membresía CONGELADA temporalmente. Motivo: {ultima_m.motivo_congelacion or 'Pausa solicitada'}."
            elif ultima_m.estado == 'VENCIDA' or ultima_m.fecha_fin < hoy:
                motivo = f"Membresía VENCIDA el {ultima_m.fecha_fin.strftime('%d/%m/%Y')}. Debe renovar su plan."
            else:
                motivo = f"Membresía en estado {ultima_m.get_estado_display()} no autoriza ingreso."
        else:
            motivo = "El socio no cuenta con ninguna membresía contratada."

        t_fin = time.perf_counter()
        latencia_ms = round((t_fin - t_inicio) * 1000.0, 2)
        registro = RegistroAcceso.objects.create(
            socio=socio,
            tipo='ENTRADA',
            resultado='DENEGADO',
            motivo=motivo,
            tiempo_respuesta_ms=latencia_ms,
            metodo=metodo
        )
        return {
            'autorizado': False,
            'motivo': motivo,
            'socio': socio,
            'tipo': tipo,
            'tiempo_respuesta_ms': latencia_ms,
            'registro_id': registro.id,
        }

    # Evaluar si la membresía activa permite ingresar
    autorizado, motivo = membresia.puede_ingresar()
    resultado = 'PERMITIDO' if autorizado else 'DENEGADO'

    t_fin = time.perf_counter()
    latencia_ms = round((t_fin - t_inicio) * 1000.0, 2)

    registro = RegistroAcceso.objects.create(
        socio=socio,
        tipo='ENTRADA',
        resultado=resultado,
        motivo=motivo,
        tiempo_respuesta_ms=latencia_ms,
        metodo=metodo
    )

    return {
        'autorizado': autorizado,
        'motivo': motivo,
        'socio': socio,
        'tipo': tipo,
        'membresia': membresia,
        'tiempo_respuesta_ms': latencia_ms,
        'registro_id': registro.id,
    }


# ------------------------------------------------------------------------------
# 2. Servicio de Gestión de Reservas y Lista de Espera FIFO
# ------------------------------------------------------------------------------
def gestionar_reserva_clase(clase_id: int, socio_id: int) -> tuple[bool, str, Reserva | None]:
    """
    Registra una reserva para una clase grupal.
    Usa transacciones atómicas (select_for_update) para prevenir condiciones de carrera y sobrecupos.
    Si la clase está llena, posiciona al socio en LISTA_ESPERA (FIFO).
    """
    try:
        with transaction.atomic():
            clase = Clase.objects.select_for_update().get(id=clase_id)
            socio = Socio.objects.get(id=socio_id)

            if not clase.activo:
                return False, "La clase grupal ha sido cancelada o dada de baja.", None

            ahora = timezone.localtime()
            if clase.fecha < ahora.date() or (clase.fecha == ahora.date() and clase.hora_fin <= ahora.time()):
                return False, "No se puede reservar una clase que ya terminó o de una fecha pasada.", None

            if not socio.activo:
                return False, "El socio está desactivado en el sistema.", None

            # Validar que el socio tenga membresía activa que permita clases
            membresia = socio.membresia_vigente()
            if not membresia:
                return False, "El socio no posee una membresía activa vigente.", None

            if not membresia.plan.activo or not membresia.plan.permite_clases:
                return False, f"El plan '{membresia.plan.nombre}' no incluye acceso a clases grupales.", None

            if membresia.fecha_fin < clase.fecha:
                return False, "La membresía debe estar vigente en la fecha de la clase.", None

            # Validar que no tenga ya una reserva activa para esta clase
            reserva_existente = Reserva.objects.filter(
                clase=clase,
                socio=socio,
                estado__in=['CONFIRMADA', 'LISTA_ESPERA']
            ).first()

            if reserva_existente:
                estado_texto = "confirmada" if reserva_existente.estado == 'CONFIRMADA' else "en lista de espera"
                return False, f"El socio ya cuenta con una solicitud {estado_texto} para esta clase.", reserva_existente

            # Determinar cupos confirmados
            confirmadas = clase.reservas.filter(estado='CONFIRMADA').count()

            if confirmadas < clase.capacidad_maxima:
                # Hay cupo disponible -> CONFIRMADA
                reserva = Reserva.objects.create(
                    clase=clase,
                    socio=socio,
                    estado='CONFIRMADA'
                )
                return True, f"Reserva CONFIRMADA para {clase.nombre}.", reserva
            else:
                # Sin cupos -> LISTA_ESPERA (FIFO)
                total_en_espera = clase.reservas.filter(estado='LISTA_ESPERA').count()
                posicion = total_en_espera + 1
                reserva = Reserva.objects.create(
                    clase=clase,
                    socio=socio,
                    estado='LISTA_ESPERA'
                )
                return True, f"Clase llena ({clase.capacidad_maxima}/{clase.capacidad_maxima}). Socio asignado a la LISTA DE ESPERA en la posición #{posicion}.", reserva

    except Clase.DoesNotExist:
        return False, "La clase especificada no existe.", None
    except Socio.DoesNotExist:
        return False, "El socio especificado no existe.", None
    except Exception as e:
        return False, f"Error al procesar la reserva: {str(e)}", None


def cancelar_reserva_clase(reserva_id: int) -> tuple[bool, str]:
    """
    Cancela una reserva.
    Si la reserva era CONFIRMADA y existen personas en LISTA_ESPERA,
    promueve automáticamente a la primera persona en espera (FIFO) a CONFIRMADA.
    """
    try:
        with transaction.atomic():
            clase_id = Reserva.objects.values_list('clase_id', flat=True).get(id=reserva_id)
            clase = Clase.all_objects.select_for_update().get(id=clase_id)
            reserva = Reserva.objects.select_for_update().get(id=reserva_id)

            if reserva.estado == 'CANCELADA':
                return False, "Esta reserva ya se encuentra cancelada."

            era_confirmada = (reserva.estado == 'CONFIRMADA')
            nombre_socio = reserva.socio.nombre_completo

            # Marcar la reserva como cancelada
            reserva.estado = 'CANCELADA'
            reserva.save(update_fields=['estado'])

            mensaje = f"Reserva de {nombre_socio} para {clase.nombre} cancelada exitosamente."

            # Si se liberó un cupo confirmado, promover al siguiente en lista de espera (FIFO)
            if era_confirmada:
                personas_en_espera = (
                    Reserva.objects.select_for_update()
                    .filter(clase=clase, estado='LISTA_ESPERA').select_related('socio')
                    .order_by('fecha_reserva', 'id')
                )
                for candidato in personas_en_espera:
                    socio = candidato.socio
                    membresia = socio.membresia_vigente() if socio.activo else None
                    if not membresia or not membresia.plan.activo or not membresia.plan.permite_clases or membresia.fecha_fin < clase.fecha:
                        candidato.estado = 'CANCELADA'
                        candidato.save(update_fields=['estado', 'fecha_actualizacion'])
                        mensaje += f" Solicitud de {socio.nombre_completo} cancelada por membresía no habilitada."
                        continue
                    candidato.estado = 'CONFIRMADA'
                    candidato.save(update_fields=['estado', 'fecha_actualizacion'])
                    mensaje += f" ¡Cupo asignado automáticamente a {socio.nombre_completo} desde la lista de espera!"
                    break

            return True, mensaje

    except Reserva.DoesNotExist:
        return False, "La reserva no existe."
    except Exception as e:
        return False, f"Error al cancelar la reserva: {str(e)}"


# ------------------------------------------------------------------------------
# 3. Servicio de Cálculo de Aforo en Tiempo Real
# ------------------------------------------------------------------------------
def calcular_aforo_actual() -> dict:
    """
    Calcula la cantidad de personas actualmente dentro de las instalaciones de FitLife.
    Un socio se considera dentro si su último acceso permitido registrado fue una ENTRADA.
    """
    capacidad_maxima = getattr(settings, 'AFORO_MAXIMO', 200)

    # Obtenemos los socios que registran accesos permitidos
    socios_con_movimiento = Socio.all_objects.filter(
        accesos__resultado='PERMITIDO'
    ).distinct()

    personas_dentro = 0
    for socio in socios_con_movimiento:
        if socio.esta_dentro():
            personas_dentro += 1

    disponible = max(capacidad_maxima - personas_dentro, 0)
    porcentaje = round((personas_dentro / capacidad_maxima) * 100, 1) if capacidad_maxima > 0 else 0.0

    return {
        'personas_dentro': personas_dentro,
        'capacidad_maxima': capacidad_maxima,
        'capacidad_disponible': disponible,
        'porcentaje_ocupacion': porcentaje,
        'fecha_hora_actual': timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M:%S"),
        'frecuencia_sondeo_seg': getattr(settings, 'POLLING_INTERVAL_SECONDS', 5),
    }
