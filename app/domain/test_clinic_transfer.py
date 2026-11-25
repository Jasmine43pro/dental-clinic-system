from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from threading import Barrier
from unittest.mock import patch
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from . import services
from .clinic_transfer import TransferRequest
from .models import (
    Appointment, AuditEvent, Clinic, ClinicTransferTime, ClinicalResource, DentistClinic,
    DentistQualification, InventoryBatch, Patient, PurchaseOrder, PurchaseOrderItem, ResourceOccupancy,
    ResourceUnavailable, Role, RoleAssignment, ScheduleSlot, Supplier, TreatmentBookingProfile,
    TreatmentQualificationRequirement, TreatmentResourceRequirement, TreatmentType, User, WorkCalendar,
)


class TransferFixtures:
    def configure(self):
        self.a = Clinic.objects.get(code='DEMO-01')
        self.b = Clinic.objects.get(code='DEMO-02')
        self.front = User.objects.get(username='demo_reception_a')
        self.other_front = User.objects.get(username='demo_reception_b')
        self.patient = Patient.objects.get(user__username='demo_patient_1')
        self.other_patient = Patient.objects.get(user__username='demo_patient_2')
        self.day = timezone.localdate() + timedelta(weeks=3)
        self.treatment = TreatmentType.objects.get(code='DEMO-T1')
        TreatmentBookingProfile.objects.filter(treatment_type=self.treatment).update(
            expected_minutes=30, turnover_minutes=10)
        self.source = self.make_slot('demo_dentist_1', self.a, 9)
        self.make_slot('demo_dentist_1', self.a, 10)
        self.bad = self.make_slot('demo_dentist_1', self.b, 10, 30)
        self.good = self.make_slot('demo_dentist_1', self.b, 11)
        self.original = self.book(self.patient, self.source)
        self.route = ClinicTransferTime.objects.create(origin=self.a, destination=self.b, minutes=30)
        self.certificate = DentistQualification.objects.filter(dentist=self.source.dentist).order_by('pk').first()
        self.requirement = TreatmentQualificationRequirement.objects.create(treatment_type=self.treatment,
            qualification=self.certificate.qualification)
        self.origin_chair = ClinicalResource.objects.get(clinic=self.a, kind='chair')
        self.target_chair = ClinicalResource.objects.get(clinic=self.b, kind='chair')

    def make_slot(self, username, clinic, hour, minute=0):
        service = DentistClinic.objects.get(dentist__user__username=username, clinic=clinic)
        start = timezone.localtime(timezone.now()).replace(year=self.day.year, month=self.day.month,
            day=self.day.day, hour=hour, minute=minute, second=0, microsecond=0)
        return ScheduleSlot.objects.create(clinic=clinic, dentist=service.dentist, service=service,
            starts_at=start, ends_at=start + timedelta(minutes=30))

    def book(self, patient, slot):
        return services.book(patient.user, patient.pk, slot.pk, treatment_id=self.treatment.pk,
            allergy_status='no_known')

    def grant(self, user, clinic):
        RoleAssignment.objects.create(user=user, role=Role.objects.get(code='reception'),
            clinic=clinic, scope_type='clinic', valid_from=timezone.now())

    def outage(self):
        return ResourceUnavailable.objects.create(resource=self.origin_chair, starts_at=self.source.starts_at,
            ends_at=self.source.ends_at, reason='虚构牙椅检修', quantity=1)

    def transfer(self, appointment, slot, actor=None, consent=True, version=None):
        request = TransferRequest(appointment.version if version is None else version,
            self.bad.starts_at, self.good.ends_at + timedelta(minutes=30), consent)
        return services.book(actor or self.front, appointment.patient_id, slot.pk, '停诊协调',
            appointment.pk, allergy_status='no_known', transfer=request)


class CrossClinicTests(TransferFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', no_credentials_file=True, stdout=StringIO())

    def setUp(self):
        self.configure()

    def search(self, slot=None, **changes):
        query = {'day': self.day.isoformat(), 'earliest': '10:00', 'latest': '12:00', **changes}
        if slot:
            query['slot'] = str(slot.pk)
        return f"{reverse('cross_clinic_reschedule', args=[self.original.pk])}?{urlencode(query)}"

    def test_coordination_search_refusal_confirmation_and_replay(self):
        self.grant(self.front, self.b)
        self.outage()
        self.client.force_login(self.front)
        link = reverse('cross_clinic_reschedule', args=[self.original.pk])
        self.assertContains(self.client.get(reverse('resources')), link)
        response = self.client.get(self.search(self.good))
        self.assertContains(response, '相邻排班间隔不足')
        self.assertContains(response, '核对并确认跨店改约')
        self.assertEqual(response.context['selected']['slot'], self.good)
        self.assertIsNone(self.client.get(self.search(earliest='15:00', latest='16:00')).context['selected'])
        self.assertContains(self.client.get(self.search(earliest='15:00', latest='16:00')), '没有其他授权门店的排班')

        payload = {'slot': self.good.pk, 'version': 1, 'identity_confirmed': 'on',
            'reason': '虚构牙椅检修，患者同意到另一门店', 'allergy_status': 'no_known', 'request_key': 'cross-once'}
        self.assertContains(self.client.post(self.search(self.good), payload), '患者未同意时，须保留原预约')
        self.original.refresh_from_db()
        self.assertEqual(self.original.slot_id, self.source.pk)
        self.assertTrue(self.original.slot_links.filter(slot=self.source, is_active=True).exists())
        self.assertTrue(self.original.resource_occupancies.filter(resource=self.origin_chair, is_active=True).exists())

        payload['consent'] = 'on'
        expected = reverse('appointment', args=[self.original.pk])
        self.assertRedirects(self.client.post(self.search(self.good), payload), expected)
        self.assertRedirects(self.client.post(self.search(self.good), payload), expected)
        self.original.refresh_from_db()
        self.assertEqual(self.original.slot_id, self.good.pk)
        self.assertEqual(self.original.version, 2)
        self.assertEqual(self.original.plan.treatment_type_id, self.treatment.pk)
        self.assertEqual(self.original.plan.turnover_minutes, 10)
        self.assertFalse(self.original.slot_links.filter(slot=self.source, is_active=True).exists())
        self.assertFalse(self.original.resource_occupancies.filter(resource=self.origin_chair, is_active=True).exists())
        self.assertTrue(self.original.resource_occupancies.filter(resource=self.target_chair, is_active=True).exists())
        event = AuditEvent.objects.get(object_id=self.original.pk, action='reschedule')
        self.assertEqual(event.actor_id, self.front.pk)
        self.assertEqual(event.reason, payload['reason'])
        self.assertEqual(event.before_value['clinic'], str(self.a.pk))
        self.assertEqual(event.after_value['clinic'], str(self.b.pk))
        self.assertIs(event.after_value['patient_consent'], True)

    def test_target_clinic_visit_and_reports_follow_the_transfer(self):
        self.grant(self.front, self.b)
        self.outage()
        appointment = self.transfer(self.original, self.good)
        doctor = self.good.dentist.user
        supplier = Supplier.objects.create(code='TRANSFER-SUP', name='虚构供应商', valid_from=self.day)
        order = PurchaseOrder.objects.create(clinic=self.b, supplier=supplier, order_number='TRANSFER-PO',
            ordered_on=self.day, status='accepted', total_amount=25, accepted_amount=25, recorded_by=self.front)
        item = PurchaseOrderItem.objects.create(purchase_order=order, item_name='虚构医用耗材',
            unit='盒', quantity=2, unit_price=Decimal('12.50'), amount=25)
        batch = InventoryBatch.objects.create(item=item, batch_number='TRANSFER-BATCH', received_on=self.day,
            received_quantity=2, remaining_quantity=2)
        with patch('domain.services.timezone', wraps=timezone) as clock:
            clock.now.return_value = self.good.starts_at
            clock.localdate.side_effect = lambda value=None: timezone.localdate(value or self.good.starts_at)
            services.appointment_action(self.front, appointment.pk, 'checkin')
            visit = services.start_visit(doctor, appointment.pk, allergy_status='known')
            issue = services.issue_material(doctor, visit.pk, batch.pk, Decimal('1'))
            visit = services.edit_visit(doctor, visit.pk, 'notes', {'version': visit.current_version,
                'clinical_notes': '课程演示就诊记录', 'special_needs_notes': ''})
            visit = services.edit_visit(doctor, visit.pk, 'add_treatment',
                {'version': visit.current_version, 'treatment': self.treatment, 'quantity': Decimal('1')})
            visit = services.edit_visit(doctor, visit.pk, 'complete', {'version': visit.current_version})
            invoice = services.issue_invoice(self.front, visit.pk, visit.current_version)
            services.invoice_action(self.front, invoice.pk, 'pay',
                {'method': '模拟现金', 'amount': invoice.total})

        self.client.force_login(User.objects.get(username='demo_finance'))
        response = self.client.get(reverse('reports'), {'clinic': self.b.pk, 'year': self.day.year,
            'quarter': (self.day.month - 1) // 3 + 1})
        self.assertContains(response, invoice.number)
        self.assertContains(response, issue.issue_number)
        self.assertEqual(response.context['revenue'], Decimal('100.00'))
        self.assertEqual(response.context['cost'], Decimal('12.50'))
        self.assertEqual(issue.cost_record.clinic_id, self.b.pk)
        self.assertEqual(issue.cost_record.dentist_id, doctor.dentist.pk)

    def test_both_clinics_and_no_generic_cross_clinic_bypass(self):
        self.outage()
        self.client.force_login(self.front)
        self.assertContains(self.client.get(self.search(self.good)), '没有其他门店的前台权限')
        with self.assertRaises(PermissionDenied):
            self.transfer(self.original, self.good)
        self.client.force_login(self.other_front)
        self.assertEqual(self.client.get(self.search(self.good)).status_code, 403)
        self.grant(self.front, self.b)
        with self.assertRaisesMessage(ValidationError, '跨店改约须先确认患者同意'):
            services.book(self.front, self.patient.pk, self.good.pk, '旧入口', self.original.pk,
                allergy_status='no_known')
        with self.assertRaisesMessage(ValidationError, '原预约已变更'):
            self.transfer(self.original, self.good, version=0)
        self.original.refresh_from_db()
        self.assertEqual(self.original.slot_id, self.source.pk)

    def test_config_eligibility_and_failed_recheck_preserve_original(self):
        self.grant(self.front, self.b)
        WorkCalendar.objects.create(clinic=self.a, cal_date=self.day, kind=WorkCalendar.Kind.CLOSED)
        self.client.force_login(self.front)
        self.assertContains(self.client.get(reverse('resources')), '查找跨店备选')
        self.route.active = False
        self.route.save()
        self.assertContains(self.client.get(self.search(self.good)), '未配置对应方向的转场时间')
        self.route.active = True
        self.route.save()
        self.requirement.active = False
        self.requirement.save()
        self.assertContains(self.client.get(self.search(self.good)), '未配置跨店接诊资质要求')
        self.requirement.active = True
        self.requirement.save()
        self.certificate.valid_to = self.day - timedelta(days=1)
        self.certificate.save()
        self.assertContains(self.client.get(self.search(self.good)), '缺少该项目所需的有效资质')
        self.certificate.valid_to = self.day
        self.certificate.save()
        self.assertContains(self.client.get(self.search(self.good, latest='11:20')), '超出患者确认的可就诊时间')
        origin_equipment = ClinicalResource.objects.create(clinic=self.a, code='A-EQ', name='虚构诊疗设备',
            kind='equipment')
        ResourceOccupancy.objects.create(appointment=self.original, resource=origin_equipment,
            plan_version=self.original.version, starts_at=self.source.starts_at,
            ends_at=self.original.plan.resource_ends_at, quantity=1)
        self.assertContains(self.client.get(self.search(self.good)), '未配置足以覆盖原安排的同类治疗资源')
        target_equipment = ClinicalResource.objects.create(clinic=self.b, code='B-EQ', name='目标诊疗设备',
            kind='equipment')
        TreatmentResourceRequirement.objects.create(treatment_type=self.treatment, resource=target_equipment, quantity=1)
        target = self.client.get(self.search(self.good))
        self.assertContains(target, '核对并确认跨店改约')
        ResourceUnavailable.objects.create(resource=target_equipment, quantity=1,
            starts_at=self.good.ends_at, ends_at=self.good.ends_at + timedelta(minutes=10),
            reason='周转期占用')
        payload = {'slot': self.good.pk, 'version': 1, 'identity_confirmed': 'on', 'consent': 'on',
            'reason': '停诊协调', 'allergy_status': 'no_known', 'request_key': 'blocked'}
        self.assertContains(self.client.post(self.search(self.good), payload), '资源容量不足')
        self.original.refresh_from_db()
        self.assertEqual(self.original.slot_id, self.source.pk)
        self.assertTrue(self.original.slot_links.filter(slot=self.source, is_active=True).exists())
        self.assertTrue(self.original.resource_occupancies.filter(resource=self.origin_chair, is_active=True).exists())
        self.assertFalse(AuditEvent.objects.filter(object_id=self.original.pk, action='reschedule').exists())

    def test_headquarters_can_maintain_configuration(self):
        route_url = reverse('master', args=['transfer-times'])
        qualification_url = reverse('master', args=['treatment-qualifications'])
        self.client.force_login(self.front)
        self.assertEqual(self.client.get(route_url).status_code, 403)
        self.assertEqual(self.client.get(qualification_url).status_code, 403)
        self.client.force_login(User.objects.get(username='demo_headquarters'))
        self.assertRedirects(self.client.post(route_url, {'origin': self.b.pk, 'destination': self.a.pk,
            'minutes': 45, 'active': 'on', 'request_key': 'reverse-route'}), route_url)
        self.assertEqual(ClinicTransferTime.objects.get(origin=self.b, destination=self.a).minutes, 45)
        self.assertRedirects(self.client.post(qualification_url, {'treatment_type': '',
            'qualification': self.certificate.qualification_id, 'active': 'on',
            'request_key': 'consultation-credential'}), qualification_url)
        self.assertTrue(TreatmentQualificationRequirement.objects.filter(treatment_type=None, active=True).exists())


class CrossClinicConcurrencyTests(TransferFixtures, TransactionTestCase):
    def test_two_operators_contend_for_target_and_loser_keeps_source(self):
        call_command('seed_demo', no_credentials_file=True, stdout=StringIO())
        self.configure()
        self.grant(self.front, self.b)
        self.grant(self.other_front, self.a)
        second_source = self.make_slot('demo_dentist_2', self.a, 9, 30)
        second = self.book(self.other_patient, second_source)
        WorkCalendar.objects.create(clinic=self.a, cal_date=self.day, kind=WorkCalendar.Kind.CLOSED)
        barrier = Barrier(2)

        def move(appointment, actor):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                try:
                    self.transfer(appointment, self.good, actor=actor)
                except ValidationError:
                    return None
                return appointment.pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_result = pool.submit(move, self.original, self.front)
            second_result = pool.submit(move, second, self.other_front)
            winners = [pk for pk in (first_result.result(), second_result.result()) if pk]
        self.assertEqual(len(winners), 1)
        for appointment, source in ((self.original, self.source), (second, second_source)):
            appointment.refresh_from_db()
            won = appointment.pk == winners[0]
            self.assertEqual(appointment.status, Appointment.Status.BOOKED)
            self.assertEqual(appointment.slot_id, self.good.pk if won else source.pk)
            self.assertEqual(appointment.slot_links.filter(slot=source, is_active=True).exists(), not won)
            self.assertTrue(appointment.resource_occupancies.filter(
                resource=self.target_chair if won else self.origin_chair, is_active=True).exists())
