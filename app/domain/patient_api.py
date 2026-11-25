import json
from datetime import date
from uuid import UUID

from django.contrib.auth import authenticate
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import OuterRef, Subquery
from django.http import JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from . import access, forms, identity, reservations, services
from .models import Appointment, Clinic, Invoice, Patient, User, Visit


TOKEN_SALT = 'domain.patient_api.session.v1'
TOKEN_LIFETIME = 60 * 60


def problem(message, status):
    return JsonResponse({'error': message}, status=status)


def json_object(request):
    if request.content_type != 'application/json':
        raise ValueError('Expected application/json')
    payload = json.loads(request.body)
    if not isinstance(payload, dict):
        raise ValueError('Expected a JSON object')
    return payload


def private_transport(request):
    # The local simulator also reaches Gunicorn through a loopback SSH forward.
    return request.is_secure() or (
        request.META.get('REMOTE_ADDR') == '127.0.0.1'
        and request.get_host().split(':', 1)[0] in {'127.0.0.1', 'localhost'}
    )


def active_patient(user):
    if user is None or not access.has_role(user, 'patient'):
        return None
    return Patient.objects.filter(user=user, active=True).first()


def authenticated_patient(request):
    scheme, _, token = request.headers.get('Authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token:
        return None
    try:
        identity = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_LIFETIME)
    except signing.BadSignature:
        return None
    user = User.objects.filter(pk=identity['uid'], is_active=True).first()
    if user is None or not constant_time_compare(identity['auth'], user.get_session_auth_hash()):
        return None
    return active_patient(user)


@require_safe
def public_clinics(request):
    clinics = Clinic.objects.filter(active=True).order_by('code').values(
        'id', 'code', 'name', 'address', 'phone'
    )
    return JsonResponse({'data': list(clinics)})


def patient_session(user, status=200):
    token = signing.dumps({'uid': str(user.pk), 'auth': user.get_session_auth_hash()}, salt=TOKEN_SALT)
    return JsonResponse({'data': {'token': token, 'expires_in': TOKEN_LIFETIME}}, status=status)


@csrf_exempt  # This JSON API does not establish a Django browser session.
@require_POST
@never_cache
def patient_register(request):
    if not private_transport(request):
        return problem('请使用 HTTPS 连接后注册。', 403)
    try:
        payload = json_object(request)
        fields = ('username', 'password1', 'password2', 'name', 'birth_date', 'phone', 'billing_address', 'email')
        data = {key: payload.get(key, '') for key in fields}
        if any(not isinstance(value, str) or len(value) > 4096 for value in data.values()):
            raise ValueError('Invalid registration form')
    except (UnicodeError, ValueError):
        return problem('注册资料格式有误，请核对后重试。', 400)

    patient = forms.PatientForm(data)
    account = forms.RegistrationForm(data)
    for form in (patient, account):
        if not form.is_valid():
            field, errors = next(iter(form.errors.items()))
            label = form[field].label if field in form.fields else '注册资料'
            return problem(f'{label}：{errors[0]}', 400)
    try:
        user = identity.register_patient(account, patient, reject_existing=True)
    except identity.RegistrationConflict as error:
        return problem(error.messages[0], 409)
    except IntegrityError:
        return problem('账号已存在或资料有冲突，请登录或联系诊所前台核对。', 409)
    return patient_session(user, status=201)


@csrf_exempt  # This API neither creates nor accepts a Django browser session cookie.
@require_POST
@never_cache
def patient_login(request):
    if not private_transport(request):
        return problem('请使用 HTTPS 连接后登录。', 403)
    try:
        payload = json_object(request)
        username, password = payload.get('username'), payload.get('password')
        if not isinstance(username, str) or not 0 < len(username.strip()) <= 150:
            raise ValueError('Invalid username')
        if not isinstance(password, str) or not 0 < len(password) <= 4096:
            raise ValueError('Invalid password')
    except (UnicodeError, ValueError):
        return problem('请输入有效的患者账号和密码。', 400)
    user = authenticate(request, username=username.strip(), password=password)
    if active_patient(user) is None:
        return problem('患者账号或密码有误，或账号未启用。', 401)
    return patient_session(user)


@require_safe
@never_cache
def available_slots(request):
    if not private_transport(request):
        return problem('请使用 HTTPS 连接后查询。', 403)
    patient = authenticated_patient(request)
    if patient is None:
        return problem('请先登录患者账号。', 401)
    try:
        clinic = UUID(request.GET.get('clinic', ''))
        day = date.fromisoformat(request.GET['date']) if request.GET.get('date') else None
    except ValueError:
        return problem('诊所或日期无效。', 400)
    slots = access.slots(patient.user).filter(
        clinic_id=clinic, starts_at__gt=timezone.now(), service__active=True,
    ).exclude(reservation_links__is_active=True).order_by('starts_at', 'pk')
    if day is not None:
        slots = slots.filter(starts_at__date=day)
    data = []
    candidates = list(slots.select_related('clinic', 'dentist', 'service')[:90])
    for slot in reservations.available_consultations(candidates, patient):
        start = timezone.localtime(slot.starts_at)
        end = timezone.localtime(slot.ends_at)
        data.append({'id': str(slot.pk), 'dentist': slot.dentist.name,
                     'date': start.date().isoformat(), 'time': f'{start:%H:%M}–{end:%H:%M}'})
        if len(data) == 30:
            break
    return JsonResponse({'data': data})


def appointment_summary(appointment, now):
    slot = appointment.slot
    start = timezone.localtime(slot.starts_at)
    end = timezone.localtime(appointment.ends_at)
    booked = appointment.status == Appointment.Status.BOOKED
    visit = getattr(appointment, 'visit', None)
    return {
        'id': str(appointment.pk), 'number': appointment.number,
        'clinic': slot.clinic.name, 'dentist': slot.dentist.name,
        'date': start.date().isoformat(), 'time': f'{start:%H:%M}–{end:%H:%M}',
        'status': appointment.get_status_display(), 'status_code': appointment.status,
        'can_cancel': booked and slot.starts_at - now >= services.PATIENT_CANCEL_LEAD,
        'cancel_reason': appointment.cancel_reason if appointment.status == Appointment.Status.CANCELLED else '',
        'has_completed_visit': visit is not None and visit.status == 'completed',
    }


def list_appointments(request, patient):
    booking_status = services.booking_allergy_results(OuterRef('pk'))[:1]
    appointments = Appointment.objects.filter(patient=patient).select_related(
        'slot__clinic', 'slot__dentist', 'plan', 'visit',
    ).annotate(booking_allergy_status=Subquery(booking_status)).order_by('-slot__starts_at', '-pk')
    page = Paginator(appointments, 20).get_page(request.GET.get('page'))
    now = timezone.now()
    return JsonResponse({'data': {
        'items': [appointment_summary(appointment, now) | {
            'allergy_confirmation': services.allergy_confirmation_label(appointment.booking_allergy_status),
        } for appointment in page],
        'next_page': page.next_page_number() if page.has_next() else None,
    }})


@csrf_exempt  # A same-origin web cookie cannot authorize a booking here.
@require_http_methods(['GET', 'HEAD', 'POST'])
@never_cache
def patient_appointments(request):
    if not private_transport(request):
        return problem('请使用 HTTPS 连接后操作。', 403)
    patient = authenticated_patient(request)
    if patient is None:
        return problem('请先登录患者账号。', 401)
    if request.method in ('GET', 'HEAD'):
        return list_appointments(request, patient)
    try:
        payload = json_object(request)
        slot = UUID(payload['slot'])
        key = UUID(payload['request_key'])
        reason = payload.get('reason', '')
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError('Invalid reason')
    except (KeyError, TypeError, AttributeError, ValueError):
        return problem('预约时段、请求标识或预约原因无效。', 400)

    actor = patient.user
    command_payload = {'slot': str(slot), 'reason': reason}
    allergy_status = payload.get('allergy_status')
    if 'allergy_status' in payload:
        command_payload['allergy_status'] = allergy_status
    try:
        appointment_id = services.command(
            actor, 'miniprogram_book', str(key), command_payload,
            lambda: services.book(actor, patient.pk, slot, reason, allergy_status=allergy_status),
        )
        appointment = Appointment.objects.get(pk=appointment_id, patient=patient)
    except ValidationError as exc:
        return problem(exc.messages[0], 409)
    except IntegrityError:
        return problem('预约时段已被占用，请重新查询。', 409)
    except ObjectDoesNotExist:
        return problem('预约时段已失效，请重新查询。', 409)
    return JsonResponse({'data': {'number': appointment.number, 'status': appointment.get_status_display()}})


@csrf_exempt  # The bearer token identifies the patient independently of web cookies.
@require_POST
@never_cache
def cancel_appointment(request, pk):
    if not private_transport(request):
        return problem('请使用 HTTPS 连接后操作。', 403)
    patient = authenticated_patient(request)
    if patient is None:
        return problem('请先登录患者账号。', 401)
    appointments = Appointment.objects.filter(patient=patient)
    if not appointments.filter(pk=pk).exists():
        return problem('未找到该预约。', 404)
    try:
        payload = json_object(request)
        key = UUID(payload['request_key'])
        reason = payload.get('reason')
        if not isinstance(reason, str) or not 0 < len(reason.strip()) <= 2000:
            raise ValueError('Invalid reason')
    except (KeyError, TypeError, AttributeError, ValueError):
        return problem('请填写取消原因并重试。', 400)

    actor = patient.user
    reason = reason.strip()
    try:
        services.command(
            actor, 'miniprogram_cancel', str(key), {'appointment': str(pk), 'reason': reason},
            lambda: services.appointment_action(actor, pk, 'cancel', reason, patient_only=True),
        )
        appointment = appointments.select_related('slot__clinic', 'slot__dentist', 'plan', 'visit').get(pk=pk)
    except ValidationError as exc:
        return problem(exc.messages[0], 409)
    except ObjectDoesNotExist:
        return problem('未找到该预约。', 404)
    return JsonResponse({'data': appointment_summary(appointment, timezone.now())})


def local_timestamp(value):
    return timezone.localtime(value).strftime('%Y-%m-%d %H:%M') if value else None


def invoice_summary(invoice):
    return {
        'id': str(invoice.pk), 'number': invoice.number, 'status': invoice.get_status_display(),
        'total': str(invoice.total), 'issued_at': local_timestamp(invoice.issued_at),
    }


@require_safe
@never_cache
def patient_visit(request, pk):
    if not private_transport(request):
        return problem('请使用 HTTPS 连接后查询。', 403)
    patient = authenticated_patient(request)
    if patient is None:
        return problem('请先登录患者账号。', 401)
    visit = Visit.objects.filter(appointment_id=pk, appointment__patient=patient, status='completed').select_related(
        'appointment__slot__clinic', 'dentist',
    ).first()
    if visit is None:
        return problem('暂无可查看的已完成就诊记录。', 404)
    if request.method == 'GET':
        services.audit(patient.user, visit, 'view_visit')
    lines = visit.treatments.filter(is_active=True).order_by('created_at', 'pk').values(
        'id', 'name_snapshot', 'quantity', 'unit_price_snapshot', 'subtotal',
    )
    return JsonResponse({'data': {
        'appointment_number': visit.appointment.number,
        'clinic': visit.appointment.slot.clinic.name, 'dentist': visit.dentist.name,
        'started_at': local_timestamp(visit.started_at), 'completed_at': local_timestamp(visit.completed_at),
        'status': visit.get_status_display(), 'treatments': list(lines),
        'invoices': [invoice_summary(invoice) for invoice in visit.invoices.order_by('-issued_at', '-pk')],
    }})


@require_safe
@never_cache
def patient_invoice(request, pk):
    if not private_transport(request):
        return problem('请使用 HTTPS 连接后查询。', 403)
    patient = authenticated_patient(request)
    if patient is None:
        return problem('请先登录患者账号。', 401)
    invoice = Invoice.objects.filter(pk=pk, visit__appointment__patient=patient, visit__status='completed').first()
    if invoice is None:
        return problem('未找到该结算单。', 404)
    if request.method == 'GET':
        services.audit(patient.user, invoice, 'view_invoice')
    lines = invoice.lines.order_by('created_at', 'pk').values(
        'id', 'name_snapshot', 'quantity_snapshot', 'unit_price_snapshot', 'subtotal',
    )
    payment = invoice.payments.filter(status='success').first()
    return JsonResponse({'data': {
        **invoice_summary(invoice), 'lines': list(lines),
        'payment': {'amount': str(payment.amount), 'method': payment.method,
                    'succeeded_at': local_timestamp(payment.succeeded_at)} if payment else None,
    }})
