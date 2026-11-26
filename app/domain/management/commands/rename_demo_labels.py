from django.core.management.base import BaseCommand
from django.db import transaction

from domain.demo_names import (
    CLINIC_ADDRESSES, CLINIC_NAMES, CONTACT_NAMES, DENTIST_NAMES, MANAGER_NAMES, PATIENT_NAMES,
    QUALIFICATION_NAMES, RESOURCE_NAMES, TREATMENTS,
)
from domain.models import (
    Clinic, ClinicalResource, Dentist, EmergencyContact, Patient, Qualification, TreatmentType, User,
)


class Command(BaseCommand):
    help = 'Update original course sample labels without rewriting clinical or billing history.'

    @transaction.atomic
    def handle(self, *args, **options):
        clinic_count = 0
        for number, (name, address) in enumerate(zip(CLINIC_NAMES, CLINIC_ADDRESSES), start=1):
            clinics = Clinic.objects.filter(code=f'DEMO-{number:02}', phone=f'TEST-CLINIC-{number:02}')
            clinic_count += clinics.filter(name=f'虚构演示诊所{number:02}').update(name=name)
            clinic_count += clinics.filter(
                address__in=('地址未公开', f'演示地址{number:02}（非真实地址）'),
            ).update(address=address)
            clinic_count += clinics.filter(manager_name=f'演示负责人{number:02}').update(
                manager_name=MANAGER_NAMES[number - 1],
            )

        dentist_count = 0
        other_count = 0
        for number, name in enumerate(DENTIST_NAMES, start=1):
            dentists = Dentist.objects.filter(employee_number=f'DEMO-D{number}', phone=f'TEST-DENTIST-{number}')
            dentist_count += dentists.filter(name=f'演示医生{number}').update(name=name)
            other_count += dentists.filter(home_address='虚构地址，不公开').update(home_address='地址未公开')
            other_count += User.objects.filter(
                username=f'demo_dentist_{number}', pk__in=dentists.filter(name=name).values('user_id'),
                first_name='', last_name='',
            ).update(first_name=name)

        for number, (patient_name, contact_name) in enumerate(zip(PATIENT_NAMES, CONTACT_NAMES), start=1):
            patient_phone = f'TEST-PATIENT-{number:02}'
            patients = Patient.objects.filter(phone=patient_phone)
            other_count += patients.filter(name=f'虚构患者{number:02}').update(name=patient_name)
            other_count += patients.filter(billing_address='虚构账单地址').update(billing_address='地址未公开')
            other_count += EmergencyContact.objects.filter(
                patient__in=patients, phone=f'TEST-CONTACT-{number:02}', name='虚构联系人',
            ).update(name=contact_name)
            other_count += User.objects.filter(
                username=f'demo_patient_{number}', pk__in=patients.filter(name=patient_name).values('user_id'),
                first_name='', last_name='',
            ).update(first_name=patient_name)

        for number, name in enumerate(QUALIFICATION_NAMES, start=1):
            old_name = '儿科' if number == 3 else name
            other_count += Qualification.objects.filter(code=f'Q{number}', name=f'{old_name}（演示）').update(name=name)

        for number, (old_name, name, _) in enumerate(TREATMENTS, start=1):
            other_count += TreatmentType.objects.filter(code=f'DEMO-T{number}', name=f'{old_name}（虚构价格）').update(name=name)
        other_count += TreatmentType.objects.filter(
            code='DEMO-LONG', name='连续预约演示项目（虚构）',
        ).update(name='综合口腔检查')

        for code, old_name, name in RESOURCE_NAMES:
            other_count += ClinicalResource.objects.filter(
                clinic__code__startswith='DEMO-', code=code, name=old_name,
            ).update(name=name)

        self.stdout.write(
            f'Updated {clinic_count} clinic fields, {dentist_count} dentist names and {other_count} other sample fields.'
        )
