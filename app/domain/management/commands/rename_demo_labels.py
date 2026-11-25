from django.core.management.base import BaseCommand
from django.db import transaction

from domain.demo_names import CLINIC_NAMES, DENTIST_NAMES
from domain.models import Clinic, Dentist


class Command(BaseCommand):
    help = 'Replace the original demo clinic and dentist names while preserving record IDs.'

    @transaction.atomic
    def handle(self, *args, **options):
        clinic_count = 0
        for number, name in enumerate(CLINIC_NAMES, start=1):
            clinic_count += Clinic.objects.filter(
                code=f'DEMO-{number:02}', name=f'虚构演示诊所{number:02}',
            ).update(name=name)

        dentist_count = 0
        for number, name in enumerate(DENTIST_NAMES, start=1):
            dentist_count += Dentist.objects.filter(
                employee_number=f'DEMO-D{number}', name=f'演示医生{number}',
            ).update(name=name)

        self.stdout.write(f'Renamed {clinic_count} demo clinics and {dentist_count} demo dentists.')
