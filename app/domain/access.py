"""Server-side role and scope policy; technical administrators have no clinical bypass."""
from django.db.models import Q
from django.utils import timezone
from django.core.exceptions import PermissionDenied
from .models import RoleAssignment, Appointment, Clinic, ScheduleSlot


def assignments(user, roles=None):
    if not user.is_authenticated or not user.is_active:
        return RoleAssignment.objects.none()
    now = timezone.now()
    qs = RoleAssignment.objects.filter(user=user, valid_from__lte=now).filter(Q(valid_to__isnull=True) | Q(valid_to__gt=now))
    return qs.filter(role__code__in=roles) if roles else qs


def has_role(user, *roles):
    return assignments(user, roles).exists()


def clinic_ids(user, roles):
    rows = assignments(user, roles)
    if rows.filter(scope_type='global').exists():
        return Clinic.objects.values_list('pk', flat=True)
    return rows.filter(scope_type='clinic').values_list('clinic_id', flat=True)


def allowed(user, roles, clinic_id):
    return assignments(user, roles).filter(Q(scope_type='global') | Q(scope_type='clinic', clinic_id=clinic_id)).exists()


def require(user, roles, clinic_id=None):
    ok = has_role(user, *roles) if clinic_id is None else allowed(user, roles, clinic_id)
    if not ok:
        raise PermissionDenied('当前账号没有执行此操作的权限。')


def appointments(user):
    condition = Q(pk__in=[])
    if has_role(user, 'patient'):
        condition |= Q(patient__user=user)
    if has_role(user, 'dentist'):
        condition |= Q(slot__dentist__user=user)
    condition |= Q(slot__clinic_id__in=clinic_ids(user, ['reception', 'manager', 'headquarters', 'finance']))
    return Appointment.objects.filter(condition).select_related('patient', 'slot__clinic', 'slot__dentist', 'visit').distinct()


def slots(user):
    qs = ScheduleSlot.objects.select_related('clinic', 'dentist', 'service')
    if has_role(user, 'patient'):
        return qs.filter(active=True, clinic__active=True, dentist__active=True)
    condition = Q(clinic_id__in=clinic_ids(user, ['reception', 'manager', 'headquarters']))
    if has_role(user, 'dentist'):
        condition |= Q(dentist__user=user)
    return qs.filter(condition).distinct()


def own_doctor(user, appointment):
    return has_role(user, 'dentist') and appointment.slot.dentist.user_id == user.pk
