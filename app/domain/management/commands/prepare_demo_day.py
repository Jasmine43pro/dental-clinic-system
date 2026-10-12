"""Append available demonstration slots without resetting any records or passwords."""
from datetime import datetime, time, timedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from domain.models import Clinic, DentistClinic, ScheduleSlot, AuditEvent


class Command(BaseCommand):
    help='为既有虚构演示诊所补充未来7天排班；不更改预约、账号或历史记录。'

    @transaction.atomic
    def handle(self,*args,**options):
        if not Clinic.objects.filter(code='DEMO-01').exists():
            raise CommandError('演示数据不存在，请先初始化演示库。')
        count=0
        now=timezone.now()
        for service in DentistClinic.objects.filter(active=True,clinic__code='DEMO-01',dentist__employee_number__startswith='DEMO-').select_related('dentist','clinic'):
            for offset in range(7):
                day=timezone.localdate()+timedelta(days=offset)
                for hour in [9,10,11,14,15,16]:
                    start=timezone.make_aware(datetime.combine(day,time(hour)))
                    end=start+timedelta(minutes=30)
                    if start<=now or service.valid_from>start or (service.valid_to and service.valid_to<end):continue
                    if ScheduleSlot.objects.filter(dentist=service.dentist,active=True,starts_at__lt=end,ends_at__gt=start).exists():continue
                    slot=ScheduleSlot(service=service,dentist=service.dentist,clinic=service.clinic,starts_at=start,ends_at=end)
                    from domain.services import valid_slot
                    valid_slot(slot);slot.save();count+=1
                    AuditEvent.objects.create(actor=None,source='demo_seed',object_type='ScheduleSlot',object_id=slot.pk,action='append_demo_slot')
        self.stdout.write(f'Added {count} demo slots. Existing records and credentials unchanged.')
