"""Role-scoped operation availability for detail and confirmation pages."""
from dataclasses import dataclass
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.utils import timezone
from . import access
from .models import VisitAmendment


@dataclass(frozen=True)
class ActionState:
    code: str
    label: str
    authorized: bool
    reason: str = ''
    primary: bool = False
    visible: bool = True

    @property
    def enabled(self):
        return self.authorized and not self.reason


def appointment_actions(actor, appointment, visit):
    now = timezone.now()
    reception = access.allowed(actor, ['reception'], appointment.slot.clinic_id)
    patient = access.has_role(actor, 'patient') and appointment.patient.user_id == actor.pk
    doctor = access.own_doctor(actor, appointment)
    booked = appointment.status == 'booked'
    cancel_reason = ''
    if not booked:
        cancel_reason = '当前预约不是待到诊状态，不能普通取消。'
    elif appointment.slot.starts_at <= now:
        cancel_reason = '预约已经开始，请联系前台核实。'
    elif patient and not reception and appointment.slot.starts_at - now < timedelta(hours=2):
        cancel_reason = '距离预约开始不足2小时，请联系前台处理。'
    reschedule_reason = '' if booked and appointment.slot.starts_at > now else '仅可调整尚未开始的待到诊预约。'
    checkin_reason = ''
    if not booked:
        checkin_reason = '当前预约不是待到诊状态，无须重复签到。'
    elif timezone.localtime(appointment.slot.starts_at).date() != timezone.localdate():
        checkin_reason = '仅可办理当天预约签到。'
    no_show_reason = ''
    if not booked:
        no_show_reason = '仅可核实待到诊预约的未到诊情况。'
    elif appointment.ends_at > now:
        no_show_reason = '请在预约预计治疗结束后核实是否爽约。'
    start_reason = ''
    if visit is not None:
        if visit.status == 'completed':
            start_reason = '就诊已完成，请查看对应就诊记录。'
    elif appointment.status != 'arrived':
        start_reason = '请先由前台完成签到。'
    elif doctor:
        from .reservations import check_capacity
        try:
            for occupancy in appointment.resource_occupancies.filter(is_active=True).select_related('resource'):
                check_capacity(occupancy.resource, occupancy.starts_at, occupancy.ends_at,
                               occupancy.quantity, appointment.pk)
        except ValidationError as exc:
            start_reason = '；'.join(exc.messages)
    correction_reason = '已经接诊，须通过受控医疗记录更正处理。' if visit is not None else ''
    return {
        'checkin': ActionState('checkin', '到诊签到', reception, checkin_reason, primary=True),
        'no_show': ActionState('no_show', '核实爽约', reception, no_show_reason),
        'reschedule': ActionState('reschedule', '调整预约', reception, reschedule_reason),
        'cancel': ActionState('cancel', '取消预约', reception or patient, cancel_reason),
        'start': ActionState('start', '开始 / 继续接诊', doctor, start_reason, primary=True),
        'extend': ActionState('extend', '延长治疗安排', reception or doctor,
                              '' if appointment.status in ['booked', 'arrived'] else '仅可延长待到诊或进行中的预约。'),
        'undo_checkin': ActionState('undo_checkin', '纠正误签到', reception,
                                    correction_reason or ('' if appointment.status == 'arrived' else '仅可纠正已到诊预约的误签到。'),
                                    visible=appointment.status == 'arrived'),
        'undo_no_show': ActionState('undo_no_show', '纠正爽约记录', reception,
                                    correction_reason or ('' if appointment.status == 'no_show' else '仅可纠正已核实的爽约记录。'),
                                    visible=appointment.status == 'no_show'),
    }


def visit_actions(actor, visit):
    doctor = access.own_doctor(actor, visit.appointment)
    billing = access.allowed(actor, ['reception', 'finance'], visit.appointment.slot.clinic_id)
    active_lines = visit.treatments.filter(is_active=True).exists()
    pending = VisitAmendment.objects.filter(visit=visit, status='pending').exists()
    edit_reason = '' if visit.status == 'in_progress' else '就诊已完成，请通过更正申请处理。'
    complete_reason = edit_reason
    if not complete_reason:
        if not visit.clinical_notes.strip():
            complete_reason = '请先保存诊疗说明。'
        elif not active_lines:
            complete_reason = '至少添加一项治疗后才能完成就诊。'
        elif visit.appointment.status != 'arrived':
            complete_reason = '预约状态已变化，请核查到诊记录。'
    amend_reason = ''
    if visit.status != 'completed':
        amend_reason = '就诊进行中，请直接保存诊疗草稿。'
    elif pending:
        amend_reason = '已有待审批更正申请，请等待处理。'
    issue_reason = ''
    if visit.status != 'completed':
        issue_reason = '就诊完成后才能出单。'
    elif pending:
        issue_reason = '更正审批中，暂不能出单。'
    elif visit.invoices.exclude(status='void').exists():
        issue_reason = '已有有效结算单，请查看结算记录。'
    elif not active_lines:
        issue_reason = '缺少治疗明细，不能出单。'
    return {
        'notes': ActionState('notes', '编辑并保存草稿', doctor, edit_reason, primary=True),
        'amend': ActionState('amend', '申请更正', doctor, amend_reason),
        'add_treatment': ActionState('add_treatment', '添加治疗', doctor, edit_reason),
        'remove_treatment': ActionState('remove_treatment', '移除明细', doctor,
                                        edit_reason or ('' if active_lines else '当前没有可移除的治疗明细。')),
        'complete': ActionState('complete', '完成就诊', doctor, complete_reason, primary=True),
        'issue': ActionState('issue', '核对并出单', billing, issue_reason, primary=True),
    }


def invoice_actions(actor, invoice):
    billing = access.allowed(actor, ['reception', 'finance'], invoice.visit.appointment.slot.clinic_id)
    state_reason = ''
    if invoice.status == 'paid':
        state_reason = '结算单已支付，不能重复收款或普通作废。'
    elif invoice.status == 'void':
        state_reason = '结算单已作废，不能继续收款或重复作废。'
    elif invoice.status != 'issued':
        state_reason = '仅可办理尚未支付的已出单单据。'
    pay_reason = state_reason
    if not pay_reason and VisitAmendment.objects.filter(visit=invoice.visit, status='pending').exists():
        pay_reason = '更正审批中，暂不能收款。'
    return {
        'pay': ActionState('pay', '模拟全额收款', billing, pay_reason, primary=True),
        'void': ActionState('void', '作废未支付单据', billing, state_reason),
    }
