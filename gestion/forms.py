from datetime import timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Prefetch, Q
from django.utils import timezone
from .models import Socio, Plan, Membresia, Pago, Profesor, Clase, Sala, Actividad


class SocioForm(forms.ModelForm):
    class Meta:
        model = Socio
        fields = ['rut', 'nombres', 'apellidos', 'email', 'telefono', 'fecha_nacimiento']
        widgets = {'fecha_nacimiento': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


def normalizar_rut(valor):
    rut = ''.join(c for c in valor.upper() if c not in '.- ')
    return (rut[:-1].lstrip('0') or '0') + rut[-1] if len(rut) > 1 else rut


def validar_rut_personal(valor):
    rut = normalizar_rut(valor)
    if not (8 <= len(rut) <= 9 and rut[:-1].isdigit() and (rut[-1].isdigit() or rut[-1] == 'K')):
        raise forms.ValidationError('Ingresa un RUT válido.')
    suma = sum(int(d) * (2 + i % 6) for i, d in enumerate(reversed(rut[:-1])))
    digito = 11 - suma % 11
    esperado = '0' if digito == 11 else 'K' if digito == 10 else str(digito)
    if rut[-1] != esperado:
        raise forms.ValidationError('El dígito verificador del RUT no es válido.')
    return f'{rut[:-1]}-{rut[-1]}'


class RegistroSocioForm(forms.Form):
    nombres = forms.CharField(max_length=100)
    apellidos = forms.CharField(max_length=100)
    rut = forms.CharField(max_length=12, label='RUT')
    email = forms.EmailField(max_length=150, label='Correo electrónico')
    telefono = forms.CharField(max_length=20, label='Teléfono')
    fecha_nacimiento = forms.DateField(required=False, label='Fecha de nacimiento (opcional)', widget=forms.DateInput(attrs={'type': 'date'}))
    clave = forms.CharField(label='Contraseña', strip=False, widget=forms.PasswordInput)
    confirmacion = forms.CharField(label='Confirmar contraseña', strip=False, widget=forms.PasswordInput)
    pregunta = forms.ChoiceField(label='Pregunta de seguridad', choices=[('', 'Selecciona una pregunta')] + Socio.PREGUNTAS_SEGURIDAD)
    respuesta = forms.CharField(label='Respuesta de seguridad', max_length=100, widget=forms.PasswordInput)

    def clean_rut(self):
        rut = normalizar_rut(self.cleaned_data['rut'])
        if not (8 <= len(rut) <= 9 and rut[:-1].isdigit() and (rut[-1].isdigit() or rut[-1] == 'K')):
            raise forms.ValidationError('Ingresa un RUT válido, por ejemplo 12.345.678-5.')
        suma = sum(int(d) * (2 + i % 6) for i, d in enumerate(reversed(rut[:-1])))
        digito = 11 - suma % 11
        esperado = '0' if digito == 11 else 'K' if digito == 10 else str(digito)
        if rut[-1] != esperado:
            raise forms.ValidationError('El dígito verificador del RUT no es válido.')
        return f'{rut[:-1]}-{rut[-1]}'

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()

    def clean(self):
        from django.contrib.auth import password_validation
        data = super().clean()
        if data.get('clave') and data.get('confirmacion'):
            if data['clave'] != data['confirmacion']:
                self.add_error('confirmacion', 'Las contraseñas no coinciden.')
            else:
                try:
                    password_validation.validate_password(data['clave'], get_user_model()(username=data.get('email', ''), first_name=data.get('nombres', ''), last_name=data.get('apellidos', ''), email=data.get('email', '')))
                except forms.ValidationError as error:
                    self.add_error('clave', error)
        return data


class ContactoSocioForm(forms.ModelForm):
    class Meta:
        model = Socio
        fields = ['email', 'telefono']

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if len(email) > 150:
            raise forms.ValidationError('El correo debe tener como máximo 150 caracteres.')
        if Socio.all_objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Este correo ya está asociado a otra ficha.')
        if get_user_model().objects.filter(username__iexact=email).exclude(pk=self.instance.usuario_id).exists():
            raise forms.ValidationError('Este correo ya está asociado a otra cuenta.')
        return email


class SeguridadSocioForm(forms.Form):
    clave_actual = forms.CharField(label='Contraseña actual', strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))
    pregunta = forms.ChoiceField(label='Pregunta de seguridad', choices=[('', 'Selecciona una pregunta')] + Socio.PREGUNTAS_SEGURIDAD)
    respuesta = forms.CharField(label='Nueva respuesta de seguridad', max_length=100, widget=forms.PasswordInput(attrs={'autocomplete': 'off'}))

    def __init__(self, usuario, *args, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)

    def clean_clave_actual(self):
        clave = self.cleaned_data['clave_actual']
        if not self.usuario.check_password(clave):
            raise forms.ValidationError('La contraseña actual no es correcta.')
        return clave


class ActivarSocioForm(forms.Form):
    rut = forms.CharField(label='RUT', max_length=12)
    codigo = forms.CharField(label='Código de activación', max_length=128, widget=forms.PasswordInput)
    clave = forms.CharField(label='Nueva contraseña', strip=False, widget=forms.PasswordInput)
    confirmacion = forms.CharField(label='Confirmar contraseña', strip=False, widget=forms.PasswordInput)
    pregunta = forms.ChoiceField(label='Pregunta de seguridad', choices=[('', 'Selecciona una pregunta')] + Socio.PREGUNTAS_SEGURIDAD)
    respuesta = forms.CharField(label='Respuesta de seguridad', max_length=100, widget=forms.PasswordInput)

    def clean(self):
        data = super().clean()
        if data.get('clave') and data.get('confirmacion') and data['clave'] != data['confirmacion']:
            self.add_error('confirmacion', 'Las contraseñas no coinciden.')
        return data


class CorreoRecuperacionForm(forms.Form):
    email = forms.EmailField(label='Correo electrónico', max_length=150)


class RespuestaRecuperacionForm(forms.Form):
    respuesta = forms.CharField(label='Respuesta de seguridad', max_length=100, widget=forms.PasswordInput(attrs={'autocomplete': 'off'}))


class NuevaClaveSocioForm(forms.Form):
    clave = forms.CharField(label='Nueva contraseña', strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    confirmacion = forms.CharField(label='Confirmar contraseña', strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def clean(self):
        data = super().clean()
        if data.get('clave') and data.get('confirmacion') and data['clave'] != data['confirmacion']:
            self.add_error('confirmacion', 'Las contraseñas no coinciden.')
        return data


class SolicitudCongelamientoForm(forms.Form):
    fecha_inicio = forms.DateField(label='Fecha de inicio', widget=forms.DateInput(attrs={'type': 'date'}))
    fecha_fin = forms.DateField(label='Último día de la pausa', widget=forms.DateInput(attrs={'type': 'date'}))
    motivo = forms.CharField(label='Motivo del congelamiento', max_length=500, widget=forms.Textarea(attrs={'rows': 3}))


class SolicitudCongelamientoPersonalForm(SolicitudCongelamientoForm):
    motivo = forms.CharField(label='Motivo del congelamiento', max_length=500, widget=forms.Textarea(attrs={'rows': 3}))


class ContratarMembresiaSocioForm(forms.Form):
    plan = forms.ModelChoiceField(queryset=Plan.objects.none(), label='Plan')
    fecha_inicio = forms.DateField(label='Fecha de inicio', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    metodo_pago = forms.ChoiceField(label='Medio de pago', choices=[('TARJETA_DEBITO', 'Débito'), ('TARJETA_CREDITO', 'Crédito')])
    rut = forms.CharField(label='Tu RUT', max_length=12)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['plan'].queryset = Plan.objects.all()


class EstiloForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


class ContactoProfesorForm(EstiloForm):
    class Meta:
        model = Profesor
        fields = ['email', 'telefono']

    def clean_email(self):
        correo = self.cleaned_data['email'].strip().lower()
        if self.instance.usuario_id and not correo:
            raise forms.ValidationError('Ingresa un correo para recuperar la cuenta.')
        if correo and get_user_model().objects.filter(email__iexact=correo).exclude(pk=self.instance.usuario_id).exists():
            raise forms.ValidationError('Este correo ya está asociado a una cuenta.')
        return correo

    def save(self, commit=True):
        profesor = super().save(commit=commit)
        if commit and profesor.usuario_id:
            get_user_model().objects.filter(pk=profesor.usuario_id).update(email=profesor.email)
        return profesor


class PlanForm(EstiloForm):
    class Meta:
        model = Plan
        fields = ['nombre', 'descripcion', 'duracion_dias', 'precio', 'permite_clases', 'limite_clases', 'dias_congelamiento']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(['nombre', 'descripcion', 'permite_clases', 'duracion_dias', 'precio', 'limite_clases', 'dias_congelamiento'])

    def clean_precio(self):
        precio = self.cleaned_data['precio']
        if precio <= 0:
            raise forms.ValidationError('El precio debe ser mayor que cero.')
        return precio

    def clean_duracion_dias(self):
        dias = self.cleaned_data['duracion_dias']
        if dias < 1:
            raise forms.ValidationError('La duración debe ser al menos un día.')
        return dias


class SelectorPlan(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value and hasattr(value, 'instance'):
            option['attrs']['data-duracion'] = value.instance.duracion_dias
            option['attrs']['data-precio'] = str(value.instance.precio)
        return option


class SelectorSocioRenovacion(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value and hasattr(value, 'instance'):
            hoy = timezone.localdate()
            membresias = value.instance.membresias_para_formulario
            vigente = next((m for m in membresias if m.fecha_inicio <= hoy <= m.fecha_fin), None)
            futura = any(m.fecha_inicio > hoy for m in membresias)
            if futura:
                option['attrs']['data-aviso'] = 'Este socio ya tiene una membresía programada.'
            elif vigente and vigente.estado == 'CONGELADA':
                option['attrs']['data-aviso'] = 'La membresía está congelada; revisa su situación antes de renovar.'
            elif vigente:
                option['attrs']['data-inicio-renovacion'] = (vigente.fecha_fin + timedelta(days=1)).isoformat()
            else:
                option['attrs']['data-inicio-renovacion'] = hoy.isoformat()
        return option


class MembresiaForm(EstiloForm):
    class Meta:
        model = Membresia
        fields = ['socio', 'plan', 'fecha_inicio', 'fecha_fin', 'estado', 'motivo_congelacion']
        widgets = {name: forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d') for name in ['fecha_inicio', 'fecha_fin']}



    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields['socio'].widget = SelectorSocioRenovacion(attrs={'class': 'form-control'})
            self.fields['socio'].queryset = Socio.objects.prefetch_related(Prefetch(
                'membresias', queryset=Membresia.objects.filter(estado__in=['ACTIVA', 'CONGELADA']).order_by('-fecha_fin'),
                to_attr='membresias_para_formulario',
            ))
            self.fields['plan'].widget = SelectorPlan(attrs={'class': 'form-control'})
            self.fields['plan'].queryset = Plan.objects.filter(activo=True)
            self.fields['fecha_fin'].required = False
            self.fields['fecha_fin'].widget.attrs['readonly'] = True
            self.fields['fecha_fin'].help_text = 'Se calcula automáticamente según el plan y la fecha de inicio.'
            self.fields['plan'].help_text = 'El precio y la duración se muestran al seleccionar un plan.'
            self.fields['fecha_inicio'].help_text = ('Primera contratación: hoy o hasta 30 días después. '
                'Renovación anticipada: comienza al día siguiente de la membresía vigente.')

    def clean(self):
        data = super().clean()
        if not self.instance.pk:
            plan, inicio, socio = data.get('plan'), data.get('fecha_inicio'), data.get('socio')
            if plan and inicio:
                data['fecha_fin'] = inicio + timedelta(days=plan.duracion_dias - 1)
            if inicio and socio:
                self.instance.fecha_inicio = inicio
                hoy = timezone.localdate()
                existentes = Membresia.objects.filter(socio=socio, estado__in=['ACTIVA', 'CONGELADA'])
                programada = existentes.filter(fecha_inicio__gt=hoy).exists()
                vigente = existentes.filter(fecha_inicio__lte=hoy, fecha_fin__gte=hoy).order_by('-fecha_fin').first()
                if programada:
                    self.add_error('fecha_inicio', 'El socio ya tiene una membresía programada. Revísala antes de contratar otra.')
                elif vigente and vigente.estado == 'CONGELADA':
                    self.add_error('fecha_inicio', 'La membresía está congelada. Resuelve el congelamiento antes de programar una renovación.')
                elif vigente and inicio != vigente.fecha_fin + timedelta(days=1):
                    self.add_error('fecha_inicio', 'La renovación debe comenzar el día siguiente al término de la membresía vigente.')
                elif not vigente and (inicio < hoy or inicio > hoy + timedelta(days=30)):
                    self.add_error('fecha_inicio', 'Elige hoy o una fecha dentro de los próximos 30 días.')
        return data


class SelectorMembresia(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value and hasattr(value, 'instance'):
            precio = value.instance.precio_contratado
            option['attrs']['data-precio'] = '' if precio is None else str(precio)
        return option


class PagoForm(EstiloForm):
    class Meta:
        model = Pago
        fields = ['membresia', 'monto', 'metodo_pago', 'numero_comprobante', 'observaciones']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['membresia'].widget = SelectorMembresia(attrs={'class': 'form-control'})
        self.fields['membresia'].queryset = Membresia.objects.select_related('socio', 'plan')
        self.fields['monto'].required = False
        self.fields['monto'].help_text = ('Para membresías nuevas se usa el precio contratado. '
            'Si la membresía es anterior a esta mejora, revisa el precio acordado e ingrésalo manualmente.')

    def clean(self):
        data = super().clean()
        membresia = data.get('membresia')
        if membresia and membresia.precio_contratado is not None:
            data['monto'] = membresia.precio_contratado
            self._errors.pop('monto', None)
        elif membresia and data.get('monto') is None:
            self.add_error('monto', 'Esta membresía antigua no tiene precio contratado registrado. Revisa e ingresa el monto.')
        if data.get('monto') is not None and data['monto'] <= 0:
            self.add_error('monto', 'El monto debe ser mayor que cero.')
        return data


class AnularPagoForm(forms.Form):
    motivo = forms.CharField(label='Motivo de la anulación', max_length=500, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}))


class ProfesorForm(EstiloForm):
    class Meta:
        model = Profesor
        fields = ['rut', 'nombres', 'apellidos', 'especialidad', 'email', 'telefono']

    def clean_rut(self):
        valor = self.cleaned_data.get('rut')
        if not valor:
            raise forms.ValidationError('Ingresa el RUT del profesor.')
        rut = validar_rut_personal(valor)
        from .models import PerfilPersonal
        if PerfilPersonal.objects.filter(rut=rut).exclude(usuario_id=self.instance.usuario_id).exists():
            raise forms.ValidationError('Este RUT ya está asociado a otra cuenta del personal.')
        return rut

    def clean_email(self):
        correo = self.cleaned_data['email'].strip().lower()
        if not correo:
            raise forms.ValidationError('Ingresa el correo del profesor.')
        if get_user_model().objects.filter(email__iexact=correo).exclude(pk=self.instance.usuario_id).exists():
            raise forms.ValidationError('Este correo ya está asociado a una cuenta.')
        return correo

    def save(self, commit=True):
        profesor = super().save(commit=commit)
        if commit and profesor.usuario_id:
            from .models import PerfilPersonal
            get_user_model().objects.filter(pk=profesor.usuario_id).update(email=profesor.email)
            PerfilPersonal.objects.filter(usuario_id=profesor.usuario_id).update(rut=profesor.rut, telefono=profesor.telefono)
        return profesor



class ActividadForm(EstiloForm):
    class Meta:
        model = Actividad
        fields = ['nombre', 'tipo', 'descripcion', 'visible_sin_horario']
        widgets = {'descripcion': forms.Textarea(attrs={'rows': 3})}


class SalaForm(EstiloForm):
    class Meta:
        model = Sala
        fields = ['nombre', 'capacidad', 'descripcion']

    def clean_capacidad(self):
        capacidad = self.cleaned_data['capacidad']
        if self.instance.pk and Clase.objects.filter(
            sala_asignada=self.instance, fecha__gte=timezone.localdate(), capacidad_maxima__gt=capacidad,
        ).exists():
            raise forms.ValidationError('No se puede reducir la capacidad: hay clases actuales o futuras con más cupos.')
        return capacidad


class ClaseForm(EstiloForm):
    class Meta:
        model = Clase
        fields = ['actividad', 'nombre', 'profesor', 'fecha', 'hora_inicio', 'hora_fin', 'capacidad_maxima', 'sala_asignada']
        widgets = {
            'fecha': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'hora_inicio': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
            'hora_fin': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['actividad'].required = True
        actividades = Actividad.objects.all()
        if self.instance.pk and self.instance.actividad_id:
            actividades = Actividad.all_objects.filter(Q(activo=True) | Q(pk=self.instance.actividad_id))
        self.fields['actividad'].queryset = actividades
        if self.instance.pk and not self.instance.actividad_id:
            self.fields['actividad'].help_text = 'Esta clase se creó antes del catálogo. Selecciona la actividad correspondiente.'
        self.fields['sala_asignada'].required = True
        salas = Sala.objects.all()
        if self.instance.pk and self.instance.sala_asignada_id:
            salas = Sala.all_objects.filter(Q(activo=True) | Q(pk=self.instance.sala_asignada_id))
        self.fields['sala_asignada'].queryset = salas
        if self.instance.pk and not self.instance.sala_asignada_id:
            self.fields['sala_asignada'].help_text = f'Sala anterior: {self.instance.sala}. Selecciona una sala del catálogo.'

    def clean(self):
        data = super().clean()
        inicio, fin = data.get('hora_inicio'), data.get('hora_fin')
        if inicio and fin and fin <= inicio:
            self.add_error('hora_fin', 'La hora de término debe ser posterior al inicio.')
        capacidad = data.get('capacidad_maxima')
        if capacidad is not None and capacidad < 1:
            self.add_error('capacidad_maxima', 'La capacidad debe ser al menos un cupo.')
        if self.instance.pk and capacidad is not None and capacidad < self.instance.total_confirmadas():
            self.add_error('capacidad_maxima', 'La capacidad no puede ser menor a las reservas confirmadas.')
        sala, fecha, profesor = data.get('sala_asignada'), data.get('fecha'), data.get('profesor')
        if self.instance.pk:
            original = Clase.all_objects.get(pk=self.instance.pk)
            campos_protegidos = ('actividad', 'nombre', 'profesor', 'fecha', 'hora_inicio', 'hora_fin', 'sala_asignada')
            if self.instance.reservas.filter(estado__in=['CONFIRMADA', 'LISTA_ESPERA']).exists():
                for campo in campos_protegidos:
                    if campo in data and data[campo] != getattr(original, campo):
                        self.add_error(campo, 'La clase tiene reservas o lista de espera. Todavía no se pueden cambiar estos datos sin gestionar a los socios afectados.')
        if sala and capacidad and capacidad > sala.capacidad:
            self.add_error('capacidad_maxima', f'La capacidad no puede superar las {sala.capacidad} personas de la sala.')
        if fecha and inicio and fin and fin > inicio:
            cruces = Clase.objects.filter(fecha=fecha, hora_inicio__lt=fin, hora_fin__gt=inicio)
            if self.instance.pk:
                cruces = cruces.exclude(pk=self.instance.pk)
            if sala and cruces.filter(Q(sala_asignada=sala) | Q(sala_asignada__isnull=True, sala__iexact=sala.nombre)).exists():
                self.add_error('sala_asignada', 'La sala ya tiene una clase en ese horario.')
            if profesor and cruces.filter(profesor=profesor).exists():
                self.add_error('profesor', 'El profesor ya tiene una clase en ese horario.')
        return data

    def save(self, commit=True):
        clase = super().save(commit=False)
        clase.sala = clase.sala_asignada.nombre[:50]
        if commit:
            clase.save()
            self.save_m2m()
        return clase


class CrearCuentaProfesorForm(forms.Form):
    usuario = forms.CharField(max_length=150, label='Nombre de usuario', validators=[get_user_model().username_validator], widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}))

    def clean_usuario(self):
        usuario = self.cleaned_data['usuario'].strip()
        if get_user_model().objects.filter(username__iexact=usuario).exists():
            raise forms.ValidationError('Este nombre de usuario ya está en uso.')
        return usuario


class CrearCuentaRecepcionForm(CrearCuentaProfesorForm):
    rut = forms.CharField(max_length=12, label='RUT')
    nombres = forms.CharField(max_length=150, label='Nombres')
    apellidos = forms.CharField(max_length=150, label='Apellidos')
    correo = forms.EmailField(label='Correo electrónico')
    telefono = forms.CharField(max_length=20, label='Teléfono')

    def clean_rut(self):
        from .models import PerfilPersonal
        rut = validar_rut_personal(self.cleaned_data['rut'])
        if PerfilPersonal.objects.filter(rut=rut).exists():
            raise forms.ValidationError('Ya existe una ficha del personal con este RUT.')
        return rut

    def clean_correo(self):
        correo = self.cleaned_data['correo'].strip().lower()
        if get_user_model().objects.filter(email__iexact=correo).exists():
            raise forms.ValidationError('Este correo ya está asociado a una cuenta.')
        return correo


class EditarRecepcionistaForm(forms.Form):
    nombres = forms.CharField(max_length=150, label='Nombres')
    apellidos = forms.CharField(max_length=150, label='Apellidos')
    rut = forms.CharField(max_length=12, label='RUT')
    correo = forms.EmailField(label='Correo electrónico')
    telefono = forms.CharField(max_length=20, label='Teléfono')

    def __init__(self, *args, usuario, **kwargs):
        self.usuario = usuario
        perfil = getattr(usuario, 'perfil_personal', None)
        kwargs.setdefault('initial', {'nombres': usuario.first_name, 'apellidos': usuario.last_name, 'rut': perfil.rut if perfil else '', 'correo': usuario.email, 'telefono': perfil.telefono if perfil else ''})
        super().__init__(*args, **kwargs)

    def clean_rut(self):
        from .models import PerfilPersonal
        rut = validar_rut_personal(self.cleaned_data['rut'])
        if PerfilPersonal.objects.filter(rut=rut).exclude(usuario=self.usuario).exists():
            raise forms.ValidationError('Ya existe una ficha del personal con este RUT.')
        return rut

    def clean_correo(self):
        correo = self.cleaned_data['correo'].strip().lower()
        usuarios = get_user_model().objects.exclude(pk=self.usuario.pk)
        if self.usuario.email and self.usuario.username.casefold() == self.usuario.email.casefold() and len(correo) > 150:
            raise forms.ValidationError('El correo no puede superar 150 caracteres para esta cuenta.')
        if usuarios.filter(Q(email__iexact=correo) | Q(username__iexact=correo)).exists():
            raise forms.ValidationError('Este correo ya está asociado a una cuenta.')
        return correo

    def save(self):
        from .models import PerfilPersonal
        datos = self.cleaned_data
        usuario = self.usuario
        campos = ['first_name', 'last_name', 'email']
        if usuario.username.casefold() == usuario.email.casefold() and usuario.email:
            usuario.username = datos['correo']
            campos.append('username')
        usuario.first_name, usuario.last_name, usuario.email = datos['nombres'], datos['apellidos'], datos['correo']
        usuario.save(update_fields=campos)
        PerfilPersonal.objects.update_or_create(usuario=usuario, defaults={'rut': datos['rut'], 'telefono': datos['telefono']})


class ActivarCuentaPersonalForm(ActivarSocioForm):
    pass


class ActivarCuentaProfesorForm(forms.Form):
    usuario = forms.CharField(max_length=150, label='Nombre de usuario', widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'username'}))
    codigo = forms.CharField(max_length=128, label='Código de activación', widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'off'}))
    clave = forms.CharField(label='Nueva contraseña', strip=False, widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}))
    confirmacion = forms.CharField(label='Confirmar contraseña', strip=False, widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}))

    def clean(self):
        data = super().clean()
        if data.get('clave') and data.get('confirmacion') and data['clave'] != data['confirmacion']:
            self.add_error('confirmacion', 'Las contraseñas no coinciden.')
        return data
