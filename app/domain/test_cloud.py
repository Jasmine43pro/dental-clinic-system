import os
from unittest.mock import patch
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from .models import Clinic, User

class CloudDemoTests(TestCase):
    def test_initialization_requires_password_and_leaves_empty_database(self):
        with patch.dict(os.environ, {'DEMO_INITIAL_PASSWORD': ''}):
            with self.assertRaises(CommandError):
                call_command('seed_demo_cloud', verbosity=0)
        self.assertEqual(Clinic.objects.count(), 0)
        self.assertEqual(User.objects.count(), 0)

    def test_repeat_initialization_preserves_changed_password_and_records(self):
        initial = 'CourseDemo-Random-Only!48276'
        changed = 'PatientChanged-Random!73529'
        with patch.dict(os.environ, {'DEMO_INITIAL_PASSWORD': initial}):
            call_command('seed_demo_cloud', verbosity=0)
        user = User.objects.get(username='demo_patient_1')
        self.assertTrue(user.check_password(initial))
        user.set_password(changed)
        user.save(update_fields=['password'])
        clinic_count = Clinic.objects.count()
        with patch.dict(os.environ, {'DEMO_INITIAL_PASSWORD': 'Another-Initial!96371'}):
            call_command('seed_demo_cloud', verbosity=0)
        user.refresh_from_db()
        self.assertTrue(user.check_password(changed))
        self.assertEqual(Clinic.objects.count(), clinic_count)
