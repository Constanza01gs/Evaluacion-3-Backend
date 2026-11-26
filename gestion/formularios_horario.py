from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import Actividad, Profesor, Sala

DIAS = ('Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo')

class HorarioFijoForm(forms.Form):
    nombre = forms.CharField(max_length=100, label='Nombre de la clase')
    actividad = forms.ModelChoiceField(queryset=Actividad.objects.none(), label='Tipo de clase')
    profesor = forms.ModelChoiceField(queryset=Profesor.objects.none(), label='Profesor')
    sala = forms.ModelChoiceField(queryset=Sala.objects.none(), label='Sala')
    capacidad = forms.IntegerField(min_value=1, label='Capacidad máxima')
    desde = forms.DateField(label='Aplicar desde', widget=forms.DateInput(attrs={'type':'date'}))
    hasta = forms.DateField(label='Fecha de término (opcional)', required=False, widget=forms.DateInput(attrs={'type':'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['actividad'].queryset = Actividad.objects.all()
        self.fields['profesor'].queryset = Profesor.objects.all()
        self.fields['sala'].queryset = Sala.objects.all()
        for i, nombre in enumerate(DIAS):
            self.fields[f'dia_{i}'] = forms.BooleanField(required=False, label=nombre)
            self.fields[f'inicio_{i}'] = forms.TimeField(required=False, label='Inicio', widget=forms.TimeInput(attrs={'type':'time'}, format='%H:%M'))
            self.fields[f'fin_{i}'] = forms.TimeField(required=False, label='Término', widget=forms.TimeInput(attrs={'type':'time'}, format='%H:%M'))
        for name, field in self.fields.items():
            field.widget.attrs['id'] = f'id_horario_{name}'

    def clean(self):
        d=super().clean()
        desde,hasta=d.get('desde'),d.get('hasta')
        if desde and desde < timezone.localdate(): self.add_error('desde','La fecha debe ser hoy o posterior.')
        if desde and hasta and hasta < desde: self.add_error('hasta','La fecha de término debe ser posterior al inicio.')
        if d.get('sala') and d.get('capacidad') and d['capacidad'] > d['sala'].capacidad: self.add_error('capacidad','La capacidad supera el aforo de la sala.')
        if not any(d.get(f'dia_{i}') for i in range(7)): raise ValidationError('Selecciona al menos un día de la semana.')
        for i in range(7):
            if d.get(f'dia_{i}'):
                ini,fin=d.get(f'inicio_{i}'),d.get(f'fin_{i}')
                if not ini or not fin: raise ValidationError(f'Completa ambas horas para {DIAS[i].lower()}.')
                if fin <= ini: raise ValidationError(f'La hora de término debe ser posterior al inicio para {DIAS[i].lower()}.')
        return d
