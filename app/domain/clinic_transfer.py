"""Eligibility checks shared by the transfer search and booking transaction."""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from . import clinic_calendar, reservations
from .models import (Appointment, ClinicTransferTime, ClinicalResource, DentistQualification,
    ResourceUnavailable, ScheduleSlot, TreatmentQualificationRequirement, WorkCalendar)


@dataclass(frozen=True)
class TransferRequest:
    version: int
    earliest: datetime
    latest: datetime
    consent: bool


def require_affected(appointment):
    reservations.require(
        appointment.status == Appointment.Status.BOOKED and appointment.slot.starts_at > timezone.now(),
        '仅可跨店改约尚未开始的待到诊预约。',
    )
    entry = WorkCalendar.objects.filter(clinic_id=appointment.slot.clinic_id,
        cal_date=timezone.localdate(appointment.slot.starts_at)).exclude(kind=WorkCalendar.Kind.WORKING).first()
    if entry and any(row.pk == appointment.pk for row in clinic_calendar.affected_appointments(entry)):
        return
    for occupancy in appointment.resource_occupancies.filter(is_active=True):
        if ResourceUnavailable.objects.filter(resource_id=occupancy.resource_id, active=True,
                starts_at__lt=occupancy.ends_at, ends_at__gt=occupancy.starts_at).exists():
            return
    raise ValidationError('此预约当前不在营业变动或资源停用的影响范围内。')


def travel_minutes(origin_id, destination_id):
    minutes = ClinicTransferTime.objects.filter(origin_id=origin_id, destination_id=destination_id,
        active=True).values_list('minutes', flat=True).first()
    reservations.require(minutes is not None, '门店间未配置对应方向的转场时间。')
    return minutes


def validate_travel(slot, reserved_end):
    from .services import valid_slot

    limit = timedelta(hours=12)
    other_slots = ScheduleSlot.objects.filter(dentist_id=slot.dentist_id, active=True).exclude(
        clinic_id=slot.clinic_id).select_related('clinic', 'dentist', 'service')
    before = other_slots.filter(ends_at__lte=slot.starts_at, ends_at__gt=slot.starts_at - limit).order_by('-ends_at')
    after = other_slots.filter(starts_at__gte=reserved_end, starts_at__lt=reserved_end + limit).order_by('starts_at')
    for candidates, incoming in ((before, True), (after, False)):
        for neighbor in candidates:
            try:
                valid_slot(neighbor)
            except ValidationError:
                continue
            if incoming:
                gap = slot.starts_at - neighbor.ends_at
                minutes = travel_minutes(neighbor.clinic_id, slot.clinic_id)
            else:
                gap = neighbor.starts_at - reserved_end
                minutes = travel_minutes(slot.clinic_id, neighbor.clinic_id)
            reservations.require(gap >= timedelta(minutes=minutes),
                f'医生与{neighbor.clinic.name}相邻排班间隔不足，转场至少需要 {minutes} 分钟。')
            break


def transfer_plan(appointment, slot, earliest, latest, *, lock):
    plan = getattr(appointment, 'plan', None)
    treatment_id = plan.treatment_type_id if plan else None
    reservations.require(slot.clinic_id != appointment.slot.clinic_id, '跨店候选须位于另一家诊所。')
    travel_minutes(appointment.slot.clinic_id, slot.clinic_id)
    inputs = reservations.planning_inputs(slot, treatment_id,
        expected_minutes=plan.expected_minutes if plan else None,
        turnover_minutes=plan.turnover_minutes if plan else None, lock=lock)
    reservations.require(timezone.now() < slot.starts_at and earliest <= slot.starts_at < inputs['ends'] <= latest,
        '该时段已开始或超出患者确认的可就诊时间。')

    day = timezone.localdate(slot.starts_at)
    required = set(TreatmentQualificationRequirement.objects.filter(
        treatment_type_id=treatment_id, active=True).values_list('qualification_id', flat=True))
    reservations.require(required, '该项目未配置跨店接诊资质要求。')
    held = set(DentistQualification.objects.filter(dentist_id=slot.dentist_id, active=True,
        qualification_id__in=required, valid_from__lte=day).filter(
        Q(valid_to__isnull=True) | Q(valid_to__gte=day)).values_list('qualification_id', flat=True))
    reservations.require(required <= held, '医生缺少该项目所需的有效资质。')

    original_needs = Counter()
    for occupancy in appointment.resource_occupancies.filter(is_active=True).select_related('resource'):
        original_needs[occupancy.resource.kind] += occupancy.quantity
    target_needs = Counter()
    kinds = dict(ClinicalResource.objects.filter(pk__in=dict(inputs['needs'])).values_list('pk', 'kind'))
    for resource_id, quantity in inputs['needs']:
        target_needs[kinds.get(resource_id)] += quantity
    reservations.require(all(target_needs[kind] >= quantity for kind, quantity in original_needs.items()),
        '目标诊所未配置足以覆盖原安排的同类治疗资源。')

    units = reservations.validate_plan(slot, appointment.patient, inputs, appointment.pk, lock=lock)
    validate_travel(slot, units[-1].ends_at)
    return units, inputs
