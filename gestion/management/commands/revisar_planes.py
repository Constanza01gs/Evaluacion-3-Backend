from django.core.management.base import BaseCommand

from gestion.models import Membresia, Plan


class Command(BaseCommand):
    help = 'Revisa los planes y las condiciones históricas sin modificar datos.'

    def handle(self, *args, **options):
        planes = Plan.all_objects.order_by('nombre')
        if not planes.exists():
            self.stdout.write('No hay planes registrados.')
            return

        for plan in planes:
            membresias = Membresia.all_objects.filter(plan=plan)
            clases = ('No incluye' if not plan.permite_clases else
                      str(plan.limite_clases) if plan.limite_clases is not None else 'Ilimitadas')
            congelamiento = (str(plan.dias_congelamiento) if plan.dias_congelamiento is not None
                             else 'Por definir')
            self.stdout.write(f'\nPlan: {plan.nombre} (ID {plan.pk})')
            self.stdout.write(f'  Disponible: {"Sí" if plan.activo else "No"}')
            self.stdout.write(f'  Duración: {plan.duracion_dias} días | Precio: ${plan.precio} CLP')
            self.stdout.write(f'  Clases: {clases} | Días de congelamiento: {congelamiento}')
            self.stdout.write(f'  Membresías asociadas: {membresias.count()}')
            self.stdout.write(f'  Sin registro propio de clases: {membresias.filter(clases_contratadas__isnull=True).count()}')
            self.stdout.write(f'  Sin límite de clases registrado: {membresias.filter(limite_clases_registrado=False).count()}')
            self.stdout.write(f'  Sin días de congelamiento registrados: {membresias.filter(dias_congelamiento_contratados__isnull=True).count()}')
        self.stdout.write('\nRevisión terminada. No se modificaron datos.')
