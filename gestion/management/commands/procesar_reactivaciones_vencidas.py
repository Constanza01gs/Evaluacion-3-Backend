from django.core.management.base import BaseCommand

from gestion.services import procesar_reactivaciones_vencidas


class Command(BaseCommand):
    help = 'Cierra solicitudes de reactivación pendientes cuya pausa ya terminó.'

    def handle(self, *args, **options):
        cantidad = procesar_reactivaciones_vencidas()
        self.stdout.write(self.style.SUCCESS(f'Solicitudes cerradas: {cantidad}'))
