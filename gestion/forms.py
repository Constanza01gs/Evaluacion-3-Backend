from django import forms
from .models import Socio, Plan, Membresia, Pago, Profesor, Clase


class SocioForm(forms.ModelForm):
    class Meta:
        model = Socio
        fields = ['rut', 'nombres', 'apellidos', 'email', 'telefono', 'fecha_nacimiento']
        widgets = {'fecha_nacimiento': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


class EstiloForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


class PlanForm(EstiloForm):
    class Meta:
        model = Plan
        fields = ['nombre', 'descripcion', 'duracion_dias', 'precio', 'permite_clases']

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


class MembresiaForm(EstiloForm):
    class Meta:
        model = Membresia
        fields = ['socio', 'plan', 'fecha_inicio', 'fecha_fin', 'estado', 'motivo_congelacion']
        widgets = {name: forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d') for name in ['fecha_inicio', 'fecha_fin']}


class PagoForm(EstiloForm):
    class Meta:
        model = Pago
        fields = ['membresia', 'monto', 'metodo_pago', 'numero_comprobante', 'observaciones']

    def clean_monto(self):
        monto = self.cleaned_data['monto']
        if monto <= 0:
            raise forms.ValidationError('El monto debe ser mayor que cero.')
        return monto


class AnularPagoForm(forms.Form):
    motivo = forms.CharField(label='Motivo de la anulación', max_length=500, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}))


class ProfesorForm(EstiloForm):
    class Meta:
        model = Profesor
        fields = ['nombres', 'apellidos', 'especialidad', 'email', 'telefono']


class ClaseForm(EstiloForm):
    class Meta:
        model = Clase
        fields = ['nombre', 'profesor', 'fecha', 'hora_inicio', 'hora_fin', 'capacidad_maxima', 'sala']
        widgets = {
            'fecha': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'hora_inicio': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
            'hora_fin': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
        }

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
        return data
