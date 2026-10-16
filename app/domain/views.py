import uuid
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlencode
from django import forms as django_forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError, ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from . import access, services, forms, action_states
from .models import *


def operation_controls(states, route, pk):
    controls = []
    for state in states.values():
        url = reverse('reschedule', args=[pk]) if state.code == 'reschedule' else reverse(route, args=[pk, state.code])
        controls.append({'code': state.code, 'label': state.label, 'authorized': state.authorized,
                         'enabled': state.enabled, 'reason': state.reason, 'primary': state.primary,
                         'visible': state.visible, 'url': url})
    return controls


def layout(request):
    active_assignments=list(access.assignments(request.user).select_related('role','clinic'))
    roles={a.role.code for a in active_assignments}
    clinics={a.clinic_id:a.clinic for a in active_assignments if a.clinic_id}
    clinic=next(iter(clinics.values())) if len(clinics)==1 else None
    local_day=timezone.localdate()
    role_titles=[('reception','前台'),('dentist','医生'),('manager','诊所管理'),('patient','患者'),('finance','财务'),('headquarters','总部'),('administrator','账号管理')]
    workspace_role=next((name for code,name in role_titles if code in roles),'工作台')
    role_summary=' / '.join(dict.fromkeys(a.role.name for a in active_assignments))
    hours=ClinicHours.objects.filter(clinic=clinic,weekday=local_day.isoweekday()).order_by('opens_at') if clinic else ClinicHours.objects.none()
    return {'primary_clinic':clinic,'clinic_badge':clinic.code.removeprefix('DEMO-') if clinic else 'ADC','workspace_role':workspace_role,'workspace_title':('前台接诊台' if workspace_role=='前台' else workspace_role+'工作台') if roles else '工作台','role_summary':role_summary,'today_date':local_day,'clinic_hours':hours,
        'scope_label':clinic.name if clinic else ('本人服务' if 'patient' in roles else '连锁范围' if roles else '账号登录'),'roles': roles, 'is_patient': 'patient' in roles, 'is_reception': 'reception' in roles,
        'is_dentist': 'dentist' in roles, 'can_schedule': 'manager' in roles,
        'can_report': bool(roles & {'manager','headquarters','finance','dentist'}),
        'can_finance': 'finance' in roles, 'can_admin': 'administrator' in roles,
        'can_master': bool(roles & {'manager','headquarters','administrator'}),
        'can_patients': bool(roles & {'patient','reception'}),
        'can_appointments': bool(roles & {'patient','reception','dentist','manager','headquarters','finance'}),
        'request_key': str(uuid.uuid4())}


def page(request, template, data=None):
    return render(request, 'domain/'+template+'.html', {**layout(request), **(data or {})})


def error_text(exc):
    if isinstance(exc, ValidationError):
        return '；'.join(exc.messages)
    if isinstance(exc, IntegrityError):
        return '数据冲突或已有相同记录，本次操作未保存。请刷新后核对。'
    return '关联记录已变化或不可用，请刷新后重试。'


def perform(request, operation, callback, form=None):
    if form is not None and not form.is_valid():
        return None
    try:
        result = services.command(request.user, operation, request.POST.get('request_key'),
            {k:v for k,v in request.POST.items() if k not in ['csrfmiddlewaretoken','request_key']}, callback)
        messages.success(request, '操作已保存。')
        return result
    except (ValidationError, IntegrityError, ObjectDoesNotExist) as exc:
        text = error_text(exc)
        if form is not None:
            form.add_error(None, text)
        else:
            messages.error(request, text)
        return None


def context_query(request):
    return urlencode({k:request.GET[k] for k in ['status','date','q','page'] if request.GET.get(k)})


def appointment_for(user, pk):
    app = get_object_or_404(Appointment, pk=pk)
    if not access.appointments(user).filter(pk=pk).exists():
        raise PermissionDenied('没有访问该预约的权限。')
    return app


def visit_for(user, pk):
    visit = get_object_or_404(Visit.objects.select_related('appointment__slot__clinic','appointment__slot__dentist','appointment__patient'), pk=pk)
    appointment_for(user, visit.appointment_id)
    return visit


@login_required
def dashboard(request):
    apps = access.appointments(request.user)
    today = apps.filter(slot__starts_at__date=timezone.localdate()).order_by('slot__starts_at')
    return page(request, 'dashboard', {'today':today[:20], 'today_count':today.count(),
        'upcoming_appointments':apps.filter(status='booked',slot__starts_at__gt=timezone.now()).order_by('slot__starts_at')[:3],
        'pending_review':apps.filter(status='booked').filter(Q(plan__ends_at__lte=timezone.now())|Q(plan__isnull=True,slot__ends_at__lte=timezone.now())).count(),
        'waiting':apps.filter(status='arrived').count(), 'upcoming':apps.filter(status='booked',slot__starts_at__gte=timezone.now()).count(),
        'role_names':access.assignments(request.user).select_related('role','clinic'), 'title':'工作台'})


def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    account = forms.RegistrationForm(request.POST or None, prefix='account')
    patient = forms.PatientForm(request.POST or None, prefix='patient')
    if request.method == 'POST' and account.is_valid() and patient.is_valid():
        try:
            with transaction.atomic():
                user = account.save()
                record = patient.save(commit=False)
                record.user = user
                record.save()
                role = Role.objects.get(code='patient')
                RoleAssignment.objects.create(user=user,role=role,scope_type='self',valid_from=timezone.now())
                services.audit(user,record,'register')
            messages.info(request,'已建立本人自助档案。若曾在诊所建档，请联系前台核验；系统不会按手机号自动合并历史。')
            login(request,user)
            return redirect('dashboard')
        except IntegrityError:
            patient.add_error(None,'账号已存在或资料有冲突，请登录或联系前台核对。')
    return page(request,'register',{'account_form':account,'patient_form':patient,'title':'患者注册'})


@login_required
def appointment_list(request):
    qs = access.appointments(request.user).order_by('-slot__starts_at')
    if request.GET.get('status')=='pending_review':
        qs=qs.filter(status='booked').filter(Q(plan__ends_at__lte=timezone.now())|Q(plan__isnull=True,slot__ends_at__lte=timezone.now()))
    elif request.GET.get('status') in Appointment.Status.values:
        qs = qs.filter(status=request.GET['status'])
    if request.GET.get('date'):
        try:
            qs = qs.filter(slot__starts_at__date=date.fromisoformat(request.GET['date']))
        except ValueError:
            messages.error(request,'日期格式无效。')
    if request.GET.get('q'):
        qs = qs.filter(Q(number__icontains=request.GET['q'])|Q(patient__name__icontains=request.GET['q']))
    return page(request,'appointments',{'items':Paginator(qs,20).get_page(request.GET.get('page')),
        'statuses':list(Appointment.Status.choices)+[('pending_review','到期待核对')],'now':timezone.now(),'context_query':context_query(request),'title':'预约记录'})


@login_required
def appointment_detail(request, pk):
    app = appointment_for(request.user, pk)
    visit = Visit.objects.filter(appointment=app).first()
    states = action_states.appointment_actions(request.user, app, visit)
    services.audit(request.user, app, 'view_appointment')
    return page(request, 'appointment', {'app': app, 'visit': visit,
        'appointment_controls': operation_controls(states, 'appointment_command', pk),
        'context_query': context_query(request), 'title': '预约详情'})


@login_required
def booking(request, pk=None):
    access.require(request.user,['patient','reception'])
    if request.method=='POST' and request.POST.get('confirmed')=='yes' and CommandReceipt.objects.filter(actor=request.user,operation='book',request_key=request.POST.get('request_key','')).exists():
        result=perform(request,'book',lambda:None)
        if result:return redirect('appointment',pk=result)
        return redirect('booking')
    replacing = appointment_for(request.user,pk) if pk else None
    if replacing:
        access.require(request.user,['reception'],replacing.slot.clinic_id)
        state = action_states.appointment_actions(request.user, replacing, Visit.objects.filter(appointment=replacing).first())['reschedule']
        if request.method == 'GET' and not state.enabled:
            return page(request, 'form', {'form': django_forms.Form(), 'title': '调整预约',
                'description': replacing.number, 'blocked_reason': state.reason,
                'back': reverse('appointment', args=[pk])})
    qs = access.slots(request.user).filter(starts_at__gt=timezone.now(),active=True,service__active=True).exclude(reservation_links__is_active=True).order_by('starts_at')
    for key, field in [('clinic','clinic_id'),('dentist','dentist_id')]:
        value = request.GET.get(key)
        if value:
            try:
                qs = qs.filter(**{field:uuid.UUID(value)})
            except ValueError:
                qs = qs.none()
    if request.GET.get('day'):
        try:
            qs = qs.filter(starts_at__date=date.fromisoformat(request.GET['day']))
        except ValueError:
            qs = qs.none()
    form = forms.BookingForm(request.POST or None)
    if access.has_role(request.user,'reception'):
        form.fields['identity_confirmed'].required=True
    else:
        form.fields['identity_confirmed'].widget=django_forms.HiddenInput()
    if replacing:
        qs=access.slots(request.user).filter(starts_at__gt=timezone.now(),active=True,service__active=True).exclude(
            reservation_links__is_active=True,reservation_links__appointment_id__in=Appointment.objects.exclude(pk=replacing.pk).values('pk')).order_by('starts_at')
        # Keep the original query filters when selecting a replacement.
        for key,field in [('clinic','clinic_id'),('dentist','dentist_id')]:
            try:
                if request.GET.get(key): qs=qs.filter(**{field:uuid.UUID(request.GET[key])})
            except ValueError: qs=qs.none()
        if request.GET.get('day'):
            try: qs=qs.filter(starts_at__date=date.fromisoformat(request.GET['day']))
            except ValueError: qs=qs.none()
    from . import reservations
    available=[]
    selected_treatment=request.POST.get('treatment') or request.GET.get('treatment') or None
    try:
        if selected_treatment: selected_treatment=uuid.UUID(selected_treatment)
        for candidate in qs[:150]:
            try:
                reservations.preview(candidate.pk,selected_treatment,excluding=pk)
                available.append(candidate.pk)
            except (ValidationError,ObjectDoesNotExist): pass
    except ValueError: pass
    form.fields['slot'].queryset=qs.filter(pk__in=available)
    if request.GET.get('treatment'): form.fields['treatment'].initial=request.GET['treatment']
    if replacing and not request.POST and not request.GET.get('treatment'):
        form.fields['treatment'].initial=replacing.plan.treatment_type_id
    if replacing:
        patients = Patient.objects.filter(pk=replacing.patient_id)
    elif access.has_role(request.user,'reception'):
        # Exact phone lookup permits cross-clinic booking without exposing the patient directory.
        phone = request.GET.get('phone','').strip()
        patients = Patient.objects.filter(phone=phone,active=True) if phone else editable_patients(request.user).filter(active=True)
    else:
        patients = Patient.objects.filter(user=request.user,active=True)
    form.fields['patient'].queryset = patients
    if patients.count()==1:
        form.fields['patient'].initial=patients.first()
    confirm = False
    if request.method=='POST' and form.is_valid():
        if request.POST.get('confirmed')=='yes':
            d=form.cleaned_data
            result=perform(request,'book',lambda:services.book(request.user,d['patient'].pk,d['slot'].pk,d['reason'],pk,d['treatment'].pk if d['treatment'] else None),form)
            if result:
                return redirect('appointment',pk=result)
        else:
            try:
                d=form.cleaned_data
                plan=reservations.preview(d['slot'].pk,d['treatment'].pk if d['treatment'] else None,d['patient'],pk)
                confirm=True
            except (ValidationError,ObjectDoesNotExist) as exc: form.add_error(None,error_text(exc))
    return page(request,'booking',{'form':form,'confirm':confirm,'replacing':replacing,
        'plan':plan if confirm else None,'treatments':TreatmentType.objects.filter(active=True,treatmentbookingprofile__isnull=False),'clinics':Clinic.objects.filter(pk__in=access.slots(request.user).values('clinic_id')).distinct(),
        'dentists':Dentist.objects.filter(pk__in=access.slots(request.user).values('dentist_id')).distinct(),'title':'调整预约' if pk else '创建预约'})


@login_required
def appointment_command(request, pk, action):
    app=appointment_for(request.user,pk)
    if action not in ['cancel','checkin','no_show','start','undo_checkin','undo_no_show','extend']:
        return HttpResponseBadRequest('未知操作')
    state = action_states.appointment_actions(request.user, app, Visit.objects.filter(appointment=app).first())[action]
    if not state.authorized:
        raise PermissionDenied('当前账号没有执行此操作的权限。')
    form=forms.ExtensionForm(request.POST or None) if action=='extend' else forms.ReasonForm(request.POST or None) if action in ['cancel','no_show','undo_checkin','undo_no_show'] else django_forms.Form(request.POST or None)
    if request.method=='POST':
        result=perform(request,'appointment_'+action,lambda:services.extend_appointment(request.user,pk,form.cleaned_data['minutes'],form.cleaned_data['reason']) if action=='extend' else services.start_visit(request.user,pk) if action=='start' else services.appointment_action(request.user,pk,action,form.cleaned_data.get('reason','')),form)
        if result:
            return redirect('visit' if action=='start' else 'appointment',pk=result)
    return page(request,'form',{'form':form,'title':{'cancel':'取消预约','checkin':'确认到诊签到','no_show':'核实爽约','start':'开始接诊','undo_checkin':'纠正误签到','undo_no_show':'纠正爽约记录','extend':'延长治疗安排'}[action],
        'description':f'{app.number} · {app.patient.name} · {timezone.localtime(app.slot.starts_at):%Y-%m-%d %H:%M}', 'back':reverse('appointment',args=[pk]), 'blocked_reason':state.reason})


@login_required
def visit_detail(request, pk):
    visit = visit_for(request.user, pk)
    doctor = access.own_doctor(request.user, visit.appointment)
    states = action_states.visit_actions(request.user, visit)
    controls = {control['code']: control for control in operation_controls(states, 'visit_command', pk)}
    services.audit(request.user, visit, 'view_visit')
    return page(request, 'visit', {'visit': visit, 'lines': visit.treatments.filter(is_active=True),
        'invoices': visit.invoices.order_by('-issued_at'), 'doctor': doctor,
        'can_bill': states['issue'].authorized,
        'record_controls': [controls[key] for key in ['notes', 'amend']],
        'treatment_controls': [controls[key] for key in ['add_treatment', 'remove_treatment', 'complete']],
        'billing_controls': [controls['issue']],
        'amendments': VisitAmendment.objects.filter(visit=visit).order_by('-created_at') if doctor else [],
        'allergies': visit.appointment.patient.patientallergy_set.select_related('allergy') if doctor else [],
        'contacts': visit.appointment.patient.emergencycontact_set.all() if doctor else [],
        'context_query': context_query(request), 'title': '就诊详情'})


@login_required
def visit_command(request, pk, action):
    visit=visit_for(request.user,pk)
    mapping={'notes':forms.NotesForm,'add_treatment':forms.TreatmentForm,'remove_treatment':forms.VersionForm,
        'complete':forms.VersionForm,'issue':forms.VersionForm,'amend':forms.AmendmentForm}
    if action not in mapping:
        return HttpResponseBadRequest('未知操作')
    if action=='issue':
        access.require(request.user,['reception','finance'],visit.appointment.slot.clinic_id)
    elif not access.own_doctor(request.user,visit.appointment):
        raise PermissionDenied
    state = action_states.visit_actions(request.user, visit)[action]
    form=mapping[action](request.POST or None,initial={'version':visit.current_version,
        'clinical_notes':visit.clinical_notes,'special_needs_notes':visit.special_needs_notes})
    if action=='remove_treatment':
        form.fields['line_id']=forms.NamedChoice(visit.treatments.filter(is_active=True),label='移除的治疗明细')
        form.fields['line_id'].label_from_instance=lambda obj:f'{obj.name_snapshot} × {obj.quantity}'
    if request.method=='POST':
        def save():
            data=dict(form.cleaned_data)
            if action=='issue': return services.issue_invoice(request.user,pk,data['version'])
            if action=='amend': return services.request_amendment(request.user,pk,data)
            if 'line_id' in data: data['line_id']=data['line_id'].pk
            return services.edit_visit(request.user,pk,action,data)
        result=perform(request,'visit_'+action,save,form)
        if result:
            return redirect('invoice',pk=result) if action=='issue' else redirect('visit',pk=pk)
    titles={'notes':'保存诊疗草稿','add_treatment':'添加治疗项目','remove_treatment':'移除治疗项目','complete':'确认完成就诊','issue':'核对并生成结算单','amend':'申请记录更正'}
    return page(request,'form',{'form':form,'title':titles[action],'description':'就诊完成前请核对诊疗说明与全部治疗明细；完成后只能通过审批更正。',
        'preview_lines':visit.treatments.filter(is_active=True) if action in ['complete','issue'] else None,'back':reverse('visit',args=[pk]), 'blocked_reason':state.reason})


@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    visit_for(request.user, invoice.visit_id)
    states = action_states.invoice_actions(request.user, invoice)
    services.audit(request.user, invoice, 'view_invoice')
    return page(request, 'invoice', {'invoice': invoice,
        'invoice_controls': operation_controls(states, 'invoice_command', pk), 'title': '结算单'})


@login_required
def invoice_command(request, pk, action):
    invoice=get_object_or_404(Invoice,pk=pk)
    access.require(request.user,['reception','finance'],invoice.visit.appointment.slot.clinic_id)
    if action not in ['pay','void']:
        return HttpResponseBadRequest('未知操作')
    state = action_states.invoice_actions(request.user, invoice)[action]
    form=forms.PaymentForm(request.POST or None,initial={'amount':invoice.total}) if action=='pay' else forms.ReasonForm(request.POST or None)
    if request.method=='POST':
        result=perform(request,'invoice_'+action,lambda:services.invoice_action(request.user,pk,action,form.cleaned_data),form)
        if result: return redirect('invoice',pk=pk)
    return page(request,'form',{'form':form,'title':'确认模拟全额收款' if action=='pay' else '确认作废结算单',
        'description':f'{invoice.number} · 应付 ¥{invoice.total}。模拟收款不连接真实支付平台。','back':reverse('invoice',args=[pk]), 'blocked_reason':state.reason})


def editable_patients(user):
    condition=Q(pk__in=[])
    if access.has_role(user,'patient'): condition|=Q(user=user)
    if access.has_role(user,'reception'):
        condition|=Q(appointment__slot__clinic_id__in=access.clinic_ids(user,['reception']))
        condition|=Q(pk__in=AuditEvent.objects.filter(actor=user,object_type='Patient',action='create_patient').values('object_id'))
    return Patient.objects.filter(condition).distinct()


@login_required
def patients(request):
    access.require(request.user,['patient','reception'])
    qs=editable_patients(request.user).order_by('name')
    phone=request.GET.get('phone','').strip()
    if phone and access.has_role(request.user,'reception'):
        qs=Patient.objects.filter(phone=phone)
    return page(request,'patients',{'patients':qs[:100],'title':'患者档案','phone':phone,'editable_ids':set(editable_patients(request.user).values_list('pk',flat=True))})


@login_required
def patient_edit(request, pk=None):
    access.require(request.user,['patient','reception'])
    if pk:
        record=get_object_or_404(editable_patients(request.user),pk=pk)
    else:
        access.require(request.user,['reception'])
        record=None
    form=forms.PatientForm(request.POST or None,instance=record)
    if record:
        form.fields['revision'].initial=record.updated_at.isoformat()
        form.fields['phone'].disabled=True
        form.fields['phone'].help_text='联系方式变更须由前台核验后通过专门入口办理；不改变患者档案身份。'
    if request.method=='POST':
        def save():
            before={}
            if record:
                current=Patient.objects.select_for_update().get(pk=record.pk)
                services.check(form.cleaned_data['revision']==current.updated_at.isoformat(),'档案已更新，请刷新后核对。')
                before={k:str(getattr(current,k)) for k in ['name','birth_date','phone','billing_address','email']}
            obj=form.save(commit=False)
            if record and (obj.name!=current.name or obj.birth_date!=current.birth_date):
                obj.identity_verified_at=None
                obj.identity_verified_by=None
            obj.save()
            services.audit(request.user,obj,'update_patient' if record else 'create_patient',before,after={k:str(v) for k,v in form.cleaned_data.items() if k!='revision'})
            if Patient.objects.filter(Q(phone=obj.phone)|Q(name=obj.name,birth_date=obj.birth_date)).exclude(pk=obj.pk).exists():
                messages.info(request,'存在联系方式或姓名与出生日期相同的档案，请完成本人核验；本次没有合并或转移历史。')
            return obj
        if perform(request,'patient_save',save,form):
            return redirect('patients')
    return page(request,'form',{'form':form,'title':'编辑患者档案' if pk else '新建患者档案','back':reverse('patients')})


@login_required
def patient_related(request, pk, kind, related_pk=None):
    record=get_object_or_404(editable_patients(request.user),pk=pk)
    if kind not in ['contact','allergy']: return HttpResponseBadRequest('未知操作')
    related_model=EmergencyContact if kind=='contact' else PatientAllergy
    related=get_object_or_404(related_model,pk=related_pk,patient=record) if related_pk else None
    form=(forms.ContactForm if kind=='contact' else forms.AllergyForm)(request.POST or None,instance=related)
    if request.method=='POST':
        def save():
            before={f:str(getattr(related,f)) for f in form.fields} if related else {}
            obj=form.save(commit=False); obj.patient=record; obj.save()
            services.audit(request.user,obj,('edit_' if related else 'add_')+kind,before=before,after={f:str(v) for f,v in form.cleaned_data.items()})
            return obj
        if perform(request,'patient_'+kind,save,form): return redirect('patients')
    return page(request,'form',{'form':form,'title':'添加紧急联系人' if kind=='contact' else '登记过敏信息','back':reverse('patients')})


@login_required
def schedules(request):
    form=forms.ScheduleForm(request.POST or None)
    form.fields['service'].queryset=DentistClinic.objects.filter(clinic_id__in=access.clinic_ids(request.user,['manager']),active=True).select_related('clinic','dentist')
    if request.method=='POST':
        access.require(request.user,['manager'])
        if perform(request,'schedule',lambda:services.schedule(request.user,**form.cleaned_data),form): return redirect('schedules')
    return page(request,'schedules',{'form':form,'slots':access.slots(request.user).filter(starts_at__gte=timezone.now()).order_by('starts_at')[:100],'title':'医生排班'})


@login_required
def amendments(request):
    access.require(request.user,['manager'])
    qs=VisitAmendment.objects.filter(visit__appointment__slot__clinic_id__in=access.clinic_ids(request.user,['manager'])).select_related('visit__appointment__patient','applicant').order_by('-created_at')
    return page(request,'amendments',{'items':qs[:100],'title':'更正审批'})


@login_required
def amendment_review(request, pk):
    amendment=get_object_or_404(VisitAmendment,pk=pk)
    access.require(request.user,['manager'],amendment.visit.appointment.slot.clinic_id)
    form=forms.ReasonForm(request.POST or None)
    form.fields['decision']=django_forms.ChoiceField(label='审批结果',choices=[('reject','驳回'),('approve','批准')])
    if request.method=='POST':
        if perform(request,'review',lambda:services.review_amendment(request.user,pk,form.cleaned_data['decision']=='approve',form.cleaned_data['reason']),form): return redirect('amendments')
    services.audit(request.user,amendment,'view_amendment')
    return page(request,'form',{'form':form,'title':'审批更正申请','description':amendment.reason,
        'changes':amendment.proposed_changes,'back':reverse('amendments')})


@login_required
def costs(request):
    access.require(request.user,['finance'])
    ids=access.clinic_ids(request.user,['finance'])
    form=forms.CostForm(request.POST or None,initial={'occurred_on':timezone.localdate()})
    form.fields['clinic'].queryset=Clinic.objects.filter(pk__in=ids)
    if request.method=='POST':
        def save():
            obj=form.save(commit=False)
            access.require(request.user,['finance'],obj.clinic_id)
            if obj.dentist_id:
                services.check(DentistClinic.objects.filter(dentist_id=obj.dentist_id,clinic_id=obj.clinic_id,
                    valid_from__date__lte=obj.occurred_on).filter(Q(valid_to__isnull=True)|Q(valid_to__date__gte=obj.occurred_on)).exists(),'该医生在成本发生日无该诊所服务记录。')
            obj.recorded_by=request.user; obj.save(); services.audit(request.user,obj,'record_cost',after={'amount':str(obj.amount)})
            return obj
        if perform(request,'cost',save,form): return redirect('costs')
    return page(request,'costs',{'form':form,'items':CostRecord.objects.filter(clinic_id__in=ids).select_related('clinic','dentist').order_by('-occurred_on')[:100],'title':'成本登记'})


@login_required
def reports(request):
    access.require(request.user,['manager','headquarters','finance','dentist'])
    today=timezone.localdate()
    try:
        year=int(request.GET.get('year',today.year)); quarter=int(request.GET.get('quarter',(today.month-1)//3+1))
        if not (2000<=year<=2100 and 1<=quarter<=4): raise ValueError
    except ValueError:
        return HttpResponseBadRequest('季度参数无效')
    start=date(year,(quarter-1)*3+1,1); end=date(year+1,1,1) if quarter==4 else date(year,quarter*3+1,1)
    ids=access.clinic_ids(request.user,['manager','headquarters','finance'])
    doctor_only=not access.has_role(request.user,'manager','headquarters','finance')
    apps=access.appointments(request.user)
    payments=PaymentRecord.objects.filter(status='success',succeeded_at__date__gte=start,succeeded_at__date__lt=end,
        invoice__visit__appointment__in=apps)
    costs_q=CostRecord.objects.filter(occurred_on__gte=start,occurred_on__lt=end)
    if doctor_only:
        costs_q=costs_q.filter(dentist__user=request.user)
    else:
        costs_q=costs_q.filter(clinic_id__in=ids)
    clinic_filter=request.GET.get('clinic','')
    if clinic_filter:
        try: selected=uuid.UUID(clinic_filter)
        except ValueError: return HttpResponseBadRequest('诊所参数无效')
        if not doctor_only and not Clinic.objects.filter(pk=selected,pk__in=ids).exists(): raise PermissionDenied
        apps=apps.filter(slot__clinic_id=selected)
        payments=payments.filter(invoice__visit__appointment__slot__clinic_id=selected)
        costs_q=costs_q.filter(clinic_id=selected)
    revenue=payments.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    cost=costs_q.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    net=revenue-cost
    receivable=Invoice.objects.filter(status='issued',visit__appointment__in=apps).aggregate(total=Sum('total'))['total'] or Decimal('0')
    due=apps.filter(slot__starts_at__date__gte=start,slot__starts_at__date__lt=end).filter(
        Q(plan__ends_at__lte=timezone.now())|Q(plan__isnull=True,slot__ends_at__lte=timezone.now()))
    denominator=due.count(); cancelled=due.filter(status='cancelled').count(); missed=due.filter(status='no_show').count()
    global_report=not clinic_filter and access.assignments(request.user,['headquarters','finance']).filter(scope_type='global').exists()
    rows=[]
    report_clinics=Clinic.objects.filter(pk__in=ids) if not doctor_only else Clinic.objects.filter(dentistclinic__dentist__user=request.user).distinct()
    if clinic_filter:report_clinics=report_clinics.filter(pk=selected)
    for clinic in report_clinics.order_by('code'):
        income=payments.filter(invoice__visit__appointment__slot__clinic=clinic).aggregate(n=Sum('amount'))['n'] or Decimal('0')
        expense=costs_q.filter(clinic=clinic).aggregate(n=Sum('amount'))['n'] or Decimal('0')
        supplies=costs_q.filter(clinic=clinic,category='supplies').aggregate(n=Sum('amount'))['n'] or Decimal('0')
        operating=costs_q.filter(clinic=clinic,category='operating').aggregate(n=Sum('amount'))['n'] or Decimal('0')
        rows.append({'name':clinic.name,'revenue':income,'supplies':supplies,'operating':operating,'cost':expense,'net':income-expense})
    doctor_rows=[]
    report_doctors=Dentist.objects.filter(user=request.user) if doctor_only else Dentist.objects.filter(dentistclinic__clinic__in=report_clinics).distinct()
    for doctor in report_doctors:
        income=payments.filter(invoice__visit__dentist=doctor).aggregate(n=Sum('amount'))['n'] or Decimal('0')
        direct=costs_q.filter(dentist=doctor).aggregate(n=Sum('amount'))['n'] or Decimal('0')
        slots_q=ScheduleSlot.objects.filter(dentist=doctor,clinic__in=report_clinics,active=True,starts_at__date__gte=start,starts_at__date__lt=end,ends_at__lte=timezone.now())
        count=slots_q.count();used=slots_q.filter(reservation_links__is_active=True,reservation_links__appointment__status__in=['arrived','completed']).distinct().count()
        supplies=costs_q.filter(dentist=doctor,category='supplies').aggregate(n=Sum('amount'))['n'] or Decimal('0')
        operating=costs_q.filter(dentist=doctor,category='operating').aggregate(n=Sum('amount'))['n'] or Decimal('0')
        doctor_rows.append({'name':doctor.name,'income':income,'supplies':supplies,'operating':operating,'cost':direct,'difference':income-direct,'slots':count,'used':used,'utilization':round(used/count*100,2) if count else None})
    return page(request,'reports',{'title':'本人业绩' if doctor_only else '季度经营汇总','year':year,'quarter':quarter,
        'revenue':revenue,'cost':cost,'net':net,'receivable':receivable,'rows':rows,'doctor_only':doctor_only,
        'public_cost':costs_q.filter(dentist__isnull=True).aggregate(n=Sum('amount'))['n'] or 0,
        'fund':services.money(max(net,Decimal('0'))*Decimal('.10')) if global_report else None,
        'cancel_rate':round(cancelled/denominator*100,2) if denominator else None,
        'no_show_rate':round(missed/denominator*100,2) if denominator else None,
        'denominator':denominator,'unchecked':due.filter(status='booked').count(),
        'doctor_rows':doctor_rows,'cost_items':costs_q.select_related('clinic','dentist').order_by('-occurred_on')[:50],
        'clinics':Clinic.objects.filter(pk__in=ids),'payments':payments.select_related('invoice').order_by('-succeeded_at')[:50]})


@login_required
def accounts(request):
    access.require(request.user,['administrator'])
    form=forms.AccountForm(request.POST or None)
    if request.method=='POST':
        def save():
            d=form.cleaned_data
            validate_password(d['password'],User(username=d['username']))
            role=d['role']; clinic=d['clinic']
            services.check(role.code not in ['patient','dentist'],'请通过档案关联患者或医生账号。')
            services.check(role.code not in ['manager','reception'] or clinic is not None,'前台或诊所管理者必须指定诊所。')
            services.check(role.code not in ['headquarters','administrator'] or clinic is None,'总部和系统管理员使用全局范围。')
            user=User.objects.create_user(username=d['username'],password=d['password'])
            RoleAssignment.objects.create(user=user,role=role,scope_type='clinic' if clinic else 'global',clinic=clinic,valid_from=timezone.now())
            services.audit(request.user,user,'create_account',after={'role':role.code})
            return user
        if perform(request,'create_account',save,form): return redirect('accounts')
    return page(request,'accounts',{'form':form,'users':User.objects.order_by('username'),'title':'账号管理'})


@login_required
def account_toggle(request, pk):
    access.require(request.user,['administrator'])
    user=get_object_or_404(User,pk=pk)
    form=forms.ReasonForm(request.POST or None)
    if request.method=='POST':
        def save():
            services.check(user.pk!=request.user.pk,'不能停用当前登录账号。')
            current=User.objects.select_for_update().get(pk=pk)
            current.is_active=not current.is_active;current.save()
            services.audit(request.user,current,'toggle_account',after={'active':current.is_active},reason=form.cleaned_data['reason'])
            return current
        if perform(request,'account_toggle',save,form): return redirect('accounts')
    return page(request,'form',{'form':form,'title':('停用' if user.is_active else '恢复')+'账号 '+user.username,'back':reverse('accounts')})


@login_required
def assignments(request):
    access.require(request.user,['administrator'])
    form=forms.AssignmentForm(request.POST or None)
    if request.method=='POST':
        def save():
            obj=RoleAssignment.objects.create(**form.cleaned_data,valid_from=timezone.now())
            services.audit(request.user,obj,'grant_role',after={'user':str(obj.user_id),'role':obj.role.code,'scope':obj.scope_type})
            return obj
        if perform(request,'assign_role',save,form): return redirect('assignments')
    return page(request,'assignments',{'form':form,'items':RoleAssignment.objects.select_related('user','role','clinic').order_by('-created_at'),'title':'角色与数据范围'})


@login_required
def assignment_revoke(request, pk):
    access.require(request.user,['administrator'])
    obj=get_object_or_404(RoleAssignment,pk=pk)
    form=forms.ReasonForm(request.POST or None)
    if request.method=='POST':
        def save():
            current=RoleAssignment.objects.select_for_update().get(pk=pk)
            services.check(current.user_id!=request.user.pk,'不能撤销当前账号自身权限，请由另一管理员处理。')
            services.check(current.valid_to is None,'授权已结束。')
            current.valid_to=timezone.now();current.save()
            services.audit(request.user,current,'revoke_role',reason=form.cleaned_data['reason']);return current
        if perform(request,'revoke_role',save,form):return redirect('assignments')
    return page(request,'form',{'form':form,'title':'撤销授权','description':f'{obj.user.username} / {obj.role.name}','back':reverse('assignments')})


@login_required
def audit_log(request):
    access.require(request.user,['administrator'])
    qs=AuditEvent.objects.select_related('actor').order_by('-occurred_at')
    if request.GET.get('q'):qs=qs.filter(Q(action__icontains=request.GET['q'])|Q(actor__username__icontains=request.GET['q']))
    # Metadata only: clinical payloads never enter the administrator response.
    return page(request,'audit',{'items':Paginator(qs,50).get_page(request.GET.get('page')),'title':'操作审计'})


MASTER = {
 'clinics':(Clinic,'诊所档案',['code','name','address','manager_name','phone','email','floor_area','active'],['manager','headquarters']),
 'hours':(ClinicHours,'营业时间',['clinic','weekday','opens_at','closes_at'],['manager','headquarters']),
 'dentists':(Dentist,'医生档案',['employee_number','name','birth_date','home_address','phone','email','active'],['headquarters']),
 'services':(DentistClinic,'医生服务关系',['dentist','clinic','valid_from','valid_to','active'],['headquarters']),
 'qualifications':(Qualification,'资质目录',['code','name'],['headquarters']),
 'certificates':(DentistQualification,'医生资质',['dentist','qualification','certificate_number','valid_from','valid_to','active'],['headquarters']),
 'treatments':(TreatmentType,'治疗项目目录',['code','name','unit','catalog_price','active'],['administrator']),
 'resources':(ClinicalResource,'诊疗资源',['clinic','code','name','kind','capacity','active'],['manager','headquarters']),
 'outages':(ResourceUnavailable,'资源停用区间',['resource','starts_at','ends_at','quantity','reason','active'],['manager','headquarters']),
 'booking-profiles':(TreatmentBookingProfile,'治疗时长与周转',['treatment_type','expected_minutes','turnover_minutes'],['administrator']),
 'requirements':(TreatmentResourceRequirement,'治疗资源要求',['treatment_type','resource','quantity'],['manager','headquarters']),
 'allergies':(Allergy,'过敏类型目录',['code','name'],['administrator']),
}
LABELS={'kind':'资源种类','capacity':'容量','resource':'诊疗资源','starts_at':'开始时间','ends_at':'结束时间','quantity':'数量','reason':'原因','treatment_type':'治疗项目','expected_minutes':'预计治疗时长（分钟）','turnover_minutes':'清洁周转（分钟）','code':'编号','name':'名称','address':'地址','manager_name':'负责人','phone':'电话','email':'邮箱','floor_area':'面积（平方米）',
 'active':'启用','clinic':'诊所','weekday':'星期（1=周一，7=周日）','opens_at':'开始营业','closes_at':'结束营业',
 'employee_number':'工号','birth_date':'出生日期','home_address':'联系地址','dentist':'医生','valid_from':'生效时间 / 日期',
 'valid_to':'失效时间 / 日期（可空）','qualification':'资质','certificate_number':'证书编号','unit':'计价单位','catalog_price':'目录单价（元）'}


@login_required
def master(request, kind, pk=None):
    if kind not in MASTER: return HttpResponseBadRequest('未知目录')
    model,title,fields,roles=MASTER[kind]
    if not access.has_role(request.user,*roles):
        for other,(_,_,_,allowed_roles) in MASTER.items():
            if access.has_role(request.user,*allowed_roles):
                return redirect('master',kind=other)
        raise PermissionDenied
    qs=model.objects.all().order_by('created_at')
    if kind in ['clinics','hours']:
        qs=qs.filter(**{'pk__in' if kind=='clinics' else 'clinic_id__in':access.clinic_ids(request.user,roles)})
    if kind=='resources': qs=qs.filter(clinic_id__in=access.clinic_ids(request.user,roles))
    if kind in ['outages','requirements']: qs=qs.filter(resource__clinic_id__in=access.clinic_ids(request.user,roles))
    obj=get_object_or_404(qs,pk=pk) if pk else None
    can_create=kind!='clinics' or access.has_role(request.user,'headquarters')
    Form=django_forms.modelform_factory(model,fields=fields,labels=LABELS,field_classes={f:forms.NamedChoice for f in fields if isinstance(model._meta.get_field(f),models.ForeignKey)})
    form=Form(request.POST or None,instance=obj)
    if kind in ['hours','resources']:form.fields['clinic'].queryset=Clinic.objects.filter(pk__in=access.clinic_ids(request.user,roles))
    if kind in ['outages','requirements']:
        form.fields['resource'].queryset=ClinicalResource.objects.filter(clinic_id__in=access.clinic_ids(request.user,roles))
    for f in form.fields.values():
        if isinstance(f,django_forms.DateTimeField):
            f.widget=django_forms.DateTimeInput(attrs={'type':'datetime-local'},format='%Y-%m-%dT%H:%M')
        elif isinstance(f,django_forms.DateField):
            f.widget=django_forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d')
    if request.method=='POST':
        if not obj and not can_create:raise PermissionDenied
        def save():
            # Serialize clinic resource configuration with booking transactions.
            if kind in ['resources','outages','requirements']:
                clinic_id=form.cleaned_data['clinic'].pk if kind=='resources' else form.cleaned_data['resource'].clinic_id
                if obj:
                    original_clinic=obj.clinic_id if kind=='resources' else obj.resource.clinic_id
                    services.check(original_clinic==clinic_id,'已有资源配置不能直接迁移至其他诊所。')
                Clinic.objects.select_for_update().get(pk=clinic_id)
            # Lock current master row; reject concurrent edits and changes invalidating future bookings.
            if obj:
                current=model.objects.select_for_update().get(pk=obj.pk)
                services.check(request.POST.get('revision')==current.updated_at.isoformat(),'该记录已更新，请刷新后重新编辑。')
            updated=form.save(commit=False)
            if obj and kind in ['services','hours']:
                future=ScheduleSlot.objects.filter(ends_at__gt=timezone.now(),active=True)
                future=future.filter(service=obj) if kind=='services' else future.filter(clinic=obj.clinic)
                services.check(not future.exists(),'存在未来有效排班，暂不允许直接变更服务关系或营业时间；请先核查排班。')
            if kind=='resources':
                from .reservations import capacity_peak
                services.check(updated.capacity>0,'资源容量须大于0。')
                if obj:
                    future=ResourceOccupancy.objects.filter(resource=obj,is_active=True,ends_at__gt=timezone.now())
                    outages=ResourceUnavailable.objects.filter(resource=obj,active=True,ends_at__gt=timezone.now())
                    rows=list(future.values_list('starts_at','ends_at','quantity'))+list(outages.values_list('starts_at','ends_at','quantity'))
                    services.check(not rows or (updated.active and capacity_peak(rows,timezone.now(),max(r[1] for r in rows))<=updated.capacity),'变更会使既有占用超出容量；请通过停用区间列出影响并协调预约。')
                    services.check(not future.exists() or updated.kind==current.kind,'资源已有有效占用，不能改变种类。')
            if kind=='outages':
                from .reservations import capacity_peak
                resource=ClinicalResource.objects.select_for_update().get(pk=updated.resource_id)
                services.check(updated.starts_at<updated.ends_at and updated.quantity>0 and bool(updated.reason.strip()),'停用须填写有效区间、正数数量及原因。')
                rows=list(ResourceUnavailable.objects.filter(resource=resource,active=True).exclude(pk=updated.pk).values_list('starts_at','ends_at','quantity'))
                services.check(not updated.active or capacity_peak(rows,updated.starts_at,updated.ends_at)+updated.quantity<=resource.capacity,'重叠停用数量超过资源容量。')
            if kind=='requirements':
                services.check(updated.quantity>0 and updated.quantity<=updated.resource.capacity,'资源要求须为正数且不超过该资源容量。')
            if kind=='booking-profiles':
                services.check(0<updated.expected_minutes<=480 and 0<=updated.turnover_minutes<=180,'预计时长1至480分钟，周转0至180分钟。')
            updated.save()
            services.audit(request.user,updated,'master_update' if obj else 'master_create',after={k:str(v) for k,v in form.cleaned_data.items()})
            return updated
        if perform(request,'master_'+kind,save,form):return redirect('master',kind=kind)
    rows=[{'pk':item.pk,'values':[str(getattr(item,f)) if not isinstance(model._meta.get_field(f),models.ForeignKey) else getattr(getattr(item,f),'name',str(getattr(item,f))) for f in fields]} for item in qs[:100]]
    return page(request,'master',{'title':title,'kind':kind,'form':form,'record':obj,'can_create':can_create,'rows':rows,
        'headers':[LABELS.get(f,f) for f in fields],'tabs':[(k,v[1]) for k,v in MASTER.items() if access.has_role(request.user,*v[3])]})


@login_required
def doctor_account(request):
    access.require(request.user,['administrator'])
    class DoctorAccountForm(forms.RegistrationForm):
        dentist=forms.NamedChoice(Dentist.objects.filter(user__isnull=True),label='关联医生档案')
    form=DoctorAccountForm(request.POST or None)
    if request.method=='POST':
        def save():
            dentist=Dentist.objects.select_for_update().get(pk=form.cleaned_data['dentist'].pk)
            services.check(dentist.user_id is None,'该医生已经关联账号。')
            user=form.save();dentist.user=user;dentist.save()
            RoleAssignment.objects.create(user=user,role=Role.objects.get(code='dentist'),scope_type='self',valid_from=timezone.now())
            services.audit(request.user,user,'create_doctor_account');return user
        if perform(request,'doctor_account',save,form):return redirect('accounts')
    return page(request,'form',{'form':form,'title':'为医生创建登录账号','back':reverse('accounts')})


@login_required
def patient_identity(request,pk,action):
    from . import identity
    record=get_object_or_404(editable_patients(request.user),pk=pk)
    access.require(request.user,['reception'])
    if action not in ['verify','phone']: return HttpResponseBadRequest('未知核验操作')
    form=(forms.IdentityForm if action=='verify' else forms.PhoneChangeForm)(request.POST or None)
    if request.method=='POST':
        def save():
            d=form.cleaned_data
            if action=='verify': return identity.verify(request.user,pk,d['evidence'],d['username'],d['password1'])
            return identity.change_phone(request.user,pk,d['phone'],d['evidence'])
        if perform(request,'patient_identity_'+action,save,form): return redirect('patients')
    return page(request,'form',{'form':form,'title':'本人核验与账号关联' if action=='verify' else '核验后变更手机号',
        'description':record.name+' · '+record.phone,'back':reverse('patients')})


@login_required
def resource_coordination(request):
    from .reservations import affected_by_outage
    access.require(request.user,['manager','reception','headquarters'])
    clinics=access.clinic_ids(request.user,['manager','reception','headquarters'])
    outages=ResourceUnavailable.objects.filter(resource__clinic_id__in=clinics,active=True,ends_at__gt=timezone.now()).select_related('resource')
    groups=[{'outage':o,'appointments':affected_by_outage(o).select_related('slot__dentist','patient')} for o in outages]
    return page(request,'resources',{'groups':groups,'resources':ClinicalResource.objects.filter(clinic_id__in=clinics).select_related('clinic'),
        'title':'资源与停诊协调'})
