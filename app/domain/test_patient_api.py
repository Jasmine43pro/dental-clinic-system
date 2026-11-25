from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.db import connections
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from .models import (
    Appointment, AuditEvent, Clinic, ClinicHours, ClinicalResource, Dentist, DentistClinic, Patient,
    Invoice, InvoiceLine, PaymentRecord, ResourceUnavailable, Role, RoleAssignment, ScheduleSlot, TreatmentType, User, Visit, VisitTreatment,
    WorkCalendar,
)


def registration_data(**overrides):
    return {
        'username': 'mini_new_patient', 'name': '注册验收虚构患者', 'birth_date': '1995-06-01',
        'phone': 'TEST-MINI-REG', 'billing_address': '虚构地址',
        'password1': 'Independent Fixture 428!', 'password2': 'Independent Fixture 428!',
        **overrides,
    }


class PatientBookingApiTests(TestCase):
    password = 'Fixture-only Password! 218'

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        patient_role = Role.objects.create(code='patient', name='患者')
        cls.user = User.objects.create_user(username='fixture_patient', password=cls.password)
        cls.patient = Patient.objects.create(user=cls.user, name='虚构患者', birth_date=date(1990, 1, 1),
                                             billing_address='虚构地址', phone='TEST-PATIENT')
        cls.other = Patient.objects.create(name='另一虚构患者', birth_date=date(1991, 1, 1),
                                          billing_address='虚构地址', phone='TEST-PATIENT-2')
        RoleAssignment.objects.create(user=cls.user, role=patient_role, scope_type='self', valid_from=now)
        cls.staff = User.objects.create_user(username='fixture_staff', password=cls.password)
        cls.clinic = Clinic.objects.create(code='API-CLINIC', name='虚构诊所', address='虚构地址',
                                          phone='TEST-CLINIC', manager_name='虚构负责人', floor_area=100)
        doctor = Dentist.objects.create(employee_number='API-D1', name='虚构医生', birth_date=date(1980, 1, 1),
                                        home_address='虚构地址', phone='TEST-DOCTOR')
        service = DentistClinic.objects.create(dentist=doctor, clinic=cls.clinic, valid_from=now)
        cls.day = timezone.localdate() + timedelta(days=1)
        ClinicHours.objects.create(clinic=cls.clinic, weekday=cls.day.isoweekday(),
                                   opens_at=time(9), closes_at=time(17))
        ClinicalResource.objects.create(clinic=cls.clinic, code='API-C1', name='牙椅', kind='chair')
        start = timezone.make_aware(datetime.combine(cls.day, time(10)))
        cls.slot = ScheduleSlot.objects.create(service=service, dentist=doctor, clinic=cls.clinic,
                                               starts_at=start, ends_at=start + timedelta(minutes=30))

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def login(self, user=None, **options):
        return self.client.post('/api/patient/login/', {'username': (user or self.user).username,
                                'password': self.password}, content_type='application/json', **options)

    def slots(self, auth=None):
        headers = {'authorization': auth} if auth else {}
        return self.client.get('/api/patient/slots/', {'clinic': str(self.clinic.pk), 'date': self.day.isoformat()},
                               secure=True, headers=headers)

    def test_login_restricts_transport_and_patient_role(self):
        self.assertEqual(self.login().status_code, 403)
        self.assertEqual(self.login(self.staff, secure=True).status_code, 401)
        response = self.login(secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('sessionid', response.cookies)
        auth = 'Bearer ' + response.json()['data']['token']
        self.assertEqual(self.slots(auth).status_code, 200)
        self.user.set_password('Different fixture password!')
        self.user.save(update_fields=['password'])
        self.assertEqual(self.slots(auth).status_code, 401)

    def test_register_creates_one_linked_patient_and_audits_it(self):
        url = '/api/patient/register/'
        payload = registration_data()
        post = dict(content_type='application/json', secure=True)
        self.assertEqual(self.client.post(url, payload, content_type='application/json').status_code, 403)
        self.assertEqual(self.client.post(url, {**payload, 'birth_date': '2999-06-01'}, **post).status_code, 400)
        self.assertEqual(self.client.post(url, {**payload, 'password2': 'different'}, **post).status_code, 400)
        self.assertFalse(User.objects.filter(username=payload['username']).exists())

        response = self.client.post(url, {**payload, 'patient_id': str(self.other.pk), 'role': 'administrator'}, **post)
        self.assertEqual(response.status_code, 201)
        self.assertNotIn('sessionid', response.cookies)
        token = response.json()['data']['token']
        record = Patient.objects.get(user__username=payload['username'])
        self.assertNotEqual(record.pk, self.other.pk)
        self.assertIsNone(record.identity_verified_at)
        self.assertEqual(record.phone, payload['phone'])
        roles = RoleAssignment.objects.filter(user=record.user).values_list('role__code', 'scope_type')
        self.assertEqual(list(roles), [('patient', 'self')])
        self.assertTrue(AuditEvent.objects.filter(actor=record.user, object_id=record.pk, action='register').exists())
        mine = self.client.get('/api/patient/appointments/', secure=True, headers={'authorization': 'Bearer ' + token})
        self.assertEqual(mine.json()['data']['items'], [])
        login = self.client.post('/api/patient/login/', {'username': payload['username'], 'password': payload['password1']}, **post)
        self.assertEqual(login.status_code, 200)

    def test_register_refers_matching_records_to_reception_without_linking(self):
        matches = [
            registration_data(phone=self.patient.phone),
            registration_data(phone=self.other.phone),
            registration_data(name=self.other.name, birth_date=self.other.birth_date.isoformat()),
        ]
        for payload in matches:
            response = self.client.post('/api/patient/register/', payload, content_type='application/json', secure=True)
            self.assertEqual(response.status_code, 409)
            self.assertIn('前台核验', response.json()['error'])
        self.other.refresh_from_db()
        self.assertIsNone(self.other.user_id)
        self.assertFalse(User.objects.filter(username=matches[0]['username']).exists())

    def test_available_slot_booking_and_exact_replay(self):
        token = self.login(secure=True).json()['data']['token']
        auth = 'Bearer ' + token
        self.assertEqual(self.slots().status_code, 401)
        self.assertEqual(self.slots(auth + 'bad').status_code, 401)
        self.assertEqual([slot['id'] for slot in self.slots(auth).json()['data']], [str(self.slot.pk)])

        payload = {'slot': str(self.slot.pk), 'request_key': str(uuid4()), 'patient': str(self.other.pk)}
        url = '/api/patient/appointments/'
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url, payload, content_type='application/json', secure=True).status_code, 401)
        options = dict(content_type='application/json', secure=True, headers={'authorization': auth})
        rejected = self.client.post(url, payload, **options)
        self.assertEqual(rejected.status_code, 409)
        self.assertIn('过敏状态', rejected.json()['error'])
        self.assertFalse(Appointment.objects.exists())
        payload['allergy_status'] = 'no_known'
        first = self.client.post(url, payload, **options)
        again = self.client.post(url, payload, **options)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(again.json(), first.json())
        appointment = Appointment.objects.get()
        self.assertEqual(appointment.patient_id, self.patient.pk)
        event = AuditEvent.objects.get(actor=self.user, object_id=appointment.pk, action='book')
        self.assertEqual(event.after_value['allergy_status'], 'no_known')
        self.assertEqual(self.client.post(url, {**payload, 'allergy_status': 'known'}, **options).status_code, 409)
        self.assertEqual(self.slots(auth).json()['data'], [])
        self.assertEqual(self.client.post(url, {**payload, 'request_key': str(uuid4())}, **options).status_code, 409)

    def booking_options(self):
        token = self.login(secure=True).json()['data']['token']
        return dict(content_type='application/json', secure=True, headers={'authorization': 'Bearer ' + token})

    def test_holiday_hides_slots_and_rejects_direct_booking(self):
        options = self.booking_options()
        self.assertEqual(len(self.slots(options['headers']['authorization']).json()['data']), 1)
        WorkCalendar.objects.create(clinic=self.clinic, cal_date=self.day, kind=WorkCalendar.Kind.HOLIDAY)
        self.assertEqual(self.slots(options['headers']['authorization']).json()['data'], [])
        response = self.client.post('/api/patient/appointments/', {'slot': str(self.slot.pk),
            'request_key': str(uuid4()), 'allergy_status': 'no_known'}, **options)
        self.assertEqual(response.status_code, 409)
        self.assertIn('营业时间', response.json()['error'])
        self.assertFalse(Appointment.objects.exists())

    def test_batch_availability_omits_patient_conflicts_and_chair_outages(self):
        options = self.booking_options()
        self.make_booking(options)
        chair = ClinicalResource.objects.get(clinic=self.clinic)
        chair.capacity = 2
        chair.save(update_fields=['capacity'])
        dentist = Dentist.objects.create(employee_number='API-D2', name='另一虚构医生',
            birth_date=date(1982, 1, 1), home_address='虚构地址', phone='TEST-DOCTOR-2')
        service = DentistClinic.objects.create(dentist=dentist, clinic=self.clinic, valid_from=timezone.now())
        candidates = []
        for offset in (0, 30, 60):
            start = self.slot.starts_at + timedelta(minutes=offset)
            candidates.append(ScheduleSlot.objects.create(service=service, dentist=dentist, clinic=self.clinic,
                starts_at=start, ends_at=start + timedelta(minutes=30)))
        ResourceUnavailable.objects.create(resource=chair, quantity=2, reason='虚构检修',
            starts_at=candidates[1].starts_at, ends_at=candidates[1].ends_at)

        response = self.slots(options['headers']['authorization'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.json()['data']], [str(candidates[2].pk)])


    def make_booking(self, options):
        response = self.client.post('/api/patient/appointments/',
                                    {'slot': str(self.slot.pk), 'request_key': str(uuid4()), 'allergy_status': 'no_known'}, **options)
        self.assertEqual(response.status_code, 200)
        return Appointment.objects.get(patient=self.patient)

    def test_patient_list_and_cancel_keep_ownership_and_release_resources(self):
        options = self.booking_options()
        appointment = self.make_booking(options)
        foreign_slot = ScheduleSlot.objects.create(
            service=self.slot.service, dentist=self.slot.dentist, clinic=self.clinic,
            starts_at=self.slot.ends_at, ends_at=self.slot.ends_at + timedelta(minutes=30),
        )
        foreign = Appointment.objects.create(number='FOREIGN-APPOINTMENT', patient=self.other,
                                             slot=foreign_slot, source='reception', created_by=self.staff)
        data = self.client.get('/api/patient/appointments/', secure=True, headers=options['headers']).json()['data']
        self.assertEqual([row['number'] for row in data['items']], [appointment.number])
        self.assertEqual(data['items'][0]['allergy_confirmation'], '已核实无已知过敏')
        self.assertTrue(data['items'][0]['can_cancel'])
        cancellation = {'reason': '虚构取消', 'request_key': str(uuid4())}
        self.assertEqual(self.client.post(f'/api/patient/appointments/{foreign.pk}/cancel/',
                                         cancellation, **options).status_code, 404)
        own_url = f'/api/patient/appointments/{appointment.pk}/cancel/'
        self.assertEqual(self.client.post(own_url, {**cancellation, 'reason': '  '}, **options).status_code, 400)
        self.assertTrue(appointment.resource_occupancies.filter(is_active=True).exists())
        self.assertEqual(self.slots(options['headers']['authorization']).json()['data'], [])

        result = self.client.post(own_url, cancellation, **options)
        replay = self.client.post(own_url, cancellation, **options)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(replay.json(), result.json())
        self.assertEqual(result.json()['data']['status_code'], Appointment.Status.CANCELLED)
        appointment.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual(appointment.cancel_party, 'patient')
        self.assertEqual(foreign.status, Appointment.Status.BOOKED)
        self.assertFalse(appointment.resource_occupancies.filter(is_active=True).exists())
        self.assertEqual(self.slots(options['headers']['authorization']).json()['data'][0]['id'], str(self.slot.pk))
        self.assertEqual(AuditEvent.objects.filter(object_id=appointment.pk, action='cancel').count(), 1)

    def test_cancel_cutoff_applies_even_to_a_patient_who_also_has_reception_access(self):
        options = self.booking_options()
        appointment = self.make_booking(options)
        reception = Role.objects.create(code='reception', name='前台')
        RoleAssignment.objects.create(user=self.user, role=reception, clinic=self.clinic, scope_type='clinic',
                                      valid_from=timezone.now())
        url = f'/api/patient/appointments/{appointment.pk}/cancel/'
        cancellation = {'reason': '虚构取消', 'request_key': str(uuid4())}
        with patch('django.utils.timezone.now', return_value=self.slot.starts_at - timedelta(minutes=119)):
            listed = self.client.get('/api/patient/appointments/', secure=True, headers=options['headers'])
            self.assertFalse(listed.json()['data']['items'][0]['can_cancel'])
            self.assertEqual(self.client.post(url, cancellation, **options).status_code, 409)
        with patch('django.utils.timezone.now', return_value=self.slot.starts_at - timedelta(hours=2)):
            self.assertEqual(self.client.post(url, cancellation, **options).status_code, 200)

    def test_visit_and_invoice_expose_only_owned_completed_records_and_patient_fields(self):
        options = self.booking_options()
        appointment = self.make_booking(options)
        visit_url = f'/api/patient/appointments/{appointment.pk}/visit/'
        own = dict(secure=True, headers=options['headers'])
        now = timezone.now()
        visit = Visit.objects.create(appointment=appointment, dentist=self.slot.dentist, started_at=now,
                                    clinical_notes='医生草稿', special_needs_notes='内部备注')
        self.assertEqual(self.client.get(visit_url, **own).status_code, 404)
        self.assertEqual(self.client.get(visit_url, secure=True).status_code, 401)
        visit.status, visit.completed_at = 'completed', now
        visit.save(update_fields=['status', 'completed_at'])
        appointment.status, appointment.checked_in_at = Appointment.Status.COMPLETED, now
        appointment.save(update_fields=['status', 'checked_in_at'])
        project = TreatmentType.objects.create(code='API-T', name='虚构检查', unit='次', catalog_price=50)
        line = VisitTreatment.objects.create(visit=visit, treatment_type=project, name_snapshot=project.name,
                                             quantity=1, unit_price_snapshot=50, subtotal=50)
        VisitTreatment.objects.create(visit=visit, treatment_type=project, name_snapshot='已撤回项目',
                                      quantity=1, unit_price_snapshot=50, subtotal=50, is_active=False)
        invoice = Invoice.objects.create(number='API-REVIEW-INVOICE', visit=visit, source_visit_version=1,
                                         issued_by=self.staff, issued_at=now, status='paid', total=Decimal('50.00'))
        InvoiceLine.objects.create(invoice=invoice, visit_treatment=line, name_snapshot='结算时的检查名称',
                                   quantity_snapshot=1, unit_price_snapshot=50, subtotal=50)
        PaymentRecord.objects.create(invoice=invoice, method='模拟现金', status='success', amount=50,
                                     succeeded_at=now, recorded_by=self.staff, request_reference='API-PAYMENT')
        invoice_url = f'/api/patient/invoices/{invoice.pk}/'

        records = self.client.get('/api/patient/appointments/', **own).json()['data']['items']
        self.assertTrue(records[0]['has_completed_visit'])
        summary = self.client.get(visit_url, **own).json()['data']
        self.assertEqual([item['name_snapshot'] for item in summary['treatments']], ['虚构检查'])
        self.assertEqual(summary['invoices'][0]['id'], str(invoice.pk))
        self.assertNotIn('clinical_notes', summary)
        self.assertNotIn('special_needs_notes', summary)
        bill = self.client.get(invoice_url, **own).json()['data']
        self.assertEqual(bill['lines'][0]['name_snapshot'], '结算时的检查名称')
        self.assertEqual((bill['status'], bill['total'], bill['payment']['amount']), ('已支付', '50.00', '50.00'))
        self.assertNotIn('request_reference', bill['payment'])
        for url in (visit_url, invoice_url):
            self.assertEqual(self.client.post(url, {}, **options).status_code, 403)
            self.assertEqual(Client().post(url, {}, **options).status_code, 405)

        intruder = User.objects.create_user(username='other_fixture_patient', password=self.password)
        self.other.user = intruder
        self.other.save(update_fields=['user'])
        RoleAssignment.objects.create(user=intruder, role=Role.objects.get(code='patient'), scope_type='self', valid_from=now)
        reception = Role.objects.create(code='reception', name='前台')
        RoleAssignment.objects.create(user=intruder, role=reception, clinic=self.clinic, scope_type='clinic', valid_from=now)
        other_token = self.login(intruder, secure=True).json()['data']['token']
        for url in (visit_url, invoice_url):
            self.assertEqual(self.client.get(url, secure=True, headers={'authorization': 'Bearer ' + other_token}).status_code, 404)


class PatientRegistrationConcurrencyTests(TransactionTestCase):
    def test_two_registrations_using_the_same_phone_only_create_one_account(self):
        Role.objects.create(code='patient', name='患者')
        barrier = Barrier(2)

        def register(index):
            try:
                barrier.wait(timeout=15)
                payload = registration_data(username=f'new_mini_patient_{index}', name=f'虚构患者{index}')
                return Client().post('/api/patient/register/', payload, content_type='application/json', secure=True).status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(register, (1, 2)), [201, 409])
        self.assertEqual(Patient.objects.filter(phone='TEST-MINI-REG').count(), 1)
        self.assertEqual(User.objects.filter(username__startswith='new_mini_patient_').count(), 1)
