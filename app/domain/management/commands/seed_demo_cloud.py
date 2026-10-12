"""Create fictional cloud demo accounts once; never reset existing passwords."""
import os
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from domain.models import Clinic, User

class Command(BaseCommand):
    help = 'Initialize a clean demo database using DEMO_INITIAL_PASSWORD.'

    @transaction.atomic
    def handle(self, *args, **options):
        if Clinic.objects.filter(code='DEMO-01').exists():
            self.stdout.write('Demo dataset exists; no passwords or records changed.')
            return
        password = os.environ.get('DEMO_INITIAL_PASSWORD', '')
        if not password:
            raise CommandError('Set DEMO_INITIAL_PASSWORD before the first demo initialization.')
        validate_password(password)
        call_command('seed_demo', no_credentials_file=True)
        for user in User.objects.filter(username__startswith='demo_'):
            user.set_password(password)
            user.save(update_fields=['password'])
        self.stdout.write('Fictional demo initialized. Passwords were not printed or exported.')
