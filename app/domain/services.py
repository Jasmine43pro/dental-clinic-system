"""Transactional business commands. All web writes use this boundary."""
import hashlib
import json
import uuid
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from . import access
from .models import *


def check(condition, message):
    if not condition:
        raise ValidationError(message)


def audit(actor, obj, action, before=None, after=None, reason=''):
    AuditEvent.objects.create(actor=actor, source='web', object_type=type(obj).__name__,
        object_id=obj.pk, action=action, before_value=before or {}, after_value=after or {}, reason=reason)


def number(prefix):
    return prefix + '-' + timezone.localtime().strftime('%Y%m%d') + '-' + uuid.uuid4().hex[:12].upper()


def money(value):
    return Decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def command(actor, operation, key, payload, callback):
    """Persist result with the write, serializing replay keys for a single actor."""
    check(bool(key) and len(key) <= 100, '请求标识缺失，请刷新页面后重试。')
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    with transaction.atomic():
        current = User.objects.select_for_update().get(pk=actor.pk)
        check(current.is_active, '账号已停用。')
        receipt = CommandReceipt.objects.filter(actor=actor, operation=operation, request_key=key).first()
        if receipt:
            check(receipt.payload_hash == digest, '同一请求标识不能用于不同内容，请刷新后重试。')
            return receipt.result_object_id
        result = callback()
        CommandReceipt.objects.create(actor=actor, operation=operation, request_key=key,
            payload_hash=digest, result_object_id=result.pk)
        return result.pk


def valid_slot(slot):
    start, end = timezone.localtime(slot.starts_at), timezone.localtime(slot.ends_at)
    service = slot.service
    check(slot.active and slot.clinic.active and slot.dentist.active and service.active, '诊所、医生或排班已停用。')
    check(service.valid_from <= slot.starts_at and (service.valid_to is None or service.valid_to >= slot.ends_at), '医生服务关系不覆盖该时段。')
    check(start.date() == end.date() and ClinicHours.objects.filter(clinic=slot.clinic,
        weekday=start.isoweekday(), opens_at__lte=start.time(), closes_at__gte=end.time()).exists(), '时段不在诊所营业时间内。')


@transaction.atomic
def book(actor, patient_id, slot_id, reason='', replacing=None, treatment_id=None):
    from . import reservations
    patient=Patient.objects.select_for_update().get(pk=patient_id)
    slot=ScheduleSlot.objects.select_related('service','clinic','dentist').get(pk=slot_id)
    old=Appointment.objects.select_related('slot').get(pk=replacing) if replacing else None
    dentist_ids={slot.dentist_id}
    if old: dentist_ids.add(old.slot.dentist_id)
    clinic_ids={slot.clinic_id}
    if old: clinic_ids.add(old.slot.clinic_id)
    list(Clinic.objects.select_for_update().filter(pk__in=clinic_ids).order_by('pk'))
    list(Dentist.objects.select_for_update().filter(pk__in=dentist_ids).order_by('pk'))
    self_booking=access.has_role(actor,'patient') and patient.user_id==actor.pk
    if not self_booking: access.require(actor,['reception'],slot.clinic_id)
    check(patient.active,'患者档案已停用。')
    check(slot.starts_at>timezone.now(),'不能预约已经开始的时段。')
    if old:
        old=Appointment.objects.select_for_update().get(pk=old.pk)
        access.require(actor,['reception'],old.slot.clinic_id)
        check(old.status=='booked' and old.slot.starts_at>timezone.now(),'仅可调整尚未开始的待到诊预约。')
        check(old.patient_id==patient.pk,'调整预约不能更换患者。')
        check(bool(reason.strip()),'请填写调整原因。')
    inputs=reservations.planning_inputs(slot,treatment_id)
    slots=reservations.validate_plan(slot,patient,inputs,replacing)
    if old:
        before={'slot':str(old.slot_id),'ends_at':old.ends_at.isoformat(),'version':old.version}
        app=old
        app.slot=slot
        app.version+=1
        app.save()
    else:
        before={}
        app=Appointment.objects.create(number=number('APT'),patient=patient,slot=slot,
            source='patient' if self_booking else 'reception',reason=reason,created_by=actor)
    reservations.persist_plan(app,slots,inputs)
    audit(actor,app,'reschedule' if old else 'book',before,
        {'slot':str(slot.pk),'ends_at':inputs['ends'].isoformat(),'resource_ends_at':inputs['resource_ends'].isoformat(),
         'version':app.version,'slot_count':len(slots)},reason)
    return app


@transaction.atomic
def extend_appointment(actor,pk,minutes,reason):
    from . import reservations
    source=Appointment.objects.select_related('slot').get(pk=pk)
    Patient.objects.select_for_update().get(pk=source.patient_id)
    Clinic.objects.select_for_update().get(pk=source.slot.clinic_id)
    Dentist.objects.select_for_update().get(pk=source.slot.dentist_id)
    app=Appointment.objects.select_for_update().get(pk=pk)
    if not access.own_doctor(actor,app): access.require(actor,['reception'],app.slot.clinic_id)
    check(app.status in ['booked','arrived'],'仅可延长待到诊或进行中的预约。')
    check(bool(reason.strip()),'请记录延长原因。')
    check(minutes>app.duration_minutes,'新的总时长须大于原治疗时长。')
    inputs=reservations.planning_inputs(app.slot,expected_minutes=minutes,old_plan=app.plan)
    slots=reservations.validate_plan(app.slot,app.patient,inputs,app.pk)
    before={'ends_at':app.ends_at.isoformat(),'version':app.version}
    app.version+=1
    app.save()
    reservations.persist_plan(app,slots,inputs)
    audit(actor,app,'extend_treatment',before,{'ends_at':inputs['ends'].isoformat(),'version':app.version},reason)
    return app


@transaction.atomic
def appointment_action(actor, pk, action, reason=''):
    source=Appointment.objects.get(pk=pk)
    Patient.objects.select_for_update().get(pk=source.patient_id)
    Dentist.objects.select_for_update().get(pk=source.slot.dentist_id)
    app = Appointment.objects.select_for_update().get(pk=pk)
    now = timezone.now()
    before = {'status': app.status}
    if action=='checkin' and app.status=='arrived':
        access.require(actor,['reception'],app.slot.clinic_id)
        return app
    if action in ['undo_checkin','undo_no_show']:
        access.require(actor,['reception'],app.slot.clinic_id)
        check(bool(reason.strip()),'请填写核验及纠错依据。')
        check(not Visit.objects.filter(appointment=app).exists(),'已经接诊，须通过受控医疗记录更正处理。')
        check(app.status==('arrived' if action=='undo_checkin' else 'no_show'),'状态不符合纠错操作。')
        if action=='undo_no_show':
            from . import reservations
            inputs=reservations.planning_inputs(app.slot,old_plan=app.plan)
            slots=reservations.validate_plan(app.slot,app.patient,inputs,app.pk)
            app.version+=1
            reservations.persist_plan(app,slots,inputs)
        app.status='booked'
        app.checked_in_at=None
        if action=='undo_checkin': app.version+=1
        app.save()
        audit(actor,app,action,before,{'status':app.status},reason)
        return app
    check(app.status == 'booked', '预约状态已变化，请刷新后重试。')
    if action == 'cancel':
        check(bool(reason.strip()), '取消原因不能为空。')
        check(app.slot.starts_at > now, '预约已经开始，不能普通取消，请联系前台核实。')
        if access.allowed(actor, ['reception'], app.slot.clinic_id):
            party = 'reception'
        else:
            check(access.has_role(actor, 'patient') and app.patient.user_id == actor.pk, '不能取消其他患者的预约。')
            check(app.slot.starts_at - now >= timedelta(hours=2), '不足2小时，请联系前台处理。')
            party = 'patient'
        app.status, app.cancelled_at, app.cancel_reason, app.cancel_party = 'cancelled', now, reason, party
    else:
        access.require(actor, ['reception'], app.slot.clinic_id)
        if action == 'checkin':
            check(timezone.localtime(app.slot.starts_at).date() == timezone.localdate(), '仅可办理当天预约签到。')
            app.status, app.checked_in_at = 'arrived', now
        elif action == 'no_show':
            check(app.ends_at <= now and bool(reason.strip()), '请在时段结束后核实未到诊情况并填写原因。')
            app.status = 'no_show'
        else:
            raise ValidationError('未知预约操作。')
    if app.status in ['cancelled','no_show']:
        AppointmentSlot.objects.filter(appointment=app,is_active=True).update(is_active=False)
        ResourceOccupancy.objects.filter(appointment=app,is_active=True).update(is_active=False)
    app.version += 1
    app.save()
    audit(actor, app, action, before, {'status': app.status}, reason)
    return app


@transaction.atomic
def start_visit(actor, pk):
    app = Appointment.objects.select_for_update().get(pk=pk)
    if not access.own_doctor(actor, app):
        raise PermissionDenied('只能接诊本人预约。')
    previous = Visit.objects.filter(appointment=app).first()
    if previous:
        return previous
    check(app.status == 'arrived', '请先由前台完成签到。')
    from .reservations import check_capacity
    for occupancy in app.resource_occupancies.filter(is_active=True).order_by('resource_id'):
        resource=ClinicalResource.objects.select_for_update().get(pk=occupancy.resource_id)
        check_capacity(resource,occupancy.starts_at,occupancy.ends_at,occupancy.quantity,app.pk)
    visit = Visit.objects.create(appointment=app, dentist=app.slot.dentist, started_at=timezone.now())
    audit(actor, visit, 'start_visit')
    return visit


def doctor_visit(actor, pk, version=None):
    visit = Visit.objects.select_for_update().get(pk=pk)
    if not access.own_doctor(actor, visit.appointment):
        raise PermissionDenied('只能修改本人接诊记录。')
    if version is not None:
        check(visit.current_version == int(version), '记录已由其他窗口更新，请刷新后重试。')
    return visit


def add_line(visit, treatment_id, quantity):
    treatment = TreatmentType.objects.select_for_update().get(pk=treatment_id, active=True)
    q = Decimal(quantity)
    check(q.is_finite() and q > 0 and q <= Decimal('999999.99') and q == money(q), '数量须为正数，最多保留两位小数。')
    return VisitTreatment.objects.create(visit=visit, treatment_type=treatment,
        name_snapshot=treatment.name, quantity=q, unit_price_snapshot=treatment.catalog_price,
        subtotal=money(q*treatment.catalog_price))


@transaction.atomic
def edit_visit(actor, pk, action, data):
    visit = doctor_visit(actor, pk, data['version'])
    check(visit.status == 'in_progress', '就诊已完成，请提交更正申请。')
    before = {'version': visit.current_version, 'clinical_notes': visit.clinical_notes, 'special_needs_notes': visit.special_needs_notes}
    if action == 'notes':
        visit.clinical_notes = data['clinical_notes']
        visit.special_needs_notes = data['special_needs_notes']
    elif action == 'add_treatment':
        line = add_line(visit, data['treatment'].pk, data['quantity'])
        before['added_line'] = str(line.pk)
    elif action == 'remove_treatment':
        line = VisitTreatment.objects.get(pk=data['line_id'], visit=visit, is_active=True)
        line.is_active = False
        line.save()
        before['removed_line'] = str(line.pk)
    elif action == 'complete':
        check(bool(visit.clinical_notes.strip()), '请先保存诊疗说明。')
        check(visit.treatments.filter(is_active=True).exists(), '至少添加一项治疗后才能完成。')
        visit.status, visit.completed_at = 'completed', timezone.now()
        app = Appointment.objects.select_for_update().get(pk=visit.appointment_id)
        check(app.status == 'arrived', '预约状态异常，请核查。')
        app.status = 'completed'
        app.version += 1
        app.save()
    else:
        raise ValidationError('未知就诊操作。')
    visit.current_version += 1
    visit.save()
    audit(actor, visit, action, before, {'version': visit.current_version, 'clinical_notes': visit.clinical_notes, 'special_needs_notes': visit.special_needs_notes, 'status': visit.status})
    return visit


@transaction.atomic
def issue_invoice(actor, pk, version):
    visit = Visit.objects.select_for_update().get(pk=pk)
    access.require(actor, ['reception', 'finance'], visit.appointment.slot.clinic_id)
    check(visit.status == 'completed', '就诊尚未完成。')
    check(visit.current_version == int(version), '就诊版本已更新，请重新核对。')
    check(not VisitAmendment.objects.filter(visit=visit, status='pending').exists(), '更正审批中，暂不能出单。')
    existing = visit.invoices.exclude(status='void').first()
    if existing:
        return existing
    lines = list(visit.treatments.filter(is_active=True))
    check(bool(lines), '缺少治疗明细，不能出单。')
    total = sum((line.subtotal for line in lines), Decimal('0'))
    previous = visit.invoices.filter(status='void', replacement__isnull=True).order_by('-issued_at').first()
    inv = Invoice.objects.create(number=number('INV'), visit=visit, source_visit_version=visit.current_version,
        replaces=previous, total=total, issued_by=actor, issued_at=timezone.now())
    for line in lines:
        check(line.subtotal == money(line.quantity*line.unit_price_snapshot), '治疗明细金额异常。')
        InvoiceLine.objects.create(invoice=inv, visit_treatment=line, name_snapshot=line.name_snapshot,
            quantity_snapshot=line.quantity, unit_price_snapshot=line.unit_price_snapshot, subtotal=line.subtotal)
    audit(actor, inv, 'issue', after={'total': str(total), 'version': visit.current_version})
    return inv


@transaction.atomic
def invoice_action(actor, pk, action, data):
    # All settlement/amendment commands acquire visit before invoice, in this order.
    visit_id = Invoice.objects.values_list('visit_id', flat=True).get(pk=pk)
    visit = Visit.objects.select_for_update().get(pk=visit_id)
    inv = Invoice.objects.select_for_update().get(pk=pk)
    access.require(actor, ['reception', 'finance'], visit.appointment.slot.clinic_id)
    check(inv.status == 'issued', '仅可操作尚未支付的已出单单据。')
    if action == 'void':
        check(bool(data['reason'].strip()), '请填写作废原因。')
        inv.status, inv.voided_by, inv.voided_at, inv.void_reason = 'void', actor, timezone.now(), data['reason']
    elif action == 'pay':
        check(not VisitAmendment.objects.filter(visit=visit, status='pending').exists(), '更正处理中，暂不能付款。')
        check(data['amount'] == inv.total, '模拟支付金额必须等于应付总额。')
        check(data['method'] in ['模拟现金', '模拟银行卡', '模拟移动支付'], '请选择有效支付方式。')
        PaymentRecord.objects.create(invoice=inv, method=data['method'], amount=inv.total, status='success',
            succeeded_at=timezone.now(), recorded_by=actor, request_reference=number('PAY'))
        inv.status = 'paid'
    else:
        raise ValidationError('未知结算操作。')
    inv.save()
    audit(actor, inv, action, {'status': 'issued'}, {'status': inv.status}, data.get('reason', ''))
    return inv


@transaction.atomic
def request_amendment(actor, pk, data):
    visit = doctor_visit(actor, pk, data['version'])
    check(visit.status == 'completed', '进行中的就诊请直接编辑。')
    check(not VisitAmendment.objects.filter(visit=visit, status='pending').exists(), '已有待审批申请。')
    changes = {'clinical_notes': {'before': visit.clinical_notes, 'after': data['clinical_notes']}}
    if data.get('treatment'):
        check(not visit.invoices.filter(status='paid').exists(), '已支付后不能修改收费明细。')
        check(not visit.invoices.filter(status='issued').exists(), '请先由前台或财务作废未支付单据，再提交收费更正。')
        treatment = TreatmentType.objects.select_for_update().get(pk=data['treatment'].pk, active=True)
        changes['replacement_treatment'] = {'id': str(treatment.pk), 'name': treatment.name,
            'price': str(treatment.catalog_price), 'quantity': str(data['quantity'])}
        changes['previous_treatments'] = [{'id': str(l.pk), 'name': l.name_snapshot, 'quantity': str(l.quantity),
            'price': str(l.unit_price_snapshot), 'subtotal': str(l.subtotal)} for l in visit.treatments.filter(is_active=True)]
    amendment = VisitAmendment.objects.create(visit=visit, base_version=visit.current_version,
        proposed_changes=changes, reason=data['reason'], applicant=actor)
    audit(actor, amendment, 'request_amendment', after=changes, reason=data['reason'])
    return amendment


@transaction.atomic
def review_amendment(actor, pk, approve, reason):
    visit_id = VisitAmendment.objects.values_list('visit_id', flat=True).get(pk=pk)
    visit = Visit.objects.select_for_update().get(pk=visit_id)
    amendment = VisitAmendment.objects.select_for_update().get(pk=pk)
    access.require(actor, ['manager'], visit.appointment.slot.clinic_id)
    check(actor.pk != amendment.applicant_id, '申请人不能审批自己的更正。')
    check(amendment.status == 'pending', '该申请已经处理。')
    check(bool(reason.strip()), '请填写审批意见。')
    if approve:
        check(visit.current_version == amendment.base_version, '版本已变化，请驳回并让医生重新申请。')
        changes = amendment.proposed_changes
        replacement = changes.get('replacement_treatment')
        if replacement:
            check(not visit.invoices.exclude(status='void').exists(), '存在有效单据，不能批准收费更正。')
            visit.treatments.filter(is_active=True).update(is_active=False)
            q, price = Decimal(replacement['quantity']), Decimal(replacement['price'])
            VisitTreatment.objects.create(visit=visit, treatment_type_id=replacement['id'],
                name_snapshot=replacement['name'], quantity=q, unit_price_snapshot=price, subtotal=money(q*price))
        old = visit.clinical_notes
        visit.clinical_notes = changes['clinical_notes']['after']
        visit.current_version += 1
        visit.save()
        amendment.effective_version = visit.current_version
        audit(actor, visit, 'amendment_effective', {'clinical_notes': old, 'version': amendment.base_version},
            {'clinical_notes': visit.clinical_notes, 'version': visit.current_version}, amendment.reason)
    amendment.status = 'approved' if approve else 'rejected'
    amendment.reviewer, amendment.decided_at, amendment.decision_reason = actor, timezone.now(), reason
    amendment.save()
    audit(actor, amendment, 'review_amendment', after={'status': amendment.status}, reason=reason)
    return amendment


@transaction.atomic
def schedule(actor, service, starts_at):
    service = DentistClinic.objects.select_for_update().get(pk=service.pk)
    access.require(actor, ['manager'], service.clinic_id)
    Dentist.objects.select_for_update().get(pk=service.dentist_id)
    check(starts_at > timezone.now(), '只能新增未来排班。')
    slot = ScheduleSlot(service=service, clinic=service.clinic, dentist=service.dentist,
        starts_at=starts_at, ends_at=starts_at+timedelta(minutes=30))
    valid_slot(slot)
    check(not ScheduleSlot.objects.filter(dentist=slot.dentist, active=True,
        starts_at__lt=slot.ends_at, ends_at__gt=slot.starts_at).exists(), '医生已有重叠排班（可能在其他诊所）。')
    slot.save()
    audit(actor, slot, 'create_schedule', after={'starts_at': starts_at.isoformat()})
    return slot
