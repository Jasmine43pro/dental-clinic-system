"""Insert a complete fictional dataset once; never overwrite existing records."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import json
import secrets
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from domain.models import *

class Command(BaseCommand):
    help='Create fictional demo data, without deletion or resetting passwords.'
    def add_arguments(self, parser):
        parser.add_argument('--no-credentials-file',action='store_true',help='Use for isolated automated tests.')
    @transaction.atomic
    def handle(self,*args,**options):
        if Clinic.objects.filter(code='DEMO-01').exists():
            self.stdout.write('Demo dataset exists; no records or passwords changed.')
            return
        if Clinic.objects.exists() or User.objects.exists():
            raise CommandError('Database not empty. Refusing to mix seed data with existing records.')
        credentials_path=settings.BASE_DIR/'.local/demo-accounts.json'
        if not options['no_credentials_file'] and credentials_path.exists():
            raise CommandError('Credentials file already exists. Refusing to overwrite it.')
        now=timezone.now()
        today=timezone.localdate()
        zone=ZoneInfo('Asia/Shanghai')
        def stamp(day,hour=9): return datetime.combine(day,time(hour),zone)
        credentials={}
        roles={}
        for code,name in [('patient','患者'),('reception','前台'),('dentist','医生'),('manager','诊所管理者'),('headquarters','总部'),('finance','财务'),('administrator','系统管理员')]:
            roles[code]=Role.objects.create(code=code,name=name)
        # Action and data scopes are enforced by the access and service modules.
        def account(name,role,clinic=None):
            password=secrets.token_urlsafe(18)
            user=User.objects.create_user(username=name,password=password)
            credentials[name]=password
            RoleAssignment.objects.create(user=user,role=roles[role],clinic=clinic,
                scope_type='clinic' if clinic else ('self' if role in ['patient','dentist'] else 'global'),valid_from=now)
            return user
        clinics=[]
        for i in range(1,16):
            c=Clinic.objects.create(code=f'DEMO-{i:02}',name=f'虚构演示诊所{i:02}',address=f'演示地址{i:02}（非真实地址）',
                manager_name=f'演示负责人{i:02}',phone=f'TEST-CLINIC-{i:02}',email=f'clinic{i}@example.invalid',floor_area=100+i)
            clinics.append(c)
            for weekday in range(1,8):
                ClinicHours.objects.create(clinic=c,weekday=weekday,opens_at=time(9),closes_at=time(17))
        reception=account('demo_reception_a','reception',clinics[0])
        account('demo_reception_b','reception',clinics[1])
        manager=account('demo_manager_a','manager',clinics[0])
        account('demo_headquarters','headquarters')
        finance=account('demo_finance','finance')
        account('demo_admin','administrator')
        doctors=[]
        relations=[]
        qualifications=[Qualification.objects.create(code=f'Q{i}',name=name) for i,name in enumerate(['牙髓病学（演示）','口腔放射学（演示）','儿科（演示）'],1)]
        for i in range(6):
            user=account(f'demo_dentist_{i+1}','dentist')
            d=Dentist.objects.create(employee_number=f'DEMO-D{i+1}',name=f'演示医生{i+1}',user=user,
                birth_date=date(1980+i,1,1),home_address='虚构地址，不公开',phone=f'TEST-DENTIST-{i+1}',email=f'dentist{i+1}@example.invalid')
            doctors.append(d)
            rel=DentistClinic.objects.create(dentist=d,clinic=clinics[0 if i<3 else 1],valid_from=stamp(today-timedelta(days=400)))
            relations.append(rel)
            DentistQualification.objects.create(dentist=d,qualification=qualifications[i%3],certificate_number=f'DEMO-CERT-{i}',valid_from=today-timedelta(days=400),valid_to=today+timedelta(days=400))
        for i in range(2):
            DentistClinic.objects.create(dentist=doctors[i],clinic=clinics[1],valid_from=stamp(today-timedelta(days=400)))
        DentistQualification.objects.create(dentist=doctors[0],qualification=qualifications[1],certificate_number='DEMO-CERT-EXTRA',valid_from=today-timedelta(days=10),valid_to=today+timedelta(days=400))
        patients=[]
        for i in range(12):
            u=account(f'demo_patient_{i+1}','patient') if i<2 else None
            p=Patient.objects.create(user=u,name=f'虚构患者{i+1:02}',birth_date=date(1960+i*3,1,1),phone=f'TEST-PATIENT-{i+1:02}',billing_address='虚构账单地址',email='')
            patients.append(p)
            EmergencyContact.objects.create(patient=p,name='虚构联系人',relationship='家属',phone=f'TEST-CONTACT-{i+1:02}')
        for code,name in [('LATEX','乳胶'),('METAL','金属')]:
            allergy=Allergy.objects.create(code=code,name=name)
            PatientAllergy.objects.create(patient=patients[0],allergy=allergy,notes='仅用于软件测试，非医疗信息')
        treatments=[TreatmentType.objects.create(code=f'DEMO-T{i}',name=f'{name}（虚构价格）',unit='单位',catalog_price=price)
                    for i,(name,price) in enumerate([('洗牙','100.00'),('修复','200.50'),('检查','50.00'),('抛光','80.00'),('项目五','300.00'),('项目六','120.00')],1)]
        for rel in relations:
            for day in range(1,8):
                for halfhour in range(4):
                    start=stamp(today+timedelta(days=day),9)+timedelta(minutes=30*halfhour)
                    ScheduleSlot.objects.create(service=rel,dentist=rel.dentist,clinic=rel.clinic,starts_at=start,ends_at=start+timedelta(minutes=30))
        statuses=['booked','cancelled','no_show','arrived','completed','completed','completed']
        for i,status in enumerate(statuses):
            day=today+timedelta(days=1) if status in ['booked','cancelled'] else today-timedelta(days=i+1)
            start=stamp(day,11)+timedelta(minutes=30 if status=='cancelled' else 0)
            slot=ScheduleSlot.objects.create(service=relations[0],dentist=doctors[0],clinic=clinics[0],starts_at=start,ends_at=start+timedelta(minutes=30))
            app=Appointment.objects.create(number=f'DEMO-A{i+1:03}',patient=patients[i],slot=slot,status=status,source='reception',created_by=reception,
                checked_in_at=start if status in ['arrived','completed'] else None,
                cancelled_at=now if status=='cancelled' else None,cancel_reason='演示取消' if status=='cancelled' else '',cancel_party='patient' if status=='cancelled' else '')
            if status not in ['arrived','completed']: continue
            visit=Visit.objects.create(appointment=app,dentist=doctors[0],status='completed' if status=='completed' else 'in_progress',
                started_at=start,completed_at=start+timedelta(minutes=25) if status=='completed' else None,clinical_notes='虚构测试记录，不是诊疗建议')
            line=VisitTreatment.objects.create(visit=visit,treatment_type=treatments[0],name_snapshot=treatments[0].name,quantity=1,unit_price_snapshot=100,subtotal=100)
            if status!='completed': continue
            def invoice(number,state='issued',replaces=None):
                inv=Invoice.objects.create(number=number,visit=visit,source_visit_version=1,status=state,total=100,issued_by=reception,issued_at=start+timedelta(minutes=26),
                    replaces=replaces,voided_by=reception if state=='void' else None,voided_at=now if state=='void' else None,void_reason='演示作废重开' if state=='void' else '')
                InvoiceLine.objects.create(invoice=inv,visit_treatment=line,name_snapshot=line.name_snapshot,quantity_snapshot=1,unit_price_snapshot=100,subtotal=100)
                return inv
            if i==4:
                inv=invoice('DEMO-INV-PAID','paid')
                PaymentRecord.objects.create(invoice=inv,method='模拟现金',amount=100,status='success',succeeded_at=start+timedelta(minutes=27),recorded_by=reception,request_reference='DEMO-PAY-1')
            elif i==5:
                old=invoice('DEMO-INV-VOID','void')
                invoice('DEMO-INV-REPLACEMENT',replaces=old)
            else:
                VisitAmendment.objects.create(visit=visit,base_version=1,proposed_changes={'clinical_notes':{'before':visit.clinical_notes,'after':'演示补充说明'}},reason='演示待审批',applicant=doctors[0].user)
        from domain.management.commands.seed_resources import initialize_demo_resources
        initialize_demo_resources()
        CostRecord.objects.create(clinic=clinics[0],dentist=doctors[0],category='supplies',amount=20,occurred_on=today-timedelta(days=5),source_reference='DEMO-COST-1',description='虚构用品成本',recorded_by=finance)
        CostRecord.objects.create(clinic=clinics[0],category='operating',amount=30,occurred_on=today-timedelta(days=5),source_reference='DEMO-COST-2',description='虚构未分摊公共费用',recorded_by=finance)
        AuditEvent.objects.create(actor=None,source='demo_seed',object_type='Clinic',object_id=clinics[0].id,action='seed_dataset',reason='仅含虚构数据')
        if not options['no_credentials_file']:
            def write_credentials():
                with credentials_path.open('x',encoding='utf-8') as file:
                    json.dump(credentials,file,ensure_ascii=False,indent=2)
            transaction.on_commit(write_credentials)
        message = 'Seeded: 15 clinics, 6 dentists, 12 patients.'
        if not options['no_credentials_file']:
            message += ' Credentials retained in .local/demo-accounts.json (not printed).'
        self.stdout.write(message)
