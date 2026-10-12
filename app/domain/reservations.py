"""Continuous schedule units and capacity checks under ordered database locks."""
from collections import defaultdict
from datetime import timedelta
from math import ceil
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import *


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


def planning_inputs(slot,treatment_id=None,expected_minutes=None,old_plan=None):
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
            profile=TreatmentBookingProfile.objects.select_for_update().filter(treatment_type=treatment).first()
            require(profile is not None,'该治疗项目尚未配置预计时长，请联系诊所。')
            minutes,turnover=profile.expected_minutes,profile.turnover_minutes
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


def validate_plan(slot,patient,inputs,excluding=None):
    from .services import valid_slot
    finish=slot.starts_at+timedelta(minutes=ceil(inputs['minutes']/30)*30)
    slots=list(ScheduleSlot.objects.select_for_update(of=('self',)).filter(dentist=slot.dentist,
        clinic=slot.clinic,starts_at__gte=slot.starts_at,starts_at__lt=finish).select_related('service','clinic','dentist').order_by('starts_at'))
    require(len(slots)==ceil(inputs['minutes']/30),'缺少覆盖治疗时长的连续排班单元。')
    cursor=slot.starts_at
    for unit in slots:
        require(unit.starts_at==cursor,'排班单元不连续，请重新选择。')
        valid_slot(unit)
        cursor=unit.ends_at
    ids=[s.pk for s in slots]
    require(not AppointmentSlot.objects.filter(slot_id__in=ids,is_active=True).exclude(appointment_id=excluding).exists(),'连续时段已被其他预约占用。')
    require(not Appointment.objects.filter(slot_id__in=ids,status__in=['booked','arrived','completed']).exclude(pk=excluding).exists(),'该时段刚刚被占用，请重新选择。')
    # Patient/doctor treatment intervals exclude cleanup; resources include cleanup.
    others=Appointment.objects.filter(status__in=['booked','arrived','completed']).exclude(pk=excluding).filter(slot__starts_at__lt=inputs['ends'])
    conflict=Q(plan__ends_at__gt=slot.starts_at)|Q(plan__isnull=True,slot__ends_at__gt=slot.starts_at)
    if patient:
        require(not others.filter(conflict,patient=patient).exists(),'患者已有重叠预约，请选择其他时间。')
    require(not others.filter(conflict,slot__dentist=slot.dentist).exists(),'医生已有重叠预约。')
    end=timezone.localtime(inputs['resource_ends'])
    start=timezone.localtime(slot.starts_at)
    require(start.date()==end.date() and ClinicHours.objects.filter(clinic=slot.clinic,
        weekday=start.isoweekday(),opens_at__lte=start.time(),closes_at__gte=end.time()).exists(),'治疗及清洁周转须在诊所营业时间内完成。')
    needs=dict(inputs['needs'])
    resources=list(ClinicalResource.objects.select_for_update().filter(pk__in=needs).order_by('pk'))
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


@transaction.atomic
def preview(slot_id,treatment_id=None,patient=None,excluding=None):
    slot=ScheduleSlot.objects.select_related('clinic','dentist','service').get(pk=slot_id)
    require(slot.starts_at>timezone.now(),'不能预约已经开始的时段。')
    Clinic.objects.select_for_update().get(pk=slot.clinic_id)
    Dentist.objects.select_for_update().get(pk=slot.dentist_id)
    inputs=planning_inputs(slot,treatment_id)
    validate_plan(slot,patient,inputs,excluding)
    return inputs


def affected_by_outage(outage):
    return Appointment.objects.filter(resource_occupancies__resource=outage.resource,
        resource_occupancies__is_active=True,resource_occupancies__starts_at__lt=outage.ends_at,
        resource_occupancies__ends_at__gt=outage.starts_at,status__in=['booked','arrived']).distinct()
