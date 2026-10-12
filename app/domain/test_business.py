from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, datetime, time
from decimal import Decimal
from io import StringIO
from threading import Barrier
import uuid
from django.core.management import call_command
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import connections
from django.test import TestCase, TransactionTestCase, Client
from django.urls import reverse
from django.utils import timezone
from . import services, access
from .models import *


class BusinessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo',no_credentials_file=True,stdout=StringIO())

    def setUp(self):
        self.front=User.objects.get(username='demo_reception_a')
        self.doctor=User.objects.get(username='demo_dentist_1')
        self.patient=Patient.objects.get(user__username='demo_patient_1')
        self.manager=User.objects.get(username='demo_manager_a')
        self.slot=ScheduleSlot.objects.filter(dentist__user=self.doctor,appointment__isnull=True).order_by('starts_at').first()

    def login(self,name):
        self.client.force_login(User.objects.get(username=name))

    def draft(self):
        app=Appointment.objects.get(number='DEMO-A004')
        return app.visit

    def test_authenticated_pages_for_all_roles(self):
        routes={
          'demo_patient_1':['/','/appointments/','/book/','/patients/'],
          'demo_reception_a':['/','/appointments/','/book/','/patients/','/schedules/'],
          'demo_dentist_1':['/','/appointments/','/schedules/','/reports/'],
          'demo_manager_a':['/','/schedules/','/amendments/','/reports/','/master/clinics/','/master/hours/'],
          'demo_headquarters':['/','/reports/','/master/dentists/','/master/services/','/master/qualifications/','/master/certificates/'],
          'demo_finance':['/','/costs/','/reports/'],
          'demo_admin':['/','/accounts/','/roles/','/audit/','/master/treatments/','/master/allergies/','/doctor-account/']}
        for username,paths in routes.items():
            self.login(username)
            for path in paths:
                with self.subTest(username=username,path=path):
                    self.assertEqual(self.client.get(path).status_code,200)

    def test_anonymous_and_csrf(self):
        self.assertEqual(self.client.get('/appointments/').status_code,302)
        self.assertEqual(self.client.get('/login/').status_code,200)
        self.assertEqual(self.client.get('/register/').status_code,200)
        client=Client(enforce_csrf_checks=True);client.force_login(self.front)
        self.assertEqual(client.post('/book/',{}).status_code,403)

    def test_patient_and_cross_clinic_isolation(self):
        app=Appointment.objects.get(number='DEMO-A004')
        for user in ['demo_patient_1','demo_reception_b','demo_admin','demo_dentist_2']:
            self.login(user)
            self.assertEqual(self.client.get(reverse('appointment',args=[app.pk])).status_code,403)
            self.assertEqual(self.client.get(reverse('visit',args=[app.visit.pk])).status_code,403)
        self.login('demo_reception_a')
        self.assertNotContains(self.client.get(reverse('visit',args=[app.visit.pk])),'虚构测试记录，不是诊疗建议')
        self.login('demo_dentist_1')
        self.assertContains(self.client.get(reverse('visit',args=[app.visit.pk])),'虚构测试记录，不是诊疗建议')

    def test_navigation_preserves_context(self):
        self.login('demo_reception_a');app=Appointment.objects.get(number='DEMO-A004')
        query='?status=arrived&page=2'
        self.assertContains(self.client.get(reverse('appointment',args=[app.pk])+query),reverse('visit',args=[app.visit.pk])+'?status=arrived&amp;page=2')
        self.assertContains(self.client.get(reverse('visit',args=[app.visit.pk])+query),reverse('appointment',args=[app.pk])+'?status=arrived&amp;page=2')

    def test_booking_replay_and_payload_mismatch(self):
        key=str(uuid.uuid4());payload={'slot':str(self.slot.pk)}
        fn=lambda:services.book(self.patient.user,self.patient.pk,self.slot.pk)
        a=services.command(self.patient.user,'book',key,payload,fn)
        self.assertEqual(a,services.command(self.patient.user,'book',key,payload,fn))
        with self.assertRaises(ValidationError):services.command(self.patient.user,'book',key,{'slot':'different'},fn)
        self.assertEqual(Appointment.objects.filter(slot=self.slot).count(),1)

    def test_patient_cross_doctor_conflict(self):
        services.book(self.patient.user,self.patient.pk,self.slot.pk)
        other=ScheduleSlot.objects.filter(starts_at=self.slot.starts_at).exclude(dentist=self.slot.dentist).first()
        with self.assertRaises(ValidationError):services.book(self.patient.user,self.patient.pk,other.pk)

    def test_invalid_service_or_hours(self):
        ClinicHours.objects.filter(clinic=self.slot.clinic).update(opens_at=time(12))
        with self.assertRaises(ValidationError):services.book(self.patient.user,self.patient.pk,self.slot.pk)

    def test_self_cancel_boundary(self):
        from unittest.mock import patch
        app=services.book(self.patient.user,self.patient.pk,self.slot.pk)
        with patch('domain.services.timezone.now',return_value=self.slot.starts_at-timedelta(hours=2)+timedelta(seconds=1)):
            with self.assertRaises(ValidationError):services.appointment_action(self.patient.user,app.pk,'cancel','计划改变')
        with patch('domain.services.timezone.now',return_value=self.slot.starts_at-timedelta(hours=2)):
            services.appointment_action(self.patient.user,app.pk,'cancel','计划改变')
        app.refresh_from_db();self.assertEqual(app.status,'cancelled')

    def test_checkin_requires_today_and_doctor_requires_arrived(self):
        app=services.book(self.patient.user,self.patient.pk,self.slot.pk)
        with self.assertRaises(ValidationError):services.appointment_action(self.front,app.pk,'checkin')
        with self.assertRaises(ValidationError):services.start_visit(self.doctor,app.pk)

    def test_complete_issue_pay_and_immutable_paid(self):
        visit=self.draft()
        visit=services.edit_visit(self.doctor,visit.pk,'notes',{'version':visit.current_version,'clinical_notes':'测试诊疗说明','special_needs_notes':''})
        visit=services.edit_visit(self.doctor,visit.pk,'add_treatment',{'version':visit.current_version,'treatment':TreatmentType.objects.get(code='DEMO-T2'),'quantity':Decimal('1.25')})
        visit=services.edit_visit(self.doctor,visit.pk,'complete',{'version':visit.current_version})
        inv=services.issue_invoice(self.front,visit.pk,visit.current_version)
        self.assertEqual(inv.total,Decimal('350.63'))
        self.assertEqual(services.issue_invoice(self.front,visit.pk,visit.current_version).pk,inv.pk)
        services.invoice_action(self.front,inv.pk,'pay',{'amount':inv.total,'method':'模拟现金'})
        with self.assertRaises(ValidationError):services.invoice_action(self.front,inv.pk,'void',{'reason':'不得作废'})
        with self.assertRaises(ValidationError):services.edit_visit(self.doctor,visit.pk,'notes',{'version':visit.current_version,'clinical_notes':'覆盖','special_needs_notes':''})

    def test_complete_requires_treatment_and_notes(self):
        visit=self.draft();visit.treatments.update(is_active=False)
        with self.assertRaises(ValidationError):services.edit_visit(self.doctor,visit.pk,'complete',{'version':visit.current_version})

    def test_stale_visit_version_rejected(self):
        visit=self.draft()
        with self.assertRaises(ValidationError):services.edit_visit(self.doctor,visit.pk,'notes',{'version':999,'clinical_notes':'test','special_needs_notes':''})

    def test_pending_amendment_blocks_issue(self):
        visit=VisitAmendment.objects.get(status='pending').visit
        with self.assertRaises(ValidationError):services.issue_invoice(self.front,visit.pk,visit.current_version)

    def test_amendment_approval_preserves_history_and_bumps_version(self):
        a=VisitAmendment.objects.get(status='pending');old=a.visit.clinical_notes
        services.review_amendment(self.manager,a.pk,True,'同意补充')
        a.refresh_from_db();a.visit.refresh_from_db()
        self.assertEqual(a.visit.current_version,2)
        self.assertEqual(a.proposed_changes['clinical_notes']['before'],old)
        self.assertTrue(AuditEvent.objects.filter(object_id=a.visit_id,action='amendment_effective').exists())

    def test_paid_charge_amendment_denied(self):
        visit=Invoice.objects.get(status='paid').visit
        with self.assertRaises(ValidationError):services.request_amendment(self.doctor,visit.pk,{'version':visit.current_version,'clinical_notes':'new','reason':'test','treatment':TreatmentType.objects.first(),'quantity':Decimal(1)})

    def test_wrong_payment_amount(self):
        inv=Invoice.objects.get(status='issued')
        with self.assertRaises(ValidationError):services.invoice_action(self.front,inv.pk,'pay',{'amount':Decimal('1'),'method':'模拟现金'})
        self.assertFalse(inv.payments.exists())

    def test_void_and_reissue_chain(self):
        inv=Invoice.objects.get(status='issued')
        services.invoice_action(self.front,inv.pk,'void',{'reason':'信息复核'})
        new=services.issue_invoice(self.front,inv.visit_id,inv.visit.current_version)
        self.assertEqual(new.replaces_id,inv.pk);self.assertNotEqual(new.number,inv.number)

    def test_report_permissions_and_zero_denominator(self):
        self.login('demo_reception_a');self.assertEqual(self.client.get('/reports/').status_code,403)
        self.login('demo_headquarters');response=self.client.get('/reports/?year=2000&quarter=1')
        self.assertContains(response,'暂无数据');self.assertContains(response,'全连锁季度激励基金')
        self.login('demo_dentist_1');self.assertNotContains(self.client.get('/reports/'),'全连锁季度激励基金')

    def test_web_booking_confirmation_and_persist(self):
        self.login('demo_patient_1')
        payload={'patient':str(self.patient.pk),'slot':str(self.slot.pk),'reason':'演示预约','request_key':str(uuid.uuid4())}
        self.assertContains(self.client.post('/book/',payload),'确认提交预约')
        self.assertFalse(Appointment.objects.filter(slot=self.slot).exists())
        payload['confirmed']='yes'
        response=self.client.post('/book/',payload)
        self.assertEqual(response.status_code,302)
        self.assertTrue(Appointment.objects.filter(slot=self.slot).exists())

    def test_register_patient_transaction(self):
        response=self.client.post('/register/',{'account-username':'new_patient','account-password1':'ExampleStrongPassword!452','account-password2':'ExampleStrongPassword!452',
            'patient-name':'虚构新患者','patient-birth_date':'1990-01-01','patient-phone':'13900000001','patient-billing_address':'虚构地址','patient-email':''})
        self.assertEqual(response.status_code,302)
        self.assertTrue(Patient.objects.filter(user__username='new_patient').exists())
        self.assertTrue(access.has_role(User.objects.get(username='new_patient'),'patient'))

    def test_expired_grant_removed(self):
        assignment=RoleAssignment.objects.get(user=self.front)
        assignment.valid_from=timezone.now()-timedelta(days=2);assignment.valid_to=timezone.now()-timedelta(days=1);assignment.save()
        self.assertFalse(access.appointments(self.front).exists())


class BusinessConcurrencyTests(TransactionTestCase):
    def setUp(self):
        call_command('seed_demo',no_credentials_file=True,stdout=StringIO())

    def race(self, functions):
        barrier=Barrier(2)
        def run(fn):
            try:
                barrier.wait(timeout=15);fn();return 'saved'
            except ValidationError:return 'conflict'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(run,functions))

    def test_concurrent_patient_booking_different_doctors(self):
        p=Patient.objects.get(user__username='demo_patient_1')
        first=ScheduleSlot.objects.filter(appointment__isnull=True).order_by('starts_at').first()
        second=ScheduleSlot.objects.filter(starts_at=first.starts_at).exclude(dentist=first.dentist).first()
        results=self.race([lambda:services.book(p.user,p.pk,first.pk),lambda:services.book(p.user,p.pk,second.pk)])
        self.assertCountEqual(results,['saved','conflict'])

    def test_payment_vs_void(self):
        user=User.objects.get(username='demo_reception_a');inv=Invoice.objects.get(status='issued')
        results=self.race([lambda:services.invoice_action(user,inv.pk,'pay',{'amount':inv.total,'method':'模拟现金'}),
            lambda:services.invoice_action(user,inv.pk,'void',{'reason':'并发测试'})])
        self.assertCountEqual(results,['saved','conflict'])
        inv.refresh_from_db();self.assertEqual(inv.payments.filter(status='success').count(),1 if inv.status=='paid' else 0)

    def test_two_reviewers_one_amendment(self):
        a=VisitAmendment.objects.get(status='pending');user=User.objects.get(username='demo_manager_a')
        results=self.race([lambda:services.review_amendment(user,a.pk,True,'批准'),lambda:services.review_amendment(user,a.pk,False,'驳回')])
        self.assertCountEqual(results,['saved','conflict'])
