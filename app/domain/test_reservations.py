from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.management import call_command
from django.db import connections, IntegrityError, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from . import services, reservations, identity
from .models import *


class ReservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_demo',no_credentials_file=True,stdout=StringIO())

    def setUp(self):
        self.patient=Patient.objects.get(user__username='demo_patient_1')
        self.front=User.objects.get(username='demo_reception_a')
        self.manager=User.objects.get(username='demo_manager_a')
        self.slot=ScheduleSlot.objects.filter(dentist__user__username='demo_dentist_1',appointment__isnull=True).order_by('starts_at').first()
        self.chair=ClinicalResource.objects.get(clinic=self.slot.clinic,kind='chair')
        self.treatment=TreatmentType.objects.get(code='DEMO-T1')
        self.profile=TreatmentBookingProfile.objects.get(treatment_type=self.treatment)

    def configure(self,minutes=60,turnover=10):
        self.profile.expected_minutes=minutes
        self.profile.turnover_minutes=turnover
        self.profile.save()
        self.chair.capacity=1
        self.chair.save()

    def book(self,slot=None,patient=None):
        patient=patient or self.patient
        return services.book(patient.user or self.front,patient.pk,(slot or self.slot).pk,treatment_id=self.treatment.pk, allergy_status='no_known')

    def test_contiguous_units_and_cleanup_snapshot(self):
        self.configure()
        app=self.book()
        self.assertEqual(app.slot_links.filter(is_active=True).count(),2)
        self.assertEqual(app.ends_at,self.slot.starts_at+timedelta(minutes=60))
        self.assertEqual(app.plan.resource_ends_at,self.slot.starts_at+timedelta(minutes=70))
        self.profile.expected_minutes=30;self.profile.save()
        app.refresh_from_db()
        self.assertEqual(app.duration_minutes,60)

    def test_gap_and_missing_tail_are_rejected(self):
        self.configure()
        ScheduleSlot.objects.filter(dentist=self.slot.dentist,starts_at=self.slot.ends_at).update(active=False)
        with self.assertRaises(ValidationError): self.book()
        self.assertFalse(Appointment.objects.filter(patient=self.patient,slot=self.slot).exists())

    def test_second_unit_cannot_be_booked_by_another_patient(self):
        self.configure(turnover=0)
        app=self.book()
        second=app.slot_links.order_by('slot__starts_at')[1].slot
        other=Patient.objects.get(user__username='demo_patient_2')
        with self.assertRaises(ValidationError): services.book(other.user,other.pk,second.pk, allergy_status='no_known')

    def test_patient_conflict_in_second_unit_across_doctors(self):
        self.configure(turnover=0)
        self.book()
        other=ScheduleSlot.objects.get(dentist__user__username='demo_dentist_2',starts_at=self.slot.ends_at)
        with self.assertRaises(ValidationError): services.book(self.patient.user,self.patient.pk,other.pk, allergy_status='no_known')

    def test_cleanup_blocks_next_resource_use_but_adjacency_is_allowed(self):
        self.configure(minutes=30,turnover=10)
        self.book()
        other=Patient.objects.get(user__username='demo_patient_2')
        adjacent=ScheduleSlot.objects.get(dentist__user__username='demo_dentist_2',starts_at=self.slot.ends_at)
        with self.assertRaises(ValidationError): services.book(other.user,other.pk,adjacent.pk, allergy_status='no_known')
        self.profile.turnover_minutes=0;self.profile.save()
        app=Appointment.objects.get(patient=self.patient,slot=self.slot)
        services.appointment_action(self.patient.user,app.pk,'cancel','测试重新安排')
        self.book()
        services.book(other.user,other.pk,adjacent.pk, allergy_status='no_known')

    def test_interval_peak_does_not_sum_adjacent_occupancies(self):
        a=self.slot.starts_at
        rows=[(a,a+timedelta(minutes=30),1),(a+timedelta(minutes=30),a+timedelta(minutes=60),1)]
        self.assertEqual(reservations.capacity_peak(rows,a,a+timedelta(minutes=60)),1)
        self.assertEqual(reservations.capacity_peak(rows+[(a+timedelta(minutes=15),a+timedelta(minutes=45),2)],a,a+timedelta(minutes=60)),3)

    def test_partial_outage_and_equipment_requirement(self):
        equipment=ClinicalResource.objects.get(clinic=self.slot.clinic,kind='equipment')
        TreatmentResourceRequirement.objects.create(treatment_type=self.treatment,resource=equipment,quantity=1)
        ResourceUnavailable.objects.create(resource=equipment,starts_at=self.slot.starts_at,ends_at=self.slot.ends_at,quantity=1,reason='检修')
        with self.assertRaises(ValidationError): self.book()
        equipment.capacity=2;equipment.save()
        self.book()

    def test_failed_reschedule_keeps_original_units_and_resources(self):
        self.configure(turnover=0)
        app=self.book()
        before=list(app.slot_links.filter(is_active=True).values_list('slot_id',flat=True))
        target=ScheduleSlot.objects.get(dentist=self.slot.dentist,starts_at=self.slot.starts_at+timedelta(days=1))
        ResourceUnavailable.objects.create(resource=self.chair,starts_at=target.starts_at,ends_at=target.ends_at+timedelta(hours=1),quantity=1,reason='停诊')
        with self.assertRaises(ValidationError): services.book(self.front,self.patient.pk,target.pk,'调整',app.pk,self.treatment.pk, allergy_status='no_known')
        app.refresh_from_db()
        self.assertEqual(app.slot_id,self.slot.pk)
        self.assertCountEqual(app.slot_links.filter(is_active=True).values_list('slot_id',flat=True),before)
        self.assertEqual(app.resource_occupancies.filter(is_active=True).count(),1)

    def test_successful_reschedule_and_cancel_preserve_history(self):
        self.configure(turnover=0)
        app=self.book()
        target=ScheduleSlot.objects.get(dentist=self.slot.dentist,starts_at=self.slot.starts_at+timedelta(days=1))
        services.book(self.front,self.patient.pk,target.pk,'调整',app.pk,self.treatment.pk, allergy_status='no_known')
        self.assertEqual(app.slot_links.filter(is_active=False).count(),2)
        self.assertEqual(app.slot_links.filter(is_active=True).count(),2)
        services.appointment_action(self.front,app.pk,'cancel','患者确认')
        self.assertFalse(app.slot_links.filter(is_active=True).exists())
        self.assertFalse(app.resource_occupancies.filter(is_active=True).exists())
        self.assertEqual(app.slot_links.count(),4)

    def test_extension_failure_and_success(self):
        self.configure(minutes=30,turnover=0)
        app=self.book()
        next_slot=ScheduleSlot.objects.get(dentist=self.slot.dentist,starts_at=self.slot.ends_at)
        other=Patient.objects.get(user__username='demo_patient_2')
        blocked=services.book(other.user,other.pk,next_slot.pk, allergy_status='no_known')
        with self.assertRaises(ValidationError): services.extend_appointment(self.front,app.pk,60,'治疗延长')
        app.refresh_from_db();self.assertEqual(app.duration_minutes,30)
        services.appointment_action(self.front,blocked.pk,'cancel','协调改期')
        services.extend_appointment(self.front,app.pk,60,'治疗延长')
        app.refresh_from_db();self.assertEqual(app.duration_minutes,60)

    def test_existing_outage_lists_affected_and_blocks_start(self):
        app=self.book()
        outage=ResourceUnavailable.objects.create(resource=self.chair,starts_at=self.slot.starts_at,ends_at=self.slot.ends_at,quantity=3,reason='临时停诊')
        self.assertIn(app,reservations.affected_by_outage(outage))
        Appointment.objects.filter(pk=app.pk).update(status='arrived')
        doctor=self.slot.dentist.user
        with self.assertRaises(ValidationError): services.start_visit(doctor,app.pk, allergy_status='no_known')

    def test_undo_checkin_requires_no_visit_and_keeps_audit(self):
        app=self.book()
        Appointment.objects.filter(pk=app.pk).update(status='arrived',checked_in_at=timezone.now())
        services.appointment_action(self.front,app.pk,'undo_checkin','核对本人后确认误签到')
        app.refresh_from_db();self.assertEqual(app.status,'booked');self.assertIsNone(app.checked_in_at)
        self.assertTrue(AuditEvent.objects.filter(object_id=app.pk,action='undo_checkin').exists())
        Appointment.objects.filter(pk=app.pk).update(status='arrived')
        services.start_visit(self.slot.dentist.user,app.pk, allergy_status='known')
        with self.assertRaises(ValidationError): services.appointment_action(self.front,app.pk,'undo_checkin','误签到')

    def test_duplicate_phone_is_hint_and_change_does_not_merge(self):
        second=Patient.objects.get(user__username='demo_patient_2')
        old_user=second.user_id
        Patient.objects.filter(pk=second.pk).update(phone=self.patient.phone)
        self.assertEqual(Patient.objects.filter(phone=self.patient.phone).count(),2)
        identity.change_phone(self.front,Patient.objects.get(appointment__number='DEMO-A004').pk,self.patient.phone,'本人核对联系方式')
        second.refresh_from_db();self.assertEqual(second.user_id,old_user)
        with self.assertRaises(PermissionDenied): identity.change_phone(self.patient.user,self.patient.pk,'13900000001','本人提出')

    def test_identity_binding_creates_account_without_moving_history(self):
        patient=Patient.objects.get(appointment__number='DEMO-A004')
        self.assertIsNone(patient.user_id)
        identity.verify(self.front,patient.pk,'本人在前台核对姓名与出生日期','verified_patient','ExampleStrongPassword!875')
        patient.refresh_from_db();self.assertIsNotNone(patient.identity_verified_at)
        self.assertEqual(patient.user.username,'verified_patient')
        self.assertTrue(patient.appointment_set.filter(number='DEMO-A004').exists())
        with self.assertRaises(ValidationError): identity.verify(self.front,patient.pk,'再次核对','demo_patient_1')

    def test_management_pages_and_scope(self):
        for username,paths in {'demo_manager_a':['/resources/','/master/resources/','/master/outages/','/master/requirements/'],
            'demo_admin':['/master/booking-profiles/'],'demo_reception_a':['/resources/']}.items():
            self.client.force_login(User.objects.get(username=username))
            for path in paths:self.assertEqual(self.client.get(path).status_code,200)
        self.client.force_login(User.objects.get(username='demo_reception_b'))
        patient=Patient.objects.get(appointment__number='DEMO-A004')
        self.assertEqual(self.client.get(reverse('patient_identity',args=[patient.pk,'verify'])).status_code,404)

    def test_database_rejects_duplicate_slot_link(self):
        app=self.book()
        with self.assertRaises(IntegrityError),transaction.atomic():
            AppointmentSlot.objects.create(appointment=app,slot=app.slot,plan_version=99)


    def test_patient_edit_rejects_stale_revision_and_invalidates_identity(self):
        patient=Patient.objects.get(appointment__number='DEMO-A004')
        identity.verify(self.front,patient.pk,'本人前台核验')
        patient.refresh_from_db()
        self.client.force_login(self.front)
        data={'name':'修正姓名','birth_date':patient.birth_date.isoformat(),'phone':patient.phone,
            'billing_address':patient.billing_address,'email':'','revision':'stale','request_key':'stale-patient'}
        response=self.client.post(reverse('patient_edit',args=[patient.pk]),data)
        self.assertContains(response,'档案已更新')
        patient.refresh_from_db();self.assertIsNotNone(patient.identity_verified_at)
        data.update(revision=patient.updated_at.isoformat(),request_key='updated-patient')
        self.assertEqual(self.client.post(reverse('patient_edit',args=[patient.pk]),data).status_code,302)
        patient.refresh_from_db();self.assertIsNone(patient.identity_verified_at)

    def test_outage_create_lists_impact_and_rejects_foreign_resource(self):
        app=self.book()
        self.client.force_login(self.manager)
        data={'resource':str(self.chair.pk),'starts_at':self.slot.starts_at.isoformat(),'ends_at':self.slot.ends_at.isoformat(),
            'quantity':1,'reason':'检修核对','active':'on','request_key':'outage-new'}
        response=self.client.post('/master/outages/',data)
        self.assertEqual(response.status_code,302)
        self.assertContains(self.client.get('/resources/'),app.number)
        foreign=ClinicalResource.objects.filter(clinic__code='DEMO-02').first()
        data.update(resource=str(foreign.pk),request_key='outage-foreign')
        self.assertEqual(self.client.post('/master/outages/',data).status_code,200)
        self.assertFalse(ResourceUnavailable.objects.filter(resource=foreign).exists())

    def test_closure_blocks_new_bookings_and_preserves_existing_appointment(self):
        self.configure(minutes=30, turnover=0)
        app = self.book()
        day = timezone.localdate(self.slot.starts_at)
        self.client.force_login(self.manager)
        data = {'clinic': self.slot.clinic_id, 'cal_date': day.isoformat(), 'kind': 'closed',
                'note': '虚构停诊核对', 'request_key': 'calendar-closure'}
        self.assertEqual(self.client.post('/master/calendar/', data).status_code, 302)
        self.assertContains(self.client.get('/resources/'), app.number)
        other_front = User.objects.get(username='demo_reception_b')
        self.client.force_login(other_front)
        self.assertNotContains(self.client.get('/resources/'), app.number)
        self.assertEqual(self.client.post('/master/calendar/', data).status_code, 403)
        next_slot = ScheduleSlot.objects.get(dentist=self.slot.dentist, starts_at=self.slot.ends_at)
        with self.assertRaisesMessage(ValidationError, '营业时间'):
            self.book(next_slot)
        app.refresh_from_db()
        self.assertEqual(app.status, Appointment.Status.BOOKED)
        self.assertEqual(app.slot_id, self.slot.pk)
        self.assertTrue(app.slot_links.filter(is_active=True).exists())

        entry = WorkCalendar.objects.get(clinic=self.slot.clinic, cal_date=day)
        self.client.force_login(self.manager)
        restored = {**data, 'kind': 'working', 'revision': entry.updated_at.isoformat(), 'request_key': 'calendar-restore'}
        self.assertEqual(self.client.post(reverse('master_edit', args=['calendar', entry.pk]), restored).status_code, 302)
        self.assertNotContains(self.client.get('/resources/'), app.number)
        self.assertEqual(reservations.preview(next_slot.pk)['minutes'], 30)

    def test_makeup_opening_controls_schedule_and_treatment_turnover(self):
        self.configure(minutes=30, turnover=10)
        start = (timezone.localtime(self.slot.starts_at) + timedelta(weeks=3)).replace(hour=9, minute=30)
        ClinicHours.objects.filter(clinic=self.slot.clinic, weekday=start.isoweekday()).delete()
        with self.assertRaisesMessage(ValidationError, '营业时间'):
            services.schedule(self.manager, self.slot.service, start)
        self.client.force_login(self.manager)
        data = {'clinic': self.slot.clinic_id, 'cal_date': start.date().isoformat(), 'kind': 'makeup',
                'opens_at': '09:00', 'request_key': 'calendar-makeup'}
        self.assertContains(self.client.post('/master/calendar/', data), '须填写有效营业区间')
        self.assertFalse(WorkCalendar.objects.exists())
        self.assertEqual(self.client.post('/master/calendar/', {**data, 'closes_at': '10:00'}).status_code, 302)
        slot = services.schedule(self.manager, self.slot.service, start)
        with self.assertRaisesMessage(ValidationError, '营业时间'):
            services.schedule(self.manager, self.slot.service, start + timedelta(minutes=30))
        with self.assertRaisesMessage(ValidationError, '清洁周转'):
            self.book(slot)
        app = self.book(services.schedule(self.manager, self.slot.service, start - timedelta(minutes=30)))
        self.assertEqual(app.status, Appointment.Status.BOOKED)
        entry = WorkCalendar.objects.get(clinic=self.slot.clinic, cal_date=start.date())
        revised = {**data, 'closes_at': '09:35', 'revision': entry.updated_at.isoformat(), 'request_key': 'calendar-shorten'}
        self.assertEqual(self.client.post(reverse('master_edit', args=['calendar', entry.pk]), revised).status_code, 302)
        self.assertContains(self.client.get('/resources/'), app.number)

    def test_web_multislot_booking_confirmation_and_identity_required(self):
        import uuid
        self.configure()
        self.client.force_login(self.front)
        query='?phone='+self.patient.phone+'&treatment='+str(self.treatment.pk)
        data={'patient':str(self.patient.pk),'slot':str(self.slot.pk),'treatment':str(self.treatment.pk),
            'reason':'预约洁牙','request_key':str(uuid.uuid4())}
        response=self.client.post('/book/'+query,data)
        self.assertFalse(Appointment.objects.filter(patient=self.patient,slot=self.slot).exists())
        data['identity_confirmed']='on'
        rejected = self.client.post('/book/'+query, {**data, 'confirmed': 'yes'})
        self.assertContains(rejected, '请先核实并选择患者的过敏状态')
        self.assertFalse(Appointment.objects.filter(patient=self.patient,slot=self.slot).exists())
        data['allergy_status'] = 'no_known'
        response=self.client.post('/book/'+query,data)
        self.assertContains(response,'预计60分钟')
        self.assertFalse(Appointment.objects.filter(patient=self.patient,slot=self.slot).exists())
        data['confirmed']='yes'
        response=self.client.post('/book/'+query,data)
        self.assertEqual(response.status_code,302)
        app=Appointment.objects.get(patient=self.patient,slot=self.slot)
        self.assertEqual(app.slot_links.filter(is_active=True).count(),2)
        replay=self.client.post('/book/'+query,data)
        self.assertEqual(replay.status_code,302)
        self.assertEqual(Appointment.objects.filter(patient=self.patient,slot=self.slot).count(),1)

    def test_capacity_reduction_keeps_existing_booking(self):
        self.book()
        self.client.force_login(self.manager)
        self.chair.refresh_from_db()
        data={'clinic':str(self.chair.clinic_id),'code':self.chair.code,'name':self.chair.name,'kind':'chair',
            'capacity':1,'active':'','revision':self.chair.updated_at.isoformat(),'request_key':'resource-disable'}
        response=self.client.post(reverse('master_edit',args=['resources',self.chair.pk]),data)
        self.assertContains(response,'变更会使既有占用超出容量')
        self.chair.refresh_from_db();self.assertTrue(self.chair.active)

    def test_review_demo_is_repeatable_and_preserves_existing_values(self):
        password=self.front.password
        self.chair.capacity=7;self.chair.save()
        call_command('prepare_review_demo',stdout=StringIO())
        count=ScheduleSlot.objects.count()
        call_command('prepare_review_demo',stdout=StringIO())
        self.assertEqual(ScheduleSlot.objects.count(),count)
        self.chair.refresh_from_db();self.front.refresh_from_db()
        self.assertEqual(self.chair.capacity,7);self.assertEqual(self.front.password,password)
        self.assertEqual(TreatmentBookingProfile.objects.get(treatment_type__code='DEMO-LONG').expected_minutes,60)

    def test_expired_unconfirmed_is_pending_review_not_automatic_no_show(self):
        app=Appointment.objects.get(number='DEMO-A003')
        Appointment.objects.filter(pk=app.pk).update(status='booked')
        self.client.force_login(self.front)
        response=self.client.get('/appointments/?status=pending_review')
        self.assertContains(response,app.number)
        self.assertContains(response,'到期待核对')
        app.refresh_from_db();self.assertEqual(app.status,'booked')

    def test_same_phone_registration_creates_separate_pending_record(self):
        data={'account-username':'new_same_phone','account-password1':'ExampleStrongPassword!452','account-password2':'ExampleStrongPassword!452',
            'patient-name':'另一个虚构患者','patient-birth_date':'1990-01-01','patient-phone':'13900000001','patient-billing_address':'虚构地址','patient-email':''}
        Patient.objects.filter(pk=self.patient.pk).update(phone='13900000001')
        response=self.client.post('/register/',data)
        self.assertEqual(response.status_code,302)
        self.assertEqual(Patient.objects.filter(phone='13900000001').count(),2)
        self.assertIsNone(Patient.objects.get(user__username='new_same_phone').identity_verified_at)


    def test_daily_schedule_reuses_booking_rules_and_keeps_appointments_during_closure(self):
        self.configure(minutes=60, turnover=0)
        appointment = self.book()
        day = timezone.localdate(self.slot.starts_at)
        filters = {'day': day.isoformat(), 'clinic': self.slot.clinic_id, 'dentist': self.slot.dentist_id}
        self.client.force_login(self.front)
        url = reverse('day_schedule')
        dashboard = self.client.get(reverse('dashboard'))
        self.assertContains(dashboard, f'href="{url}"')
        response = self.client.get(url, filters)
        rows = response.context['rows']
        occupied = [row for row in rows if row.get('appointment') == appointment]
        self.assertEqual([row['status'] for row in occupied], ['已约', '已约'])
        self.assertContains(response, reverse('appointment', args=[appointment.pk]))
        available = [row for row in rows if row.get('booking_query')]
        self.assertTrue(available)
        open_slot = available[0]['slot']
        booking = self.client.get(f"{reverse('booking')}?{available[0]['booking_query']}")
        self.assertEqual(booking.context['form']['slot'].value(), str(open_slot.pk))

        outage = ResourceUnavailable.objects.create(resource=self.chair, starts_at=open_slot.starts_at,
            ends_at=open_slot.ends_at, quantity=1, reason='牙椅检修')
        outage_url = f'{reverse("resources")}?{response.context["resource_query"]}'
        blocked = self.client.get(url, filters)
        blocked_row = next(row for row in blocked.context['rows'] if row['slot'] == open_slot)
        self.assertEqual(blocked_row['status'], '不可约')
        self.assertNotIn('booking_query', blocked_row)
        self.assertContains(blocked, f'#outage-{outage.pk}')
        self.assertContains(self.client.get(outage_url), f'id="outage-{outage.pk}"')

        WorkCalendar.objects.create(clinic=self.slot.clinic, cal_date=day, kind=WorkCalendar.Kind.CLOSED,
            note='临时停诊演示')
        closed = self.client.get(url, filters)
        self.assertContains(closed, '临时停诊')
        self.assertContains(closed, appointment.number)
        self.assertFalse(any(row['status'] == '可约' for row in closed.context['rows']))
        self.assertContains(self.client.get(outage_url), appointment.number)

        arrived = Appointment.objects.get(number='DEMO-A004')
        arrived_day = timezone.localdate(arrived.slot.starts_at)
        arrived_response = self.client.get(url, {**filters, 'day': arrived_day.isoformat()})
        self.assertContains(arrived_response, '已到诊')
        self.assertContains(arrived_response, arrived.number)

    def test_daily_schedule_scopes_clinics_doctors_and_resources(self):
        outside = Clinic.objects.get(code='DEMO-02')
        outside_slot = ScheduleSlot.objects.filter(clinic=outside,
            dentist__user__username='demo_dentist_4').order_by('starts_at').first()
        outside_appointment = self.book(outside_slot)
        day = timezone.localdate(outside_slot.starts_at).isoformat()
        outside_resource = ClinicalResource.objects.get(clinic=outside, kind='chair')
        ResourceUnavailable.objects.create(resource=outside_resource, starts_at=outside_slot.starts_at,
            ends_at=outside_slot.ends_at, quantity=1, reason='二店专用检修')
        url = reverse('day_schedule')
        self.client.force_login(self.front)
        own_board = self.client.get(url, {'day': day})
        self.assertNotContains(own_board, outside_appointment.number)
        self.assertNotContains(own_board, '二店专用检修')
        self.assertNotIn(outside_slot.dentist, own_board.context['dentists'])
        for filters in ({'clinic': outside.pk}, {'dentist': outside_slot.dentist_id}):
            self.assertEqual(self.client.get(url, {'day': day, **filters}).status_code, 403)
        self.assertEqual(self.client.get(reverse('resources'), {'day': day, 'clinic': outside.pk}).status_code, 403)
        self.assertEqual(self.client.get(url, {'day': '2026-02-30'}).status_code, 400)
        self.client.force_login(User.objects.get(username='demo_headquarters'))
        network = self.client.get(url, {'day': day, 'clinic': outside.pk, 'dentist': outside_slot.dentist_id})
        self.assertContains(network, outside_appointment.number)
        self.assertContains(network, '二店专用检修')
        self.assertTrue(all(row['slot'].dentist_id == outside_slot.dentist_id for row in network.context['rows']))
        self.assertNotContains(network, '办理预约')
        self.client.force_login(self.patient.user)
        self.assertEqual(self.client.get(url).status_code, 403)


class CapacityConcurrencyTests(TransactionTestCase):
    def setUp(self):
        call_command('seed_demo',no_credentials_file=True,stdout=StringIO())

    def test_two_doctors_compete_for_one_chair(self):
        chair=ClinicalResource.objects.get(clinic__code='DEMO-01',kind='chair')
        chair.capacity=1;chair.save()
        slots=[ScheduleSlot.objects.filter(dentist__user__username='demo_dentist_'+str(i),appointment__isnull=True).order_by('starts_at').first().pk for i in [1,2]]
        patients=[Patient.objects.get(user__username='demo_patient_'+str(i)).pk for i in [1,2]]
        barrier=Barrier(2)
        def worker(i):
            connections.close_all()
            try:
                patient=Patient.objects.get(pk=patients[i]);barrier.wait(timeout=10)
                try: services.book(patient.user,patient.pk,slots[i], allergy_status='no_known');return 'booked'
                except ValidationError:return 'rejected'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,[0,1]))
        self.assertCountEqual(results,['booked','rejected'])
