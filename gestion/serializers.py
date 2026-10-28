"""
Serializadores de Django REST Framework para la API de FitLife.
Permite exponer los endpoints REST para socios, planes, membresías, clases, reservas y control QR.
"""

from rest_framework import serializers
from .models import Socio, Plan, Membresia, Pago, Profesor, Clase, Reserva, RegistroAcceso


class SocioSerializer(serializers.ModelSerializer):
    nombre_completo = serializers.ReadOnlyField()
    iniciales = serializers.ReadOnlyField()
    esta_dentro = serializers.BooleanField(read_only=True)

    class Meta:
        model = Socio
        fields = [
            'id', 'rut', 'nombres', 'apellidos', 'nombre_completo',
            'iniciales', 'email', 'telefono', 'fecha_nacimiento',
            'codigo_qr', 'fecha_registro', 'activo', 'esta_dentro'
        ]
        read_only_fields = ['codigo_qr', 'fecha_registro', 'activo']


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ['id', 'nombre', 'descripcion', 'duracion_dias', 'precio', 'permite_clases', 'activo']
        read_only_fields = ['activo']


class MembresiaSerializer(serializers.ModelSerializer):
    socio_nombre = serializers.ReadOnlyField(source='socio.nombre_completo')
    plan_nombre = serializers.ReadOnlyField(source='plan.nombre')
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)

    class Meta:
        model = Membresia
        fields = [
            'id', 'socio', 'socio_nombre', 'plan', 'plan_nombre',
            'fecha_inicio', 'fecha_fin', 'estado', 'estado_display',
            'motivo_congelacion', 'activo'
        ]
        read_only_fields = ['activo']

    def validate(self, attrs):
        inicio = attrs.get('fecha_inicio', getattr(self.instance, 'fecha_inicio', None))
        fin = attrs.get('fecha_fin', getattr(self.instance, 'fecha_fin', None))
        if inicio and fin and fin < inicio:
            raise serializers.ValidationError({'fecha_fin': 'La fecha de término no puede ser anterior al inicio.'})
        if self.instance:
            from django.core.exceptions import ValidationError as DjangoValidationError
            import copy
            candidata = copy.copy(self.instance)
            for name, value in attrs.items():
                setattr(candidata, name, value)
            try:
                candidata.clean()
            except DjangoValidationError as exc:
                raise serializers.ValidationError(exc.message_dict if hasattr(exc, 'message_dict') else exc.messages)
        return attrs


class PagoSerializer(serializers.ModelSerializer):
    socio_nombre = serializers.ReadOnlyField(source='membresia.socio.nombre_completo')
    plan_nombre = serializers.ReadOnlyField(source='membresia.plan.nombre')

    class Meta:
        model = Pago
        fields = [
            'id', 'membresia', 'socio_nombre', 'plan_nombre',
            'monto', 'fecha_pago', 'metodo_pago', 'numero_comprobante', 'observaciones',
            'anulado', 'motivo_anulacion', 'fecha_anulacion', 'anulado_por'
        ]
        read_only_fields = ['fecha_pago']


class ProfesorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profesor
        fields = ['id', 'nombres', 'apellidos', 'especialidad', 'email', 'telefono', 'activo']


class ClaseSerializer(serializers.ModelSerializer):
    profesor_nombre = serializers.ReadOnlyField(source='profesor.__str__')
    cupos_ocupados = serializers.IntegerField(source='total_confirmadas', read_only=True)
    cupos_libres = serializers.IntegerField(source='cupos_disponibles', read_only=True)
    en_espera = serializers.IntegerField(source='total_en_espera', read_only=True)
    duracion_min = serializers.ReadOnlyField(source='duracion_minutos')

    class Meta:
        model = Clase
        fields = [
            'id', 'nombre', 'profesor', 'profesor_nombre', 'fecha',
            'hora_inicio', 'hora_fin', 'duracion_min', 'capacidad_maxima',
            'cupos_ocupados', 'cupos_libres', 'en_espera', 'sala', 'activo'
        ]


class ReservaSerializer(serializers.ModelSerializer):
    socio_nombre = serializers.ReadOnlyField(source='socio.nombre_completo')
    clase_nombre = serializers.ReadOnlyField(source='clase.nombre')
    estado_display = serializers.CharField(source='get_estado_display', read_only=True)

    class Meta:
        model = Reserva
        fields = [
            'id', 'clase', 'clase_nombre', 'socio', 'socio_nombre',
            'estado', 'estado_display', 'fecha_reserva', 'fecha_actualizacion'
        ]
        read_only_fields = ['fecha_reserva', 'fecha_actualizacion']


class RegistroAccesoSerializer(serializers.ModelSerializer):
    socio_nombre = serializers.ReadOnlyField(source='socio.nombre_completo')
    socio_rut = serializers.ReadOnlyField(source='socio.rut')

    class Meta:
        model = RegistroAcceso
        fields = [
            'id', 'socio', 'socio_nombre', 'socio_rut', 'tipo',
            'resultado', 'motivo', 'fecha_hora', 'tiempo_respuesta_ms', 'metodo'
        ]
        read_only_fields = ['fecha_hora', 'tiempo_respuesta_ms']


class ValidarAccesoQRSerializer(serializers.Serializer):
    """Valida los datos recibidos desde el simulador de lector QR."""
    identificador = serializers.CharField(required=True, help_text="Código QR, RUT o ID del socio")
    tipo = serializers.ChoiceField(choices=['ENTRADA', 'SALIDA'], default='ENTRADA')
    metodo = serializers.CharField(default='SIMULACION_QR')
