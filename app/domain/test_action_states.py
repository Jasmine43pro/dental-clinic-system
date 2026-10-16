"""Operation availability, state transitions and direct-request protection."""
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
import re
import uuid
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from . import services
from .models import (Appointment, Invoice, Patient, PaymentRecord, ResourceUnavailable,
                     Role, RoleAssignment, ScheduleSlot, TreatmentType, User, Visit)


class StateControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', no_credentials_file=True, stdout=StringIO())
        RoleAssignment.objects.update(valid_from=timezone.now() - timedelta(days=30))
        cls.patient_user = User.objects.create_user(username='state_patient')
        cls.patient = Patient.objects.create(user=cls.patient_user, name='状态验证患者',
            birth_date=date(1995, 1, 1), phone='TEST-STATE-PATIENT', billing_address='虚构地址')
        RoleAssignment.objects.create(user=cls.patient_user, role=Role.objects.get(code='patient'),
            scope_type='self', valid_from=timezone.now() - timedelta(days=365))

    def setUp(self):
        self.front = User.objects.get(username='demo_reception_a')
        self.doctor = User.objects.get(username='demo_dentist_1')
        self.treatment = TreatmentType.objects.get(code='DEMO-T1')
        self.slot = ScheduleSlot.objects.filter(dentist__user=self.doctor,
            appointment__isnull=True, starts_at__gt=timezone.now()).order_by('starts_at').first()
        self.assertIsNotNone(self.slot)
        clock_patch = patch('django.utils.timezone.now', return_value=self.slot.starts_at - timedelta(hours=2))
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)

    def booking(self):
        return services.book(self.front, self.patient.pk, self.slot.pk, treatment_id=self.treatment.pk)

    def read(self, actor, route, *args):
        self.client.force_login(actor)
        response = self.client.get(reverse(route, args=args))
        self.assertEqual(response.status_code, 200)
        return response

    def write(self, actor, route, args, data):
        self.client.force_login(actor)
        return self.client.post(reverse(route, args=args), {**data, 'request_key': str(uuid.uuid4())})

    def operation(self, response, code, enabled, reason=None):
        html = response.content.decode()
        match = re.search(r'<div class="operation-item" data-operation="'+re.escape(code)+r'">(.*?)</div>', html, re.S)
        self.assertIsNotNone(match, code)
        block = match.group(1)
        if enabled:
            self.assertIn('<a ', block)
            self.assertNotIn('disabled', block)
        else:
            self.assertIn('<button ', block)
            self.assertIn('disabled', block)
            self.assertIn('aria-describedby=', block)
            self.assertNotIn('href=', block)
            if reason:
                self.assertIn(reason, block)

    def visit(self, completed=False):
        app = self.booking()
        with patch('django.utils.timezone.now', return_value=self.slot.starts_at - timedelta(minutes=10)):
            services.appointment_action(self.front, app.pk, 'checkin')
            visit = services.start_visit(self.doctor, app.pk)
        self.clock.return_value = self.slot.starts_at + timedelta(minutes=10)
        if completed:
            services.edit_visit(self.doctor, visit.pk, 'notes', {'version': visit.current_version,
                'clinical_notes': '虚构验证诊疗说明', 'special_needs_notes': ''})
            visit.refresh_from_db()
            services.edit_visit(self.doctor, visit.pk, 'add_treatment', {'version': visit.current_version,
                'treatment': self.treatment, 'quantity': Decimal('1')})
            visit.refresh_from_db()
            services.edit_visit(self.doctor, visit.pk, 'complete', {'version': visit.current_version})
            visit.refresh_from_db()
        return visit

    def test_patient_cutoff_and_forged_cancellation(self):
        app = self.booking()
        with patch('django.utils.timezone.now', return_value=self.slot.starts_at - timedelta(hours=1)):
            response = self.read(self.patient_user, 'appointment', app.pk)
            self.operation(response, 'cancel', False, '不足2小时')
            rejected = self.write(self.patient_user, 'appointment_command', [app.pk, 'cancel'], {'reason': '验证取消'})
        self.assertContains(rejected, '不足2小时')
        app.refresh_from_db()
        self.assertEqual(app.status, 'booked')
        self.assertTrue(app.slot_links.filter(is_active=True).exists())

    def test_role_scopes_hide_controls_and_block_direct_forms(self):
        app = self.booking()
        response = self.read(self.patient_user, 'appointment', app.pk)
        for action in ['checkin', 'no_show', 'start', 'extend', 'reschedule']:
            self.assertNotContains(response, 'data-operation="'+action+'"')
        for action in ['checkin', 'no_show', 'start', 'extend', 'undo_checkin', 'undo_no_show']:
            with self.subTest(action=action):
                self.assertEqual(self.client.get(reverse('appointment_command', args=[app.pk, action])).status_code, 403)
        self.client.force_login(User.objects.get(username='demo_reception_b'))
        self.assertEqual(self.client.get(reverse('appointment', args=[app.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('appointment_command', args=[app.pk, 'checkin'])).status_code, 403)

    def test_checkin_date_and_no_show_end_boundary(self):
        app = self.booking()
        with patch('django.utils.timezone.now', return_value=self.slot.starts_at - timedelta(days=1)):
            response = self.read(self.front, 'appointment', app.pk)
            self.operation(response, 'checkin', False, '当天')
            self.operation(response, 'no_show', False, '结束后')
        with patch('django.utils.timezone.now', return_value=self.slot.starts_at - timedelta(minutes=10)):
            response = self.read(self.front, 'appointment', app.pk)
            self.operation(response, 'checkin', True)
            self.operation(response, 'no_show', False)
        with patch('django.utils.timezone.now', return_value=app.ends_at):
            self.operation(self.read(self.front, 'appointment', app.pk), 'no_show', True)

    def test_incomplete_visit_requires_notes_and_treatment(self):
        visit = self.visit()
        response = self.read(self.doctor, 'visit', visit.pk)
        self.operation(response, 'complete', False, '诊疗说明')
        self.operation(response, 'remove_treatment', False, '没有可移除')
        rejected = self.write(self.doctor, 'visit_command', [visit.pk, 'complete'], {'version': visit.current_version})
        self.assertContains(rejected, '诊疗说明')
        visit.refresh_from_db()
        self.assertEqual(visit.status, 'in_progress')
        services.edit_visit(self.doctor, visit.pk, 'notes', {'version': visit.current_version,
            'clinical_notes': '虚构验证诊疗说明', 'special_needs_notes': ''})
        visit.refresh_from_db()
        self.operation(self.read(self.doctor, 'visit', visit.pk), 'complete', False, '至少添加一项治疗')
        services.edit_visit(self.doctor, visit.pk, 'add_treatment', {'version': visit.current_version,
            'treatment': self.treatment, 'quantity': Decimal('1')})
        self.operation(self.read(self.doctor, 'visit', visit.pk), 'complete', True)
        self.operation(self.read(self.front, 'visit', visit.pk), 'issue', False, '完成后')

    def test_pending_amendment_blocks_payment_but_allows_void(self):
        visit = self.visit(completed=True)
        invoice = services.issue_invoice(self.front, visit.pk, visit.current_version)
        services.request_amendment(self.doctor, visit.pk, {'version': visit.current_version,
            'clinical_notes': '虚构更正说明', 'reason': '验证更正'})
        response = self.read(self.front, 'invoice', invoice.pk)
        self.operation(response, 'pay', False, '审批中')
        self.operation(response, 'void', True)
        self.operation(self.read(self.front, 'visit', visit.pk), 'issue', False, '审批中')
        self.operation(self.read(self.doctor, 'visit', visit.pk), 'amend', False, '待审批')
        rejected = self.write(self.front, 'invoice_command', [invoice.pk, 'pay'],
            {'amount': str(invoice.total), 'method': '模拟现金'})
        self.assertContains(rejected, '更正处理中')
        self.assertFalse(PaymentRecord.objects.filter(invoice=invoice).exists())
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'issued')
        accepted = self.write(self.front, 'invoice_command', [invoice.pk, 'void'], {'reason': '验证作废'})
        self.assertEqual(accepted.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'void')

    def test_paid_invoice_and_completed_visit_remain_protected(self):
        visit = self.visit(completed=True)
        invoice = services.issue_invoice(self.front, visit.pk, visit.current_version)
        services.invoice_action(self.front, invoice.pk, 'pay', {'amount': invoice.total, 'method': '模拟现金'})
        response = self.read(self.front, 'invoice', invoice.pk)
        self.operation(response, 'pay', False, '已支付')
        self.operation(response, 'void', False, '已支付')
        rejected = self.write(self.front, 'invoice_command', [invoice.pk, 'void'], {'reason': '不允许的作废'})
        self.assertContains(rejected, '尚未支付')
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'paid')
        doctor_page = self.read(self.doctor, 'visit', visit.pk)
        self.operation(doctor_page, 'notes', False, '就诊已完成')
        self.operation(doctor_page, 'add_treatment', False, '就诊已完成')
        self.operation(doctor_page, 'complete', False, '就诊已完成')
        self.operation(doctor_page, 'amend', True)
        self.assertEqual(PaymentRecord.objects.filter(invoice=invoice, status='success').count(), 1)

    def test_voided_invoice_cannot_collect_and_can_be_replaced(self):
        visit = self.visit(completed=True)
        invoice = services.issue_invoice(self.front, visit.pk, visit.current_version)
        services.invoice_action(self.front, invoice.pk, 'void', {'reason': '验证作废'})
        response = self.read(self.front, 'invoice', invoice.pk)
        self.operation(response, 'pay', False, '已作废')
        self.operation(response, 'void', False, '已作废')
        rejected = self.write(self.front, 'invoice_command', [invoice.pk, 'pay'],
            {'amount': str(invoice.total), 'method': '模拟现金'})
        self.assertContains(rejected, '尚未支付')
        self.assertFalse(PaymentRecord.objects.filter(invoice=invoice).exists())
        self.operation(self.read(self.front, 'visit', visit.pk), 'issue', True)
        replacement = services.issue_invoice(self.front, visit.pk, visit.current_version)
        self.assertEqual(replacement.replaces_id, invoice.pk)

    def test_existing_invoice_disables_duplicate_issue_without_breaking_replay(self):
        visit = self.visit(completed=True)
        invoice = services.issue_invoice(self.front, visit.pk, visit.current_version)
        self.operation(self.read(self.front, 'visit', visit.pk), 'issue', False, '已有有效结算单')
        form_page = self.read(self.front, 'visit_command', visit.pk, 'issue')
        self.assertContains(form_page, 'disabled aria-describedby="operation-blocked-reason"')
        repeated = self.write(self.front, 'visit_command', [visit.pk, 'issue'], {'version': visit.current_version})
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(repeated.url, reverse('invoice', args=[invoice.pk]))
        self.assertEqual(Invoice.objects.filter(visit=visit).count(), 1)

    def test_resource_outage_explains_and_blocks_start(self):
        app = self.booking()
        with patch('django.utils.timezone.now', return_value=self.slot.starts_at - timedelta(minutes=10)):
            services.appointment_action(self.front, app.pk, 'checkin')
            occupancy = app.resource_occupancies.filter(is_active=True).select_related('resource').first()
            self.assertIsNotNone(occupancy)
            ResourceUnavailable.objects.create(resource=occupancy.resource, starts_at=occupancy.starts_at,
                ends_at=occupancy.ends_at, quantity=occupancy.resource.capacity, reason='虚构检修验证')
            self.operation(self.read(self.doctor, 'appointment', app.pk), 'start', False, '资源容量不足')
            blocked_form = self.read(self.doctor, 'appointment_command', app.pk, 'start')
            self.assertContains(blocked_form, 'disabled aria-describedby="operation-blocked-reason"')
            rejected = self.write(self.doctor, 'appointment_command', [app.pk, 'start'], {})
            self.assertContains(rejected, '资源容量不足')
        self.assertFalse(Visit.objects.filter(appointment=app).exists())

    def test_started_appointment_cannot_be_rescheduled_by_direct_request(self):
        app = self.booking()
        original_slot = app.slot_id
        target = ScheduleSlot.objects.get(dentist=self.slot.dentist, starts_at=self.slot.starts_at + timedelta(days=1))
        with patch('django.utils.timezone.now', return_value=self.slot.starts_at + timedelta(minutes=1)):
            self.operation(self.read(self.front, 'appointment', app.pk), 'reschedule', False, '尚未开始')
            blocked = self.read(self.front, 'reschedule', app.pk)
            self.assertContains(blocked, 'disabled aria-describedby="operation-blocked-reason"')
            rejected = self.write(self.front, 'reschedule', [app.pk], {'patient': str(self.patient.pk),
                'slot': str(target.pk), 'treatment': str(self.treatment.pk), 'identity_confirmed': 'on',
                'reason': '验证禁止改约', 'confirmed': 'yes'})
            self.assertContains(rejected, '仅可调整尚未开始')
        app.refresh_from_db()
        self.assertEqual(app.slot_id, original_slot)
        self.assertEqual(app.version, 1)
