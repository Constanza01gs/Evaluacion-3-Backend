from django.core.management.base import BaseCommand
from gestion.services import cerrar_listas_iniciadas


class Command(BaseCommand):
    help = 'Cierra las listas de espera de las clases que ya comenzaron.'

    def handle(self, *args, **options):
        total = cerrar_listas_iniciadas()
        self.stdout.write(f'Solicitudes cerradas sin cupo: {total}')
