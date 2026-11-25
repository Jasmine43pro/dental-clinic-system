"""采购入库、耗材领用与成本归集的业务规则验证。"""
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import (Appointment, CostRecord, InventoryBatch, MaterialIssue, PurchaseOrder,
                     PurchaseOrderItem, Supplier, User, Visit)


class InventoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo', no_credentials_file=True, stdout=StringIO())

    def setUp(self):
        self.manager = User.objects.get(username='demo_manager_a')
        self.doctor = User.objects.get(username='demo_dentist_1')
        self.visit = Appointment.objects.get(number='DEMO-A004').visit
        self.clinic = self.visit.appointment.slot.clinic
        Visit.objects.filter(pk=self.visit.pk).update(status='in_progress', completed_at=None)
        self.visit.refresh_from_db()

    def order(self, quantity='10', price='12.50', expires=None, valid_to=None):
        supplier = Supplier.objects.create(code='SUP-' + uuid.uuid4().hex[:6],
            name='康泰医疗器械', valid_from=timezone.localdate() - timedelta(days=60),
            valid_to=valid_to)
        order = PurchaseOrder.objects.create(clinic=self.clinic, supplier=supplier,
            order_number='PO-' + uuid.uuid4().hex[:8], ordered_on=timezone.localdate(),
            total_amount=services.money(Decimal(quantity) * Decimal(price)),
            recorded_by=self.manager)
        item = PurchaseOrderItem.objects.create(purchase_order=order, item_name='一次性手套',
            unit='盒', quantity=Decimal(quantity), unit_price=Decimal(price),
            amount=services.money(Decimal(quantity) * Decimal(price)))
        return order, item

    def receive(self, order, item, quantity, expires=None):
        return services.receive_purchase(self.manager, order.pk,
            [{'item_id': item.pk, 'accepted_quantity': Decimal(quantity), 'expires_on': expires}])

    def test_receive_full_creates_batch_and_accepts(self):
        order, item = self.order()
        services.receive_purchase(self.manager, order.pk,
            [{'item_id': item.pk, 'accepted_quantity': Decimal('10')}])
        order.refresh_from_db()
        self.assertEqual(order.status, 'accepted')
        self.assertEqual(order.accepted_amount, Decimal('125.00'))
        batch = InventoryBatch.objects.get(item=item)
        self.assertEqual(batch.received_quantity, Decimal('10'))
        self.assertEqual(batch.remaining_quantity, Decimal('10'))
        self.assertEqual(batch.clinic.pk, self.clinic.pk)

    def test_receive_partial_keeps_only_accepted_amount(self):
        order, item = self.order()
        services.receive_purchase(self.manager, order.pk,
            [{'item_id': item.pk, 'accepted_quantity': Decimal('4')}])
        order.refresh_from_db()
        self.assertEqual(order.accepted_amount, Decimal('50.00'))
        self.assertEqual(order.status, 'accepted')
        self.assertEqual(InventoryBatch.objects.get(item=item).received_quantity, Decimal('4'))

    def test_receive_nothing_marks_returned(self):
        order, item = self.order()
        services.receive_purchase(self.manager, order.pk,
            [{'item_id': item.pk, 'accepted_quantity': Decimal('0')}])
        order.refresh_from_db()
        self.assertEqual(order.status, 'returned')
        self.assertEqual(order.accepted_amount, Decimal('0'))
        self.assertFalse(InventoryBatch.objects.filter(item=item).exists())

    def test_receive_rejected_for_non_manager(self):
        order, item = self.order()
        with self.assertRaises(PermissionDenied):
            services.receive_purchase(self.doctor, order.pk,
                [{'item_id': item.pk, 'accepted_quantity': Decimal('10')}])

    def test_receive_rejects_quantity_over_order(self):
        order, item = self.order()
        with self.assertRaises(ValidationError):
            services.receive_purchase(self.manager, order.pk,
                [{'item_id': item.pk, 'accepted_quantity': Decimal('11')}])

    def test_receive_rejects_expired_supplier(self):
        order, item = self.order(valid_to=timezone.localdate() - timedelta(days=1))
        with self.assertRaises(ValidationError):
            services.receive_purchase(self.manager, order.pk,
                [{'item_id': item.pk, 'accepted_quantity': Decimal('10')}])

    def test_issue_deducts_batch_and_records_cost(self):
        order, item = self.order()
        self.receive(order, item, '10')
        batch = InventoryBatch.objects.get(item=item)
        issue = services.issue_material(self.doctor, self.visit.pk, batch.pk, Decimal('3'))
        batch.refresh_from_db()
        self.assertEqual(batch.remaining_quantity, Decimal('7'))
        self.assertEqual(issue.cost_record.amount, Decimal('37.50'))
        self.assertEqual(issue.cost_record.category, 'supplies')
        self.assertEqual(issue.cost_record.visit_id, self.visit.pk)
        self.assertEqual(issue.cost_record.source_reference, issue.issue_number)

    def test_issue_rejects_more_than_remaining(self):
        order, item = self.order()
        self.receive(order, item, '10')
        batch = InventoryBatch.objects.get(item=item)
        with self.assertRaises(ValidationError):
            services.issue_material(self.doctor, self.visit.pk, batch.pk, Decimal('11'))

    def test_issue_rejects_expired_batch(self):
        order, item = self.order()
        self.receive(order, item, '10', expires=timezone.localdate() - timedelta(days=1))
        batch = InventoryBatch.objects.get(item=item)
        with self.assertRaises(ValidationError):
            services.issue_material(self.doctor, self.visit.pk, batch.pk, Decimal('1'))

    def test_issue_rejects_finished_visit(self):
        order, item = self.order()
        self.receive(order, item, '10')
        batch = InventoryBatch.objects.get(item=item)
        Visit.objects.filter(pk=self.visit.pk).update(status='completed',
            completed_at=timezone.now() + timedelta(minutes=1))
        with self.assertRaises(ValidationError):
            services.issue_material(self.doctor, self.visit.pk, batch.pk, Decimal('1'))

    def test_return_restores_batch_and_reverses_cost(self):
        order, item = self.order()
        self.receive(order, item, '10')
        batch = InventoryBatch.objects.get(item=item)
        issue = services.issue_material(self.doctor, self.visit.pk, batch.pk, Decimal('3'))
        services.return_material(self.doctor, issue.pk)
        batch.refresh_from_db()
        issue.refresh_from_db()
        issue.cost_record.refresh_from_db()
        self.assertEqual(batch.remaining_quantity, Decimal('10'))
        self.assertIsNotNone(issue.reversed_at)
        self.assertEqual(issue.cost_record.amount, Decimal('0'))
        self.assertIn('退料冲销', issue.cost_record.description)

    def test_double_return_rejected(self):
        order, item = self.order()
        self.receive(order, item, '10')
        batch = InventoryBatch.objects.get(item=item)
        issue = services.issue_material(self.doctor, self.visit.pk, batch.pk, Decimal('2'))
        services.return_material(self.doctor, issue.pk)
        with self.assertRaises(ValidationError):
            services.return_material(self.doctor, issue.pk)

    def test_web_issue_rolls_up_once_and_later_return_recalculates_original_quarter(self):
        CostRecord.objects.all().delete()
        order, item = self.order()
        self.receive(order, item, '10')
        batch = InventoryBatch.objects.get(item=item)
        CostRecord.objects.create(clinic=self.clinic, category='operating', amount=30,
            occurred_on=timezone.localdate(), source_reference='TEST-PUBLIC', recorded_by=self.manager)
        visit_url = reverse('visit', args=[self.visit.pk])
        issue_url = reverse('material_issue', args=[self.visit.pk])
        self.client.force_login(User.objects.get(username='demo_reception_a'))
        self.assertContains(self.client.get(visit_url), issue_url)
        self.assertContains(self.client.get(issue_url), batch.batch_number)
        payload = {'request_key': 'issue-once', 'batch': batch.pk, 'quantity': '3'}
        self.assertRedirects(self.client.post(issue_url, payload), visit_url)
        self.assertRedirects(self.client.post(issue_url, payload), visit_url)
        issue = MaterialIssue.objects.get()
        cost = issue.cost_record
        self.assertEqual(cost.dentist_id, self.visit.dentist_id)
        self.assertEqual(cost.clinic_id, self.clinic.pk)
        self.assertEqual(cost.occurred_on, timezone.localdate(issue.issued_at))
        self.assertEqual(CostRecord.objects.count(), 2)
        batch.refresh_from_db()
        self.assertEqual(batch.remaining_quantity, 7)

        quarter = (cost.occurred_on.month - 1) // 3 + 1
        report_url = reverse('reports')
        filters = {'year': cost.occurred_on.year, 'quarter': quarter, 'clinic': self.clinic.pk}
        for actor, expected in ((self.doctor, Decimal('37.50')), (self.manager, Decimal('67.50'))):
            self.client.force_login(actor)
            report = self.client.get(report_url, filters)
            self.assertEqual(report.context['cost'], expected)
            self.assertEqual(report.context['rows'][0]['cost'], expected)
            row = next(row for row in report.context['doctor_rows'] if row['name'] == self.visit.dentist.name)
            self.assertEqual(row['cost'], Decimal('37.50'))
            for source in (order.order_number, batch.batch_number, issue.issue_number, visit_url, '12.50'):
                self.assertContains(report, source)
            if actor == self.manager:
                self.assertEqual(report.context['public_cost'], Decimal('30.00'))

        return_on = date(cost.occurred_on.year + (quarter == 4), quarter * 3 % 12 + 1, 1)
        return_url = reverse('material_return', args=[self.visit.pk, issue.pk])
        self.client.force_login(self.doctor)
        self.assertContains(self.client.get(return_url), '冲销原领用季度')
        later = timezone.make_aware(datetime.combine(return_on, time(12)))
        with patch('domain.services.timezone') as clock:
            clock.now.return_value = later
            self.assertRedirects(self.client.post(return_url,
                {'request_key': 'return-once', 'reason': '未使用'}), visit_url)
        batch.refresh_from_db()
        self.assertEqual(batch.remaining_quantity, 10)
        self.assertEqual(CostRecord.objects.count(), 2)
        self.assertEqual(self.client.get(report_url, filters).context['cost'], 0)
        self.client.force_login(self.manager)
        original_report = self.client.get(report_url, filters)
        self.assertEqual(original_report.context['cost'], Decimal('30.00'))
        self.assertEqual(original_report.context['public_cost'], Decimal('30.00'))
        self.assertContains(original_report, '已冲销原领用季度')
        later_report = self.client.get(report_url, {**filters, 'year': return_on.year, 'quarter': quarter % 4 + 1})
        self.assertEqual(later_report.context['cost'], 0)
        self.assertNotContains(later_report, issue.issue_number)

        CostRecord.objects.bulk_create([
            CostRecord(clinic=self.clinic, category='operating', amount=0,
                occurred_on=cost.occurred_on, source_reference=f'TEST-EXTRA-{index}',
                recorded_by=self.manager)
            for index in range(50)
        ])
        self.assertNotContains(self.client.get(report_url, filters), issue.issue_number)
        older_costs = self.client.get(report_url, {**filters, 'page': 2})
        self.assertContains(older_costs, issue.issue_number)
        self.assertEqual(older_costs.context['cost'], Decimal('30.00'))

    def test_material_web_operations_respect_visit_and_clinic_scope(self):
        order, item = self.order()
        self.receive(order, item, '10')
        batch = InventoryBatch.objects.get(item=item)
        issue = services.issue_material(self.doctor, self.visit.pk, batch.pk, Decimal('1'))
        issue_url = reverse('material_issue', args=[self.visit.pk])
        return_url = reverse('material_return', args=[self.visit.pk, issue.pk])
        for username in ('demo_dentist_2', 'demo_reception_b', 'demo_manager_a'):
            actor = User.objects.get(username=username)
            self.client.force_login(actor)
            self.assertIn(self.client.post(issue_url, {'batch': batch.pk, 'quantity': '2',
                'request_key': 'denied-issue'}).status_code, (403, 404))
            self.assertIn(self.client.post(return_url, {'reason': '违规操作',
                'request_key': 'denied-return'}).status_code, (403, 404))
        other_doctor = User.objects.get(username='demo_dentist_2')
        with self.assertRaises(PermissionDenied):
            services.issue_material(other_doctor, self.visit.pk, batch.pk, Decimal('1'))
        with self.assertRaises(PermissionDenied):
            services.return_material(other_doctor, issue.pk)
        issue.refresh_from_db()
        batch.refresh_from_db()
        self.assertIsNone(issue.reversed_at)
        self.assertEqual(batch.remaining_quantity, 9)
