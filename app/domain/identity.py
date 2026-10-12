"""Explicit verification; phone lookup never merges or transfers patient history."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from . import access
from .models import Patient,User,Role,RoleAssignment,AuditEvent


def scoped(actor,patient):
    from .views import editable_patients
    access.require(actor,['reception'])
    if not editable_patients(actor).filter(pk=patient.pk).exists():
        raise PermissionDenied('只能核验本诊所已接诊或由本人建档的患者。')


@transaction.atomic
def verify(actor,pk,evidence,username='',password=''):
    from .services import check,audit
    patient=Patient.objects.select_for_update().get(pk=pk)
    scoped(actor,patient)
    check(bool(evidence.strip()),'请记录本人核验依据。')
    before={'user':str(patient.user_id),'verified_at':str(patient.identity_verified_at)}
    if username:
        if password:
            from django.contrib.auth.password_validation import validate_password
            check(not User.objects.filter(username=username).exists(),'登录账号已存在，不能重复创建。')
            user=User(username=username)
            validate_password(password,user=user)
            user.set_password(password)
            user.save()
        else:
            user=User.objects.select_for_update().get(username=username,is_active=True)
        check(patient.user_id in [None,user.pk],'患者已关联其他账号，不能直接替换。')
        check(not Patient.objects.filter(user=user).exclude(pk=patient.pk).exists(),'该账号已关联其他患者，不能直接合并档案。')
        check(not RoleAssignment.objects.filter(user=user).exclude(role__code='patient').exists(),'工作人员账号不能绑定为患者自助账号。')
        patient.user=user
        RoleAssignment.objects.get_or_create(user=user,role=Role.objects.get(code='patient'),scope_type='self',clinic=None,valid_to=None,defaults={'valid_from':timezone.now()})
    patient.identity_verified_at=timezone.now()
    patient.identity_verified_by=actor
    patient.save()
    audit(actor,patient,'verify_identity',before,{'user':str(patient.user_id),'verified_at':patient.identity_verified_at.isoformat()},evidence)
    return patient


@transaction.atomic
def change_phone(actor,pk,phone,evidence):
    from .services import check,audit
    patient=Patient.objects.select_for_update().get(pk=pk)
    scoped(actor,patient)
    check(bool(evidence.strip()),'请记录本人核验依据。')
    check(bool(phone.strip()),'手机号不能为空。')
    before={'phone':patient.phone}
    patient.phone=phone.strip()
    patient.identity_verified_at=timezone.now()
    patient.identity_verified_by=actor
    patient.save()
    audit(actor,patient,'change_phone',before,{'phone':patient.phone,'same_phone_hint':Patient.objects.filter(phone=patient.phone).exclude(pk=pk).exists()},evidence)
    return patient
