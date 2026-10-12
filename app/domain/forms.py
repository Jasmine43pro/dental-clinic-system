from decimal import Decimal
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import *


class NamedChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        if isinstance(obj, DentistClinic):
            return f'{obj.clinic.name} / {obj.dentist.name}'
        if isinstance(obj, Patient):
            return f'{obj.name} · {obj.birth_date:%Y-%m-%d} · 联系号末四位{obj.phone[-4:]}'
        if isinstance(obj, ScheduleSlot):
            return f'{timezone.localtime(obj.starts_at):%Y-%m-%d %H:%M} · {obj.clinic.name} · {obj.dentist.name}'
        return getattr(obj, 'name', getattr(obj, 'username', str(obj.pk)))


class PatientForm(forms.ModelForm):
    revision=forms.CharField(required=False,widget=forms.HiddenInput)
    class Meta:
        model = Patient
        fields = ['name', 'birth_date', 'phone', 'billing_address', 'email']
        labels = {'name':'姓名', 'birth_date':'出生日期', 'phone':'手机号', 'billing_address':'账单地址', 'email':'邮箱'}
        widgets = {'birth_date':forms.DateInput(attrs={'type':'date'}, format='%Y-%m-%d')}

    def clean_birth_date(self):
        value = self.cleaned_data['birth_date']
        if value > timezone.localdate():
            raise ValidationError('出生日期不能在未来。')
        return value

    def clean_phone(self):
        value = self.cleaned_data['phone'].strip()
        if not (len(value) == 11 and value.isdigit() and value.startswith('1')) and not value.startswith('TEST-'):
            raise ValidationError('请输入11位手机号。')
        return value


class RegistrationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ['username']
        labels = {'username':'登录账号'}


class ContactForm(forms.ModelForm):
    class Meta:
        model = EmergencyContact
        fields = ['name','relationship','phone']
        labels = {'name':'联系人姓名','relationship':'关系','phone':'联系电话'}


class AllergyForm(forms.ModelForm):
    allergy = NamedChoice(Allergy.objects.all(), label='过敏类型')
    class Meta:
        model = PatientAllergy
        fields = ['allergy','manifestation','notes']
        labels = {'manifestation':'表现','notes':'备注'}
        widgets = {'manifestation':forms.Textarea(attrs={'rows':2}),'notes':forms.Textarea(attrs={'rows':2})}


class BookingForm(forms.Form):
    identity_confirmed=forms.BooleanField(label='已核对本人姓名、出生日期及预约对象（前台代办必选）',required=False)
    patient = NamedChoice(Patient.objects.none(), label='患者')
    slot = NamedChoice(ScheduleSlot.objects.none(), label='可预约时段')
    treatment = NamedChoice(TreatmentType.objects.filter(active=True,treatmentbookingprofile__isnull=False),label='预计治疗项目',required=False,help_text='尚未明确治疗类型可留空，安排30分钟咨询；不据此判断团购新客资格。')
    reason = forms.CharField(label='预约原因 / 调整原因', required=False, max_length=1000, widget=forms.Textarea(attrs={'rows':2}))


class ReasonForm(forms.Form):
    reason = forms.CharField(label='操作原因', max_length=2000, widget=forms.Textarea(attrs={'rows':2}))


class VersionForm(forms.Form):
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class NotesForm(VersionForm):
    clinical_notes = forms.CharField(label='诊疗说明', max_length=20000, widget=forms.Textarea(attrs={'rows':5}))
    special_needs_notes = forms.CharField(label='特殊需要说明', required=False, max_length=5000, widget=forms.Textarea(attrs={'rows':2}))


class TreatmentForm(VersionForm):
    treatment = NamedChoice(TreatmentType.objects.filter(active=True), label='治疗项目（目录价格）')
    quantity = forms.DecimalField(label='数量', min_value=Decimal('.01'), max_value=Decimal('999999.99'), decimal_places=2, initial=1)


class AmendmentForm(VersionForm):
    clinical_notes = forms.CharField(label='更正后诊疗说明', max_length=20000, widget=forms.Textarea(attrs={'rows':5}))
    reason = forms.CharField(label='更正原因', max_length=2000, widget=forms.Textarea(attrs={'rows':2}))
    treatment = NamedChoice(TreatmentType.objects.filter(active=True), label='替换收费项目（可选）', required=False,
        help_text='仅需更正收费时选择；将当前全部有效明细替换为此一项。多项目复杂调整请先保留原记录，勿误用。')
    quantity = forms.DecimalField(label='替换项目数量', required=False, min_value=Decimal('.01'), max_value=Decimal('999999.99'), decimal_places=2)

    def clean(self):
        data = super().clean()
        if data.get('treatment') and not data.get('quantity'):
            self.add_error('quantity','收费更正必须填写数量。')
        return data


class PaymentForm(forms.Form):
    method = forms.ChoiceField(label='模拟支付方式', choices=[(x,x) for x in ['模拟现金','模拟银行卡','模拟移动支付']])
    amount = forms.DecimalField(label='全额支付金额（元）', min_value=0, max_digits=14, decimal_places=2)


class ScheduleForm(forms.Form):
    service = NamedChoice(DentistClinic.objects.none(), label='医生 / 服务诊所')
    starts_at = forms.DateTimeField(label='开始时间（北京时间，时长30分钟）', widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))


class CostForm(forms.ModelForm):
    clinic = NamedChoice(Clinic.objects.none(), label='所属诊所')
    dentist = NamedChoice(Dentist.objects.filter(active=True), label='归属医生', required=False, help_text='公共费用留空；不自动分摊。')
    class Meta:
        model = CostRecord
        fields = ['clinic','dentist','category','amount','occurred_on','source_reference','description']
        labels = {'category':'成本类别','amount':'金额（元）','occurred_on':'发生日期','source_reference':'来源凭证号','description':'说明'}
        widgets = {'occurred_on':forms.DateInput(attrs={'type':'date'}, format='%Y-%m-%d'), 'description':forms.Textarea(attrs={'rows':2})}


class AccountForm(forms.Form):
    username = forms.CharField(label='账号', max_length=150)
    password = forms.CharField(label='初始密码', widget=forms.PasswordInput)
    role = NamedChoice(Role.objects.exclude(code__in=['patient','dentist']), label='角色', help_text='医生账号从医生档案创建；患者通过注册关联。')
    clinic = NamedChoice(Clinic.objects.all(), label='诊所范围', required=False)


class AssignmentForm(forms.Form):
    user = NamedChoice(User.objects.all(), label='账号')
    role = NamedChoice(Role.objects.all(), label='角色')
    scope_type = forms.ChoiceField(label='授权范围', choices=[('self','本人'),('clinic','指定诊所'),('global','全局')])
    clinic = NamedChoice(Clinic.objects.all(), label='诊所', required=False)

    def clean(self):
        d = super().clean()
        role, scope, clinic, user = d.get('role'), d.get('scope_type'), d.get('clinic'), d.get('user')
        if not role or not user:
            return d
        if (scope == 'clinic') != bool(clinic):
            raise ValidationError('指定诊所范围必须选择诊所，其他范围不能选择诊所。')
        if role.code in ['patient','dentist']:
            if scope != 'self' or not (Patient if role.code=='patient' else Dentist).objects.filter(user=user).exists():
                raise ValidationError('患者/医生必须关联对应档案且使用本人范围。')
        if role.code in ['reception','manager'] and scope != 'clinic':
            raise ValidationError('前台和诊所管理者只能授权指定诊所。')
        if role.code in ['headquarters','administrator'] and scope != 'global':
            raise ValidationError('总部和系统管理员使用全局范围。')
        return d



class IdentityForm(forms.Form):
    confirmed=forms.BooleanField(label='已核对本人姓名、出生日期及联系方式')
    evidence=forms.CharField(label='核验方式与依据',max_length=1000,widget=forms.Textarea(attrs={'rows':3}),help_text='描述本人确认方式，不录入证件号码或上传证件照片。')
    username=forms.CharField(label='关联登录账号（可空）',required=False,max_length=150)
    password1=forms.CharField(label='新账号密码（仅新建账号填写）',required=False,widget=forms.PasswordInput)
    password2=forms.CharField(label='再次输入新账号密码',required=False,widget=forms.PasswordInput)
    def clean(self):
        data=super().clean()
        if data.get('password1') or data.get('password2'):
            if not data.get('username') or data.get('password1')!=data.get('password2'):
                raise ValidationError('新建账号须填写账号及两次相同密码。')
        return data


class PhoneChangeForm(forms.Form):
    phone=forms.CharField(label='核验后的新手机号',max_length=30)
    confirmed=forms.BooleanField(label='已完成本人联系方式变更核验')
    evidence=forms.CharField(label='核验依据及变更原因',max_length=1000,widget=forms.Textarea(attrs={'rows':3}))
    def clean_phone(self):
        value=self.cleaned_data['phone'].strip()
        if not (len(value)==11 and value.isdigit() and value.startswith('1')) and not value.startswith('TEST-'):
            raise ValidationError('请输入11位手机号。')
        return value


class ExtensionForm(ReasonForm):
    minutes=forms.IntegerField(label='延长后的治疗总时长（分钟）',min_value=1,max_value=480)
