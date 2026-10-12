"""Acceptance scenarios derived from the six original case information needs."""
from contextlib import nullcontext
from datetime import date,datetime,timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo
import uuid
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *

ZONE=ZoneInfo('Asia/Shanghai')


def stamp(day,hour=8,minute=0):
    return datetime.combine(day,datetime.min.time(),ZONE)+timedelta(hours=hour,minutes=minute)


class CoreWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roles={code:Role.objects.create(code=code,name=name) for code,name in [('patient','患者'),('reception','前台'),('dentist','医生'),('manager','诊所管理者'),('headquarters','总部'),('finance','财务'),('administrator','系统管理员')]}
        cls.hq=User.objects.create_user(username='accept_headquarters')
        cls.admin=User.objects.create_user(username='accept_admin')
        cls.finance=User.objects.create_user(username='accept_finance')
        cls.front=User.objects.create_user(username='accept_front_a')
        cls.front_b=User.objects.create_user(username='accept_front_b')
        cls.manager=User.objects.create_user(username='accept_manager_a')
        cls.manager_b=User.objects.create_user(username='accept_manager_b')
        for user,role in [(cls.hq,'headquarters'),(cls.admin,'administrator'),(cls.finance,'finance')]:
            RoleAssignment.objects.create(user=user,role=cls.roles[role],scope_type='global',valid_from=stamp(date(2025,1,1)))

    def write(self,user,url,payload,at=None):
        self.client.force_login(user)
        with patch('django.utils.timezone.now',return_value=at) if at is not None else nullcontext():
            response=self.client.post(url,{**payload,'request_key':str(uuid.uuid4())})
        errors=response.context['form'].errors.as_json() if response.context and 'form' in response.context else response.content.decode()[:150]
        self.assertEqual(response.status_code,302,(url,errors))
        return response

    def arrange(self,day=date(2026,10,12),minutes=60):
        """Create actual master records using the same web commands as staff."""
        self.day=day
        for code in ['A','B']:
            self.write(self.hq,'/master/clinics/',{'code':'ACCEPT-'+code,'name':'验收虚构诊所'+code,
                'address':'虚构地址'+code,'manager_name':'虚构负责人'+code,'phone':'TEST-CLINIC-'+code,
                'email':'','floor_area':'100','active':'on'})
        self.clinic=Clinic.objects.get(code='ACCEPT-A');self.clinic_b=Clinic.objects.get(code='ACCEPT-B')
        for user,role,clinic in [(self.front,'reception',self.clinic),(self.front_b,'reception',self.clinic_b),
            (self.manager,'manager',self.clinic),(self.manager_b,'manager',self.clinic_b)]:
            self.write(self.admin,'/roles/',{'user':str(user.pk),'role':str(self.roles[role].pk),'scope_type':'clinic','clinic':str(clinic.pk)})
        for clinic in [self.clinic,self.clinic_b]:
            for weekday in range(1,8):
                self.write(self.hq,'/master/hours/',{'clinic':str(clinic.pk),'weekday':weekday,'opens_at':'09:00','closes_at':'17:00'})
        self.write(self.hq,'/master/dentists/',{'employee_number':'ACCEPT-D1','name':'验收虚构医生','birth_date':'1980-01-01',
            'home_address':'虚构联系地址','phone':'TEST-DOCTOR','email':'','active':'on'})
        self.doctor=Dentist.objects.get(employee_number='ACCEPT-D1')
        self.write(self.admin,'/doctor-account/',{'dentist':str(self.doctor.pk),'username':'accept_doctor',
            'password1':'AcceptancePassword!2026','password2':'AcceptancePassword!2026'})
        self.doctor.refresh_from_db()
        for code,name in [('ACCEPT-Q1','验收资质一'),('ACCEPT-Q2','验收资质二')]:
            self.write(self.hq,'/master/qualifications/',{'code':code,'name':name})
            q=Qualification.objects.get(code=code)
            self.write(self.hq,'/master/certificates/',{'dentist':str(self.doctor.pk),'qualification':str(q.pk),
                'certificate_number':code+'-CERT','valid_from':'2025-01-01','valid_to':'2027-01-01','active':'on'})
        self.assertEqual(self.doctor.dentistqualification_set.count(),2)
        self.services=[]
        for clinic in [self.clinic,self.clinic_b]:
            self.write(self.hq,'/master/services/',{'dentist':str(self.doctor.pk),'clinic':str(clinic.pk),
                'valid_from':'2025-01-01T09:00','valid_to':'','active':'on'})
            self.services.append(DentistClinic.objects.get(dentist=self.doctor,clinic=clinic))
        self.assertEqual(self.doctor.dentistclinic_set.count(),2)
        for manager,clinic,code in [(self.manager,self.clinic,'A'),(self.manager_b,self.clinic_b,'B')]:
            self.write(manager,'/master/resources/',{'clinic':str(clinic.pk),'code':'CHAIR-'+code,
                'name':'验收虚构牙椅'+code,'kind':'chair','capacity':1,'active':'on'})
        for code,name,price in [('ACCEPT-T1','验收洁牙','100.00'),('ACCEPT-T2','验收抛光','200.50')]:
            self.write(self.admin,'/master/treatments/',{'code':code,'name':name,'unit':'单位','catalog_price':price,'active':'on'})
        self.treatment=TreatmentType.objects.get(code='ACCEPT-T1');self.extra=TreatmentType.objects.get(code='ACCEPT-T2')
        self.write(self.admin,'/master/booking-profiles/',{'treatment_type':str(self.treatment.pk),'expected_minutes':minutes,'turnover_minutes':10})
        for minute in [0,30]:
            self.write(self.manager,'/schedules/',{'service':str(self.services[0].pk),'starts_at':stamp(day,11,minute).isoformat()})
        self.slot=ScheduleSlot.objects.get(dentist=self.doctor,clinic=self.clinic,starts_at=stamp(day,11))
        self.write(self.front,'/patients/new/',{'name':'验收虚构患者','birth_date':'1995-01-01','phone':'TEST-ACCEPT-PATIENT',
            'billing_address':'虚构账单地址','email':''})
        self.patient=Patient.objects.get(phone='TEST-ACCEPT-PATIENT')
        self.write(self.front,reverse('patient_related',args=[self.patient.pk,'contact']),
            {'name':'验收虚构家属','relationship':'家属','phone':'TEST-CONTACT'})
        for code in ['LATEX','METAL']:
            self.write(self.admin,'/master/allergies/',{'code':code,'name':'验收过敏-'+code})
            self.write(self.front,reverse('patient_related',args=[self.patient.pk,'allergy']),
                {'allergy':str(Allergy.objects.get(code=code).pk),'manifestation':'虚构测试表现','notes':'验收记录'})
        self.assertEqual(self.patient.emergencycontact_set.count(),1)
        self.assertEqual(self.patient.patientallergy_set.count(),2)

    def reserve(self):
        self.client.force_login(self.front)
        url='/book/'
        payload={'patient':str(self.patient.pk),'slot':str(self.slot.pk),'treatment':str(self.treatment.pk),
            'reason':'验收虚构预约','identity_confirmed':'on','request_key':str(uuid.uuid4())}
        before_count=Appointment.objects.filter(patient=self.patient).count()
        preview=self.client.post(url,payload)
        self.assertEqual(preview.status_code,200)
        self.assertContains(preview,'确认提交预约')
        self.assertEqual(Appointment.objects.filter(patient=self.patient).count(),before_count)
        payload['confirmed']='yes'
        response=self.client.post(url,payload)
        self.assertEqual(response.status_code,302,response.content.decode()[:3000])
        app=Appointment.objects.get(patient=self.patient,slot=self.slot)
        self.assertEqual(app.reserved_unit_count,2)
        return app

    def complete(self,app,pay=True):
        self.write(self.front,reverse('appointment_command',args=[app.pk,'checkin']),{},at=stamp(self.day,11))
        self.write(self.doctor.user,reverse('appointment_command',args=[app.pk,'start']),{},at=stamp(self.day,11,1))
        visit=Visit.objects.get(appointment=app)
        self.client.force_login(self.doctor.user)
        summary=self.client.get(reverse('visit',args=[visit.pk]))
        self.assertContains(summary,'验收过敏-LATEX');self.assertContains(summary,'验收过敏-METAL')
        self.assertContains(summary,'验收虚构家属')
        self.write(self.doctor.user,reverse('visit_command',args=[visit.pk,'notes']),{'version':visit.current_version,
            'clinical_notes':'验收虚构诊疗记录','special_needs_notes':'验收虚构特殊需求'},at=stamp(self.day,11,10))
        visit.refresh_from_db()
        self.write(self.doctor.user,reverse('visit_command',args=[visit.pk,'add_treatment']),
            {'version':visit.current_version,'treatment':str(self.treatment.pk),'quantity':'1'},at=stamp(self.day,11,20))
        visit.refresh_from_db()
        self.write(self.doctor.user,reverse('visit_command',args=[visit.pk,'add_treatment']),
            {'version':visit.current_version,'treatment':str(self.extra.pk),'quantity':'1.25'},at=stamp(self.day,11,30))
        visit.refresh_from_db()
        self.write(self.doctor.user,reverse('visit_command',args=[visit.pk,'complete']),{'version':visit.current_version},at=stamp(self.day,11,59))
        visit.refresh_from_db();app.refresh_from_db()
        self.assertEqual(app.status,'completed');self.assertEqual(visit.status,'completed')
        self.write(self.front,reverse('visit_command',args=[visit.pk,'issue']),{'version':visit.current_version},at=stamp(self.day,12))
        inv=Invoice.objects.get(visit=visit)
        self.assertEqual(inv.total,Decimal('350.63'))
        self.assertEqual(inv.lines.count(),2)
        if pay:
            self.write(self.finance,reverse('invoice_command',args=[inv.pk,'pay']),{'method':'模拟现金','amount':'350.63'},at=stamp(self.day,12,5))
            inv.refresh_from_db();self.assertEqual(inv.status,'paid')
            self.assertEqual(inv.payments.get(status='success').amount,Decimal('350.63'))
        return visit,inv

    def cost(self,clinic,category,amount,reference,dentist=None):
        self.write(self.finance,'/costs/',{'clinic':str(clinic.pk),'dentist':str(dentist.pk) if dentist else '',
            'category':category,'amount':amount,'occurred_on':self.day.isoformat(),'source_reference':reference,'description':'验收虚构成本'})

    def test_front_created_patient_is_immediately_available_for_booking(self):
        with patch('django.utils.timezone.now',return_value=stamp(date(2026,10,12))):
            self.arrange()
            self.client.force_login(self.front)
            self.assertContains(self.client.get('/book/'),'验收虚构患者')
            self.reserve()

    def test_six_modules_end_to_end_with_exact_quarter_totals(self):
        day=date(2026,10,12)
        with patch('django.utils.timezone.now',return_value=stamp(day)):
            self.arrange();app=self.reserve()
        with patch('django.utils.timezone.now',return_value=stamp(day,11)):
            visit,inv=self.complete(app)
            self.cost(self.clinic,'supplies','60.00','ACCEPT-SUPPLIES',self.doctor)
            self.cost(self.clinic,'operating','25.00','ACCEPT-OPERATING',self.doctor)
            self.cost(self.clinic,'operating','15.00','ACCEPT-PUBLIC')
        with patch('django.utils.timezone.now',return_value=stamp(day,12,30)):
            self.client.force_login(self.hq)
            response=self.client.get('/reports/?year=2026&quarter=4')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.context['revenue'],Decimal('350.63'))
            self.assertEqual(response.context['cost'],Decimal('100.00'))
            self.assertEqual(response.context['net'],Decimal('250.63'))
            self.assertEqual(response.context['fund'],Decimal('25.06'))
            self.assertEqual(response.context['public_cost'],Decimal('15.00'))
            doctor_row=next(r for r in response.context['doctor_rows'] if r['name']==self.doctor.name)
            self.assertEqual(doctor_row['income'],Decimal('350.63'))
            self.assertEqual(doctor_row['supplies'],Decimal('60.00'))
            self.assertEqual(doctor_row['operating'],Decimal('25.00'))
            self.assertEqual(doctor_row['cost'],Decimal('85.00'))
            self.assertEqual(doctor_row['used'],2)
            self.assertEqual(doctor_row['utilization'],100.0)
            self.assertEqual(response.context['denominator'],1)
            self.client.force_login(self.manager_b)
            other=self.client.get('/reports/?year=2026&quarter=4')
            self.assertEqual(other.context['revenue'],0);self.assertEqual(other.context['cost'],0)
            self.client.force_login(self.front_b)
            self.assertEqual(self.client.get(reverse('visit',args=[visit.pk])).status_code,403)
            self.assertEqual(self.client.get(reverse('invoice',args=[inv.pk])).status_code,403)
            self.client.force_login(self.front)
            self.assertNotContains(self.client.get(reverse('visit',args=[visit.pk])),'验收虚构诊疗记录')

    def test_multislot_utilization_and_not_yet_due_review(self):
        day=date(2026,10,12)
        with patch('django.utils.timezone.now',return_value=stamp(day)):
            self.arrange();app=self.reserve()
        with patch('django.utils.timezone.now',return_value=stamp(day,11,15)):
            self.client.force_login(self.hq)
            report=self.client.get('/reports/?year=2026&quarter=4')
            self.assertEqual(report.context['denominator'],0)
            self.assertEqual(report.context['unchecked'],0)
            self.client.force_login(self.front)
            self.assertEqual(self.client.get('/appointments/?status=pending_review').context['items'].paginator.count,0)
        with patch('django.utils.timezone.now',return_value=stamp(day,11)):
            self.complete(app)
        with patch('django.utils.timezone.now',return_value=stamp(day,12,30)):
            self.client.force_login(self.hq)
            report=self.client.get('/reports/?year=2026&quarter=4')
            row=next(r for r in report.context['doctor_rows'] if r['name']==self.doctor.name)
            self.assertEqual((row['used'],row['slots'],row['utilization']),(2,2,100.0))

    def test_same_patient_and_doctor_across_clinics_and_account_binding(self):
        day=date(2026,10,12)
        with patch('django.utils.timezone.now',return_value=stamp(day)):
            self.arrange();first=self.reserve()
            self.write(self.front,reverse('patient_identity',args=[self.patient.pk,'verify']),
                {'confirmed':'on','evidence':'本人核验（虚构验收场景）','username':'accept_patient',
                 'password1':'QuarterLedger!5829a','password2':'QuarterLedger!5829a'})
            self.patient.refresh_from_db()
            self.write(self.manager_b,'/schedules/',{'service':str(self.services[1].pk),'starts_at':stamp(day+timedelta(days=1),11).isoformat()})
            second_slot=ScheduleSlot.objects.get(clinic=self.clinic_b,starts_at=stamp(day+timedelta(days=1),11))
            self.write(self.patient.user,'/book/',{'patient':str(self.patient.pk),'slot':str(second_slot.pk),'reason':'跨店验收',
                'treatment':'','confirmed':'yes'})
            second=Appointment.objects.get(slot=second_slot)
            self.assertEqual(first.patient_id,second.patient_id)
            self.assertEqual(first.slot.dentist_id,second.slot.dentist_id)
            self.client.force_login(self.patient.user)
            page=self.client.get('/appointments/')
            self.assertContains(page,first.number);self.assertContains(page,second.number)
            self.client.force_login(self.front_b)
            lookup=self.client.get('/patients/?phone='+self.patient.phone)
            self.assertContains(lookup,self.patient.name)
            self.assertNotContains(lookup,'验收过敏-LATEX')
            self.assertNotContains(lookup,'虚构账单地址')

    def test_quarter_boundaries_do_not_mix_receipts_and_costs(self):
        first_day=date(2026,9,30)
        with patch('django.utils.timezone.now',return_value=stamp(first_day)):
            self.arrange(day=first_day);first=self.reserve()
            self.complete(first);self.cost(self.clinic,'supplies','20.00','SEPTEMBER',self.doctor)
        self.day=date(2026,10,1)
        with patch('django.utils.timezone.now',return_value=stamp(self.day)):
            for minute in [0,30]:
                self.write(self.manager,'/schedules/',{'service':str(self.services[0].pk),'starts_at':stamp(self.day,11,minute).isoformat()})
            self.slot=ScheduleSlot.objects.get(clinic=self.clinic,starts_at=stamp(self.day,11))
            second=self.reserve();self.complete(second)
            self.cost(self.clinic,'operating','30.00','OCTOBER',self.doctor)
        with patch('django.utils.timezone.now',return_value=stamp(self.day,13)):
            self.client.force_login(self.hq)
            q3=self.client.get('/reports/?year=2026&quarter=3');q4=self.client.get('/reports/?year=2026&quarter=4')
            self.assertEqual((q3.context['revenue'],q3.context['cost'],q3.context['fund']),(Decimal('350.63'),Decimal('20'),Decimal('33.06')))
            self.assertEqual((q4.context['revenue'],q4.context['cost'],q4.context['fund']),(Decimal('350.63'),Decimal('30'),Decimal('32.06')))
            self.assertEqual(q3.context['denominator'],1);self.assertEqual(q4.context['denominator'],1)

    def test_cancellation_no_show_and_unconfirmed_are_separate(self):
        day=date(2026,10,12)
        with patch('django.utils.timezone.now',return_value=stamp(day)):
            self.arrange();waiting=self.reserve()
            extra=[]
            for hour in [9,10]:
                self.write(self.manager,'/schedules/',{'service':str(self.services[0].pk),'starts_at':stamp(day,hour).isoformat()})
                slot=ScheduleSlot.objects.get(clinic=self.clinic,starts_at=stamp(day,hour))
                self.write(self.front,'/book/',{'patient':str(self.patient.pk),'slot':str(slot.pk),'identity_confirmed':'on','confirmed':'yes','reason':'验收'})
                extra.append(Appointment.objects.get(slot=slot))
            self.write(self.front,reverse('appointment_command',args=[extra[0].pk,'cancel']),{'reason':'患者已确认取消'})
        with patch('django.utils.timezone.now',return_value=stamp(day,12,30)):
            self.write(self.front,reverse('appointment_command',args=[extra[1].pk,'no_show']),{'reason':'已联系核实未到诊'})
            self.client.force_login(self.hq)
            response=self.client.get('/reports/?year=2026&quarter=4')
            self.assertEqual((response.context['denominator'],response.context['unchecked']),(3,1))
            self.assertEqual(response.context['cancel_rate'],33.33);self.assertEqual(response.context['no_show_rate'],33.33)
            waiting.refresh_from_db();self.assertEqual(waiting.status,'booked')

    def test_unpaid_voided_and_price_snapshot_accounting(self):
        day=date(2026,10,12)
        with patch('django.utils.timezone.now',return_value=stamp(day)):
            self.arrange();app=self.reserve();visit,invoice=self.complete(app,pay=False)
        with patch('django.utils.timezone.now',return_value=stamp(day,12,30)):
            TreatmentType.objects.filter(pk=self.extra.pk).update(catalog_price=999)
            self.assertEqual(invoice.lines.get(visit_treatment__treatment_type=self.extra).unit_price_snapshot,Decimal('200.50'))
            self.client.force_login(self.hq)
            response=self.client.get('/reports/?year=2026&quarter=4')
            self.assertEqual(response.context['revenue'],0);self.assertEqual(response.context['receivable'],Decimal('350.63'))
            self.write(self.front,reverse('invoice_command',args=[invoice.pk,'void']),{'reason':'验收作废未支付单据'})
            self.client.force_login(self.hq)
            response=self.client.get('/reports/?year=2026&quarter=4')
            self.assertEqual(response.context['revenue'],0);self.assertEqual(response.context['receivable'],0)

    def test_qualification_can_be_shared_by_multiple_dentists(self):
        with patch('django.utils.timezone.now',return_value=stamp(date(2026,10,12))):
            self.arrange()
            self.write(self.hq,'/master/dentists/',{'employee_number':'ACCEPT-D2','name':'验收第二医生','birth_date':'1982-01-01',
                'home_address':'虚构地址','phone':'TEST-DOCTOR-2','email':'','active':'on'})
            doctor=Dentist.objects.get(employee_number='ACCEPT-D2')
            qualification=Qualification.objects.get(code='ACCEPT-Q1')
            self.write(self.hq,'/master/certificates/',{'dentist':str(doctor.pk),'qualification':str(qualification.pk),
                'certificate_number':'ACCEPT-SHARED','valid_from':'2025-01-01','valid_to':'2027-01-01','active':'on'})
            self.assertEqual(DentistQualification.objects.filter(qualification=qualification).count(),2)
