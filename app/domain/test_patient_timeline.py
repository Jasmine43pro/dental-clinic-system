from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Appointment, AppointmentPlan, AuditEvent, Clinic, DentistClinic, Patient, Role,
    RoleAssignment, ScheduleSlot, TreatmentType, User, Visit, VisitTreatment,
)


class PatientTimelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', no_credentials_file=True, stdout=StringIO())
        cls.owner = User.objects.get(username='demo_patient_1')
        cls.patient = Patient.objects.get(user=cls.owner)
        cls.other_patient = Patient.objects.get(user__username='demo_patient_2')
        cls.other_patient.name = cls.patient.name
        cls.other_patient.save(update_fields=['name'])
        cls.front = User.objects.get(username='demo_reception_a')
        cls.in_progress = Appointment.objects.get(number='DEMO-A004')
        cls.completed = Appointment.objects.get(number='DEMO-A005')
        for appointment, name in ((cls.in_progress, '待确认治疗草稿'), (cls.completed, '已完成治疗快照')):
            appointment.patient = cls.patient
            appointment.save(update_fields=['patient'])
            appointment.visit.treatments.update(name_snapshot=name)
        cls.upcoming = Appointment.objects.get(number='DEMO-A001')
        cls.treatment = TreatmentType.objects.get(code='DEMO-T2')
        AppointmentPlan.objects.update_or_create(appointment=cls.upcoming, defaults={
            'treatment_type': cls.treatment, 'ends_at': cls.upcoming.slot.ends_at,
            'resource_ends_at': cls.upcoming.slot.ends_at})
        VisitTreatment.objects.create(visit=cls.completed.visit, treatment_type=cls.treatment,
            name_snapshot='历史已撤销治疗', quantity=1, unit_price_snapshot=0, subtotal=0, is_active=False)

        cls.other_clinic = Clinic.objects.get(code='DEMO-02')
        cls.service = DentistClinic.objects.get(clinic=cls.other_clinic, dentist__user__username='demo_dentist_4')
        cls.outside_completed = cls.make_completed('TIMELINE-B', cls.patient, 0, 'B 店已完成治疗')
        cls.outside_only_patient = Patient.objects.create(name='仅在 B 店的同名患者', birth_date=cls.patient.birth_date,
            phone='TEST-TIMELINE-B')
        cls.outside_only = cls.make_completed('TIMELINE-B-ONLY', cls.outside_only_patient, 1, 'B 店其他患者治疗')

    @classmethod
    def make_completed(cls, number, patient, offset, name):
        start = cls.completed.slot.starts_at + timedelta(hours=1, minutes=30 * offset)
        slot = ScheduleSlot.objects.create(clinic=cls.other_clinic, dentist=cls.service.dentist, service=cls.service,
            starts_at=start, ends_at=start + timedelta(minutes=30))
        appointment = Appointment.objects.create(number=number, patient=patient, slot=slot, status='completed',
            source='reception', created_by=User.objects.get(username='demo_reception_b'), checked_in_at=start)
        visit = Visit.objects.create(appointment=appointment, dentist=slot.dentist, status='completed',
            started_at=start, completed_at=start + timedelta(minutes=25),
            clinical_notes='仅接诊医生可查看的诊疗记录')
        VisitTreatment.objects.create(visit=visit, treatment_type=cls.treatment,
            name_snapshot=name, quantity=1, unit_price_snapshot=0, subtotal=0)
        return appointment

    def test_owner_sees_plans_and_completed_treatments_without_drafts_or_same_name_history(self):
        url = reverse('patient_timeline', args=[self.patient.pk])
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('patients')), url)
        self.assertContains(self.client.get(reverse('appointment', args=[self.upcoming.pk])), url)
        response = self.client.get(url)
        for expected in ('计划 · 待到诊', self.treatment.name, '就诊进行中', '已完成治疗快照', 'B 店已完成治疗'):
            self.assertContains(response, expected)
        for private in ('DEMO-A002', '待确认治疗草稿', '历史已撤销治疗',
                        '仅接诊医生可查看的诊疗记录', 'B 店其他患者治疗'):
            self.assertNotContains(response, private)
        self.assertEqual([entry.pk for entry in response.context['items']],
            [self.upcoming.pk, self.in_progress.pk, self.outside_completed.pk, self.completed.pk])
        self.assertEqual(self.client.get(reverse('patient_timeline', args=[self.other_patient.pk])).status_code, 404)
        self.assertTrue(AuditEvent.objects.filter(actor=self.owner, object_id=self.patient.pk,
            action='view_patient_timeline').exists())
        self.assertIn('no-store', response['Cache-Control'])
        draft_url = reverse('visit', args=[self.in_progress.visit.pk])
        self.assertNotContains(self.client.get(draft_url), '待确认治疗草稿')
        self.client.force_login(self.in_progress.visit.dentist.user)
        self.assertContains(self.client.get(draft_url), '待确认治疗草稿')

    def test_front_desk_cross_clinic_permission_and_exact_phone_lookup(self):
        url = reverse('patient_timeline', args=[self.patient.pk])
        outside_url = reverse('patient_timeline', args=[self.outside_only_patient.pk])
        self.client.force_login(self.front)
        self.assertContains(self.client.get(url), '已完成治疗快照')
        self.assertNotContains(self.client.get(url), 'B 店已完成治疗')
        self.assertNotContains(self.client.get(reverse('patients'), {'phone': self.outside_only_patient.phone}),
            outside_url)
        self.assertEqual(self.client.get(outside_url).status_code, 404)
        grant = RoleAssignment.objects.create(user=self.front, role=Role.objects.get(code='reception'),
            clinic=self.other_clinic, scope_type='clinic', valid_from=timezone.now())
        expanded = self.client.get(url)
        self.assertContains(expanded, 'B 店已完成治疗')
        self.assertNotContains(expanded, 'B 店其他患者治疗')
        self.assertContains(self.client.get(outside_url), 'B 店其他患者治疗')
        grant.valid_to = timezone.now()
        grant.save(update_fields=['valid_to'])
        self.assertNotContains(self.client.get(url), 'B 店已完成治疗')
        self.assertEqual(self.client.get(outside_url).status_code, 404)
        self.client.force_login(User.objects.get(username='demo_admin'))
        self.assertEqual(self.client.get(url).status_code, 404)
