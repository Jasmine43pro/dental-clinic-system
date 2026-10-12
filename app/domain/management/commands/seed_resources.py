"""Add explicitly fictional resources without resetting existing configuration."""
from django.core.management.base import BaseCommand
from django.db import transaction
from domain.models import *


def initialize_demo_resources():
    for clinic in Clinic.objects.filter(code__startswith='DEMO-'):
        chair,_=ClinicalResource.objects.get_or_create(clinic=clinic,code='DEMO-CHAIR',defaults={'name':'演示牙椅容量','kind':'chair','capacity':3})
        ClinicalResource.objects.get_or_create(clinic=clinic,code='DEMO-ASSISTANT',defaults={'name':'演示辅助人员容量','kind':'assistant','capacity':2})
        ClinicalResource.objects.get_or_create(clinic=clinic,code='DEMO-EQUIPMENT',defaults={'name':'演示设备容量','kind':'equipment','capacity':1})
        for treatment in TreatmentType.objects.filter(code__startswith='DEMO-'):
            TreatmentBookingProfile.objects.get_or_create(treatment_type=treatment,defaults={'expected_minutes':30,'turnover_minutes':0})
            TreatmentResourceRequirement.objects.get_or_create(treatment_type=treatment,resource=chair,defaults={'quantity':1})
    for app in Appointment.objects.filter(slot__clinic__code__startswith='DEMO-').select_related('slot'):
        AppointmentPlan.objects.get_or_create(appointment=app,defaults={'plan_version':app.version,'ends_at':app.slot.ends_at,'resource_ends_at':app.slot.ends_at})
        active=app.status in ['booked','arrived','completed']
        AppointmentSlot.objects.get_or_create(appointment=app,slot=app.slot,plan_version=app.plan.plan_version,defaults={'is_active':active})
        chair=ClinicalResource.objects.filter(clinic=app.slot.clinic,code='DEMO-CHAIR').first()
        if chair and not app.resource_occupancies.exists():
            ResourceOccupancy.objects.create(appointment=app,resource=chair,plan_version=app.plan.plan_version,
                starts_at=app.slot.starts_at,ends_at=app.plan.resource_ends_at,quantity=1,is_active=active)


class Command(BaseCommand):
    help='Initialize fictional scheduling resources; preserve existing values and passwords.'
    @transaction.atomic
    def handle(self,*args,**options):
        initialize_demo_resources()
        self.stdout.write('Fictional resources initialized; existing configuration preserved.')
