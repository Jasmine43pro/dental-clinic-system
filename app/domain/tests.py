from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from django.core.management import call_command
from django.db import IntegrityError, transaction, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from .models import *

class DatabaseBaselineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo',no_credentials_file=True,stdout=StringIO())

    def rejects(self,fn):
        with self.assertRaises(IntegrityError), transaction.atomic(): fn()

    def test_seed_counts_and_repeat_no_changes(self):
        self.assertEqual((Clinic.objects.count(),Dentist.objects.count(),Patient.objects.count()),(15,6,12))
        before=(Appointment.objects.count(),User.objects.get(username='demo_patient_1').password)
        call_command('seed_demo',no_credentials_file=True,stdout=StringIO())
        self.assertEqual(before,(Appointment.objects.count(),User.objects.get(username='demo_patient_1').password))

    def test_appointment_visit_both_directions(self):
        visit=Visit.objects.first()
        self.assertEqual(visit.appointment.visit.pk,visit.pk)
        self.assertEqual(visit.appointment.slot.clinic.code,'DEMO-01')

    def test_doctor_cross_clinic_overlap_rejected(self):
        slot=ScheduleSlot.objects.filter(clinic__code='DEMO-01').first()
        rel=DentistClinic.objects.filter(dentist=slot.dentist).exclude(clinic=slot.clinic).first()
        if rel is None:
            rel=DentistClinic.objects.create(dentist=slot.dentist,clinic=Clinic.objects.get(code='DEMO-15'),valid_from=slot.starts_at-timedelta(days=1))
        self.rejects(lambda:ScheduleSlot.objects.create(service=rel,dentist=slot.dentist,clinic=rel.clinic,starts_at=slot.starts_at,ends_at=slot.ends_at))

    def test_adjacent_slot_allowed(self):
        slot=ScheduleSlot.objects.filter(appointment__number='DEMO-A003').get()
        ScheduleSlot.objects.create(service=slot.service,dentist=slot.dentist,clinic=slot.clinic,starts_at=slot.ends_at,ends_at=slot.ends_at+timedelta(minutes=30))

    def test_mismatched_service_rejected(self):
        rel=DentistClinic.objects.first()
        other=Clinic.objects.exclude(pk=rel.clinic_id).first()
        start=timezone.now()+timedelta(days=50)
        self.rejects(lambda:ScheduleSlot.objects.create(service=rel,dentist=rel.dentist,clinic=other,starts_at=start,ends_at=start+timedelta(minutes=30)))

    def test_referenced_slot_cannot_move(self):
        app=Appointment.objects.first()
        self.rejects(lambda:ScheduleSlot.objects.filter(pk=app.slot_id).update(starts_at=app.slot.starts_at+timedelta(days=10),ends_at=app.slot.ends_at+timedelta(days=10)))

    def test_duplicate_visit_rejected(self):
        v=Visit.objects.first()
        self.rejects(lambda:Visit.objects.create(appointment=v.appointment,dentist=v.dentist,started_at=timezone.now()))

    def test_second_effective_invoice_rejected(self):
        inv=Invoice.objects.filter(status='issued').first()
        self.rejects(lambda:Invoice.objects.create(number='CONFLICT',visit=inv.visit,source_visit_version=1,total=100,issued_by=inv.issued_by,issued_at=timezone.now()))

    def test_second_successful_payment_rejected(self):
        p=PaymentRecord.objects.get(status='success')
        self.rejects(lambda:PaymentRecord.objects.create(invoice=p.invoice,method='test',amount=p.amount,status='success',succeeded_at=timezone.now(),recorded_by=p.recorded_by,request_reference='DUPLICATE'))

    def test_price_snapshot_survives_catalog_change(self):
        line=InvoiceLine.objects.first()
        before=line.unit_price_snapshot
        TreatmentType.objects.filter(pk=line.visit_treatment.treatment_type_id).update(catalog_price=999)
        line.refresh_from_db()
        self.assertEqual(line.unit_price_snapshot,before)

    def test_amount_check_and_role_scope(self):
        line=VisitTreatment.objects.first()
        self.rejects(lambda:VisitTreatment.objects.filter(pk=line.pk).update(subtotal=1))
        assignment=RoleAssignment.objects.filter(scope_type='clinic').first()
        self.rejects(lambda:RoleAssignment.objects.filter(pk=assignment.pk).update(clinic=None))

    def test_audit_append_only(self):
        event=AuditEvent.objects.first()
        self.rejects(lambda:AuditEvent.objects.filter(pk=event.pk).update(reason='rewrite'))
        self.rejects(lambda:AuditEvent.objects.filter(pk=event.pk).delete())

class ConcurrentBookingTest(TransactionTestCase):
    def test_two_transactions_one_slot(self):
        call_command('seed_demo',no_credentials_file=True,stdout=StringIO())
        slot=ScheduleSlot.objects.filter(appointment__isnull=True).first()
        user=User.objects.first()
        patients=list(Patient.objects.all()[:2])
        barrier=Barrier(2)
        def book(i):
            try:
                barrier.wait(timeout=10)
                with transaction.atomic():
                    Appointment.objects.create(number=f'RACE-{i}',patient_id=patients[i].pk,
                        slot_id=slot.pk,created_by_id=user.pk,source='reception')
                return 'saved'
            except IntegrityError:
                return 'conflict'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(book,[0,1]))
        self.assertCountEqual(results,['saved','conflict'])
        self.assertEqual(Appointment.objects.filter(slot=slot,status='booked').count(),1)
