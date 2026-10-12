"""Append future fictional scheduling examples; preserve existing data and passwords."""
from datetime import datetime,time,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.utils import timezone
from domain.models import *
from domain.services import valid_slot


class Command(BaseCommand):
    help='Append two days of fictional continuous slots and a long-duration example.'
    @transaction.atomic
    def handle(self,*args,**options):
        clinic=Clinic.objects.filter(code='DEMO-01').first()
        if not clinic:raise CommandError('Initialize the fictional dataset first.')
        from domain.management.commands.seed_resources import initialize_demo_resources
        initialize_demo_resources()
        treatment,_=TreatmentType.objects.get_or_create(code='DEMO-LONG',defaults={'name':'连续预约演示项目（虚构）','unit':'次','catalog_price':Decimal('80.00')})
        TreatmentBookingProfile.objects.get_or_create(treatment_type=treatment,defaults={'expected_minutes':60,'turnover_minutes':10})
        for resource in ClinicalResource.objects.filter(clinic=clinic,code__in=['DEMO-CHAIR','DEMO-ASSISTANT','DEMO-EQUIPMENT']):
            TreatmentResourceRequirement.objects.get_or_create(treatment_type=treatment,resource=resource,defaults={'quantity':1})
        count=0
        for day in [1,2]:
            date=timezone.localdate()+timedelta(days=day)
            for dentist in Dentist.objects.filter(employee_number__in=['DEMO-D1','DEMO-D2','DEMO-D3'],active=True):
                for half in range(12):
                    start=datetime.combine(date,time(9),ZoneInfo('Asia/Shanghai'))+timedelta(minutes=30*half)
                    end=start+timedelta(minutes=30)
                    if ScheduleSlot.objects.filter(dentist=dentist,active=True,starts_at__lt=end,ends_at__gt=start).exists():continue
                    service=DentistClinic.objects.filter(dentist=dentist,clinic=clinic,active=True,valid_from__lte=start).filter(models.Q(valid_to__isnull=True)|models.Q(valid_to__gte=end)).first()
                    if not service:continue
                    slot=ScheduleSlot(service=service,dentist=dentist,clinic=clinic,starts_at=start,ends_at=end)
                    valid_slot(slot);slot.save();count+=1
        self.stdout.write('Future fictional slots appended: '+str(count)+'. Existing records and passwords preserved.')
