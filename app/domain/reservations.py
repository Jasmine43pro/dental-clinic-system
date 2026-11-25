"""Continuous schedule units and capacity checks under ordered database locks."""
from bisect import bisect_left
from collections import defaultdict
from datetime import timedelta
from math import ceil
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from .models import *


OCCUPYING_STATUSES = [Appointment.Status.BOOKED, Appointment.Status.ARRIVED, Appointment.Status.COMPLETED]


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def capacity_peak(rows, start, end):
    """Half-open intervals: adjacent reservations share no instant."""
    events=defaultdict(int)
    for a,b,qty in rows:
        a,b=max(a,start),min(b,end)
        if a<b:
            events[a]+=qty
            events[b]-=qty
    used=peak=0
    for at in sorted(events):
        used+=events[at]
        peak=max(peak,used)
    return peak


def check_capacity(resource,start,end,quantity,excluding=None):
    require(resource.active, '所需资源已停用：'+resource.name)
    occupied=ResourceOccupancy.objects.filter(resource=resource,is_active=True,
        starts_at__lt=end,ends_at__gt=start).exclude(appointment_id=excluding)
    outages=ResourceUnavailable.objects.filter(resource=resource,active=True,
        starts_at__lt=end,ends_at__gt=start)
    rows=list(occupied.values_list('starts_at','ends_at','quantity'))+list(outages.values_list('starts_at','ends_at','quantity'))
    require(capacity_peak(rows,start,end)+quantity<=resource.capacity,
        '资源容量不足或处于停用区间：'+resource.name)


def planning_inputs(slot,treatment_id=None,expected_minutes=None,old_plan=None,*,turnover_minutes=None,lock=True):
    if old_plan is not None:
        minutes=expected_minutes or old_plan.expected_minutes
        turnover=old_plan.turnover_minutes
        treatment_id=old_plan.treatment_type_id
        # Extensions preserve the reservation's resource snapshot.
        needs=list(ResourceOccupancy.objects.filter(appointment=old_plan.appointment,is_active=True).values_list('resource_id','quantity'))
    else:
        minutes,turnover=30,0
        if treatment_id:
            treatment=TreatmentType.objects.get(pk=treatment_id,active=True)
            profiles=TreatmentBookingProfile.objects.filter(treatment_type=treatment)
            profile=(profiles.select_for_update() if lock else profiles).first()
            require(profile is not None,'该治疗项目尚未配置预计时长，请联系诊所。')
            minutes,turnover=profile.expected_minutes,profile.turnover_minutes
        if expected_minutes is not None:
            minutes = expected_minutes
        if turnover_minutes is not None:
            turnover = turnover_minutes
        needs=list(TreatmentResourceRequirement.objects.filter(treatment_type_id=treatment_id,
            resource__clinic=slot.clinic).values_list('resource_id','quantity')) if treatment_id else []
        # Consultation and configured treatments both require a chair.
        chair_ids=set(ClinicalResource.objects.filter(pk__in=[r for r,q in needs],kind='chair').values_list('pk',flat=True))
        if not chair_ids:
            chair=ClinicalResource.objects.filter(clinic=slot.clinic,kind='chair',active=True).order_by('code').first()
            require(chair is not None,'诊所尚未配置可用牙椅，请联系管理者。')
            needs.append((chair.pk,1))
    require(0<minutes<=480 and 0<=turnover<=180,'预计治疗时长须为1至480分钟，周转须为0至180分钟。')
    ends=slot.starts_at+timedelta(minutes=minutes)
    resource_ends=ends+timedelta(minutes=turnover)
    return dict(treatment_id=treatment_id,minutes=minutes,turnover=turnover,ends=ends,resource_ends=resource_ends,needs=needs)


def validate_plan(slot,patient,inputs,excluding=None,*,lock=True):
    from . import clinic_calendar
    from .services import valid_slot
    hours = clinic_calendar.hours_for_day(slot.clinic_id, timezone.localdate(slot.starts_at))
    finish=slot.starts_at+timedelta(minutes=ceil(inputs['minutes']/30)*30)
    units=ScheduleSlot.objects.filter(dentist=slot.dentist, clinic=slot.clinic,
        starts_at__gte=slot.starts_at, starts_at__lt=finish).select_related('service','clinic','dentist').order_by('starts_at')
    slots=list(units.select_for_update(of=('self',)) if lock else units)
    require(len(slots)==ceil(inputs['minutes']/30),'缺少覆盖治疗时长的连续排班单元。')
    cursor=slot.starts_at
    for unit in slots:
        require(unit.starts_at==cursor,'排班单元不连续，请重新选择。')
        valid_slot(unit, hours=hours)
        cursor=unit.ends_at
    ids=[s.pk for s in slots]
    require(not AppointmentSlot.objects.filter(slot_id__in=ids,is_active=True).exclude(appointment_id=excluding).exists(),'连续时段已被其他预约占用。')
    require(not Appointment.objects.filter(slot_id__in=ids,status__in=OCCUPYING_STATUSES).exclude(pk=excluding).exists(),'该时段刚刚被占用，请重新选择。')
    # Patient/doctor treatment intervals exclude cleanup; resources include cleanup.
    others=Appointment.objects.filter(status__in=OCCUPYING_STATUSES).exclude(pk=excluding).filter(slot__starts_at__lt=inputs['ends'])
    conflict=Q(plan__ends_at__gt=slot.starts_at)|Q(plan__isnull=True,slot__ends_at__gt=slot.starts_at)
    if patient:
        require(not others.filter(conflict,patient=patient).exists(),'患者已有重叠预约，请选择其他时间。')
    require(not others.filter(conflict,slot__dentist=slot.dentist).exists(),'医生已有重叠预约。')
    require(clinic_calendar.covers(hours, slot.starts_at, inputs['resource_ends']),
            '治疗及清洁周转须在诊所营业时间内完成。')
    needs=dict(inputs['needs'])
    needed_resources=ClinicalResource.objects.filter(pk__in=needs).order_by('pk')
    resources=list(needed_resources.select_for_update() if lock else needed_resources)
    require(len(resources)==len(needs),'所需资源记录不完整。')
    for resource in resources:
        require(resource.clinic_id==slot.clinic_id,'资源不属于预约诊所。')
        check_capacity(resource,slot.starts_at,inputs['resource_ends'],needs[resource.pk],excluding)
    return slots


def persist_plan(app,slots,inputs):
    AppointmentSlot.objects.filter(appointment=app,is_active=True).update(is_active=False)
    ResourceOccupancy.objects.filter(appointment=app,is_active=True).update(is_active=False)
    AppointmentPlan.objects.update_or_create(appointment=app,defaults=dict(plan_version=app.version,
        treatment_type_id=inputs['treatment_id'],expected_minutes=inputs['minutes'],turnover_minutes=inputs['turnover'],
        ends_at=inputs['ends'],resource_ends_at=inputs['resource_ends']))
    app._state.fields_cache.pop('plan',None)
    AppointmentSlot.objects.bulk_create([AppointmentSlot(appointment=app,slot=s,plan_version=app.version) for s in slots])
    ResourceOccupancy.objects.bulk_create([ResourceOccupancy(appointment=app,resource_id=r,quantity=q,
        plan_version=app.version,starts_at=app.slot.starts_at,ends_at=inputs['resource_ends']) for r,q in inputs['needs']])


def preview(slot_id,treatment_id=None,patient=None,excluding=None):
    # A preview never reserves resources. The booking transaction validates again under locks.
    slot=ScheduleSlot.objects.select_related('clinic','dentist','service').get(pk=slot_id)
    require(slot.starts_at>timezone.now(),'不能预约已经开始的时段。')
    inputs=planning_inputs(slot,treatment_id,lock=False)
    validate_plan(slot,patient,inputs,excluding,lock=False)
    return inputs


def check_consultation_opening(slot):
    validate_plan(slot, None, planning_inputs(slot), lock=False)


def available_consultations(candidates, patient):
    """Check consultation starts for one clinic using a read-only batch."""
    from . import clinic_calendar
    from .services import valid_slot

    if not candidates:
        return
    clinic_id = candidates[0].clinic_id
    doctor_ids = {slot.dentist_id for slot in candidates}
    earliest, latest = candidates[0].starts_at, max(slot.ends_at for slot in candidates)
    chair = ClinicalResource.objects.filter(clinic_id=clinic_id, kind='chair', active=True).order_by('code').first()
    if chair is None:
        return
    occupied = ResourceOccupancy.objects.filter(resource=chair, is_active=True,
        starts_at__lt=latest, ends_at__gt=earliest).values_list('starts_at', 'ends_at', 'quantity')
    outages = ResourceUnavailable.objects.filter(resource=chair, active=True,
        starts_at__lt=latest, ends_at__gt=earliest).values_list('starts_at', 'ends_at', 'quantity')
    capacity_rows = [*occupied, *outages]

    units = defaultdict(list)
    for doctor_id, starts_at in ScheduleSlot.objects.filter(clinic_id=clinic_id, dentist_id__in=doctor_ids,
            starts_at__gte=earliest, starts_at__lt=latest).order_by('starts_at').values_list('dentist_id', 'starts_at'):
        units[doctor_id].append(starts_at)
    taken = set(AppointmentSlot.objects.filter(slot_id__in=[slot.pk for slot in candidates], is_active=True)
        .values_list('slot_id', flat=True))
    overlapping = list(Appointment.objects.filter(status__in=OCCUPYING_STATUSES,
        slot__starts_at__lt=latest).filter(Q(patient=patient) | Q(slot__dentist_id__in=doctor_ids)).filter(
        Q(plan__ends_at__gt=earliest) | Q(plan__isnull=True, slot__ends_at__gt=earliest),
    ).values_list('patient_id', 'slot__dentist_id', 'slot__starts_at', 'plan__ends_at', 'slot__ends_at'))

    hours = {}
    now = timezone.now()
    for slot in candidates:
        if slot.pk in taken or slot.starts_at <= now:
            continue
        doctor_units = units[slot.dentist_id]
        if bisect_left(doctor_units, slot.ends_at) - bisect_left(doctor_units, slot.starts_at) != 1:
            continue
        day = timezone.localdate(slot.starts_at)
        if day not in hours:
            hours[day] = clinic_calendar.hours_for_day(clinic_id, day)
        try:
            valid_slot(slot, hours=hours[day])
        except ValidationError:
            continue
        if any((person == patient.pk or doctor == slot.dentist_id) and
               start < slot.ends_at and (plan_end or slot_end) > slot.starts_at
               for person, doctor, start, plan_end, slot_end in overlapping):
            continue
        if capacity_peak(capacity_rows, slot.starts_at, slot.ends_at) < chair.capacity:
            yield slot


def affected_by_outage(outage):
    return Appointment.objects.filter(resource_occupancies__resource=outage.resource,
        resource_occupancies__is_active=True,resource_occupancies__starts_at__lt=outage.ends_at,
        resource_occupancies__ends_at__gt=outage.starts_at,status__in=['booked','arrived']).distinct()
