"""Domain persistence, relationships and database integrity constraints."""
import uuid
from datetime import timedelta
from django.contrib.auth.models import AbstractUser, Permission
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import models
from django.db.models import Q, F, Value, Func

class Entity(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta: abstract=True

class User(AbstractUser):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)

class Clinic(Entity):
    code=models.CharField(max_length=20,unique=True)
    name=models.CharField(max_length=100)
    address=models.CharField(max_length=255)
    manager_name=models.CharField(max_length=60)
    phone=models.CharField(max_length=30)
    email=models.EmailField(blank=True)
    floor_area=models.DecimalField(max_digits=10,decimal_places=2)
    active=models.BooleanField(default=True)
    class Meta: constraints=[models.CheckConstraint(condition=Q(floor_area__gte=0),name='clinic_area_nonnegative')]

class ClinicHours(Entity):
    clinic=models.ForeignKey(Clinic,on_delete=models.PROTECT)
    weekday=models.PositiveSmallIntegerField()
    opens_at=models.TimeField()
    closes_at=models.TimeField()
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(weekday__range=(1,7)) & Q(closes_at__gt=F('opens_at')),name='valid_clinic_hours')]

class Dentist(Entity):
    employee_number=models.CharField(max_length=30,unique=True)
    user=models.OneToOneField(User,null=True,blank=True,on_delete=models.PROTECT)
    name=models.CharField(max_length=60)
    birth_date=models.DateField()
    home_address=models.CharField(max_length=255)
    phone=models.CharField(max_length=30)
    email=models.EmailField(blank=True)
    active=models.BooleanField(default=True)

class DentistClinic(Entity):
    dentist=models.ForeignKey(Dentist,on_delete=models.PROTECT)
    clinic=models.ForeignKey(Clinic,on_delete=models.PROTECT)
    valid_from=models.DateTimeField()
    valid_to=models.DateTimeField(null=True,blank=True)
    active=models.BooleanField(default=True)
    class Meta:
        constraints=[
            models.UniqueConstraint(fields=['id','dentist','clinic'],name='service_identity'),
            models.CheckConstraint(condition=Q(valid_to__isnull=True)|Q(valid_to__gt=F('valid_from')),name='service_valid_period'),
            ExclusionConstraint(name='service_no_overlap',expressions=[('dentist',RangeOperators.EQUAL),('clinic',RangeOperators.EQUAL),
                (Func(F('valid_from'),F('valid_to'),Value('[)'),function='TSTZRANGE',output_field=DateTimeRangeField()),RangeOperators.OVERLAPS)],condition=Q(active=True))]

class Qualification(Entity):
    code=models.CharField(max_length=30,unique=True)
    name=models.CharField(max_length=100)

class DentistQualification(Entity):
    dentist=models.ForeignKey(Dentist,on_delete=models.PROTECT)
    qualification=models.ForeignKey(Qualification,on_delete=models.PROTECT)
    certificate_number=models.CharField(max_length=80)
    valid_from=models.DateField()
    valid_to=models.DateField(null=True,blank=True)
    active=models.BooleanField(default=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['dentist','qualification','certificate_number'],name='certificate_identity'),
            models.CheckConstraint(condition=Q(valid_to__isnull=True)|Q(valid_to__gte=F('valid_from')),name='certificate_valid_period')]

class Patient(Entity):
    user=models.OneToOneField(User,null=True,blank=True,on_delete=models.PROTECT)
    name=models.CharField(max_length=60)
    birth_date=models.DateField()
    phone=models.CharField(max_length=30,db_index=True)
    identity_verified_at=models.DateTimeField(null=True,blank=True)
    identity_verified_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.PROTECT,related_name="patients_verified")
    billing_address=models.CharField(max_length=255)
    email=models.EmailField(blank=True)
    active=models.BooleanField(default=True)

class EmergencyContact(Entity):
    patient=models.ForeignKey(Patient,on_delete=models.PROTECT)
    name=models.CharField(max_length=60)
    relationship=models.CharField(max_length=30)
    phone=models.CharField(max_length=30)

class Allergy(Entity):
    code=models.CharField(max_length=30,unique=True)
    name=models.CharField(max_length=80)

class PatientAllergy(Entity):
    patient=models.ForeignKey(Patient,on_delete=models.PROTECT)
    allergy=models.ForeignKey(Allergy,on_delete=models.PROTECT)
    manifestation=models.TextField(blank=True)
    notes=models.TextField(blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['patient','allergy'],name='patient_allergy_unique')]

class ScheduleSlot(Entity):
    service=models.ForeignKey(DentistClinic,on_delete=models.PROTECT)
    dentist=models.ForeignKey(Dentist,on_delete=models.PROTECT)
    clinic=models.ForeignKey(Clinic,on_delete=models.PROTECT)
    starts_at=models.DateTimeField()
    ends_at=models.DateTimeField()
    active=models.BooleanField(default=True)
    class Meta:
        indexes=[models.Index(fields=['clinic','starts_at']),models.Index(fields=['dentist','starts_at'])]
        constraints=[models.CheckConstraint(condition=Q(ends_at=F('starts_at')+timedelta(minutes=30)),name='slot_thirty_minutes'),
            ExclusionConstraint(name='dentist_slot_no_overlap',expressions=[('dentist',RangeOperators.EQUAL),
                (Func(F('starts_at'),F('ends_at'),Value('[)'),function='TSTZRANGE',output_field=DateTimeRangeField()),RangeOperators.OVERLAPS)],condition=Q(active=True))]

class Appointment(Entity):
    class Status(models.TextChoices):
        BOOKED='booked','待到诊'
        CANCELLED='cancelled','已取消'
        NO_SHOW='no_show','爽约'
        ARRIVED='arrived','已到诊'
        COMPLETED='completed','已完成'
    number=models.CharField(max_length=40,unique=True)
    patient=models.ForeignKey(Patient,on_delete=models.PROTECT)
    slot=models.ForeignKey(ScheduleSlot,on_delete=models.PROTECT)
    status=models.CharField(max_length=15,choices=Status,default=Status.BOOKED)
    source=models.CharField(max_length=20,choices=[('patient','患者自助'),('reception','前台代办')])
    reason=models.TextField(blank=True)
    checked_in_at=models.DateTimeField(null=True,blank=True)
    cancelled_at=models.DateTimeField(null=True,blank=True)
    cancel_reason=models.TextField(blank=True)
    cancel_party=models.CharField(max_length=20,blank=True)
    created_by=models.ForeignKey(User,on_delete=models.PROTECT)
    version=models.PositiveIntegerField(default=1)
    @property
    def ends_at(self):
        try: return self.plan.ends_at
        except AppointmentPlan.DoesNotExist: return self.slot.ends_at

    @property
    def planned_resources(self):
        try: version=self.plan.plan_version
        except AppointmentPlan.DoesNotExist: return self.resource_occupancies.none()
        return self.resource_occupancies.filter(plan_version=version).select_related('resource')

    @property
    def reserved_unit_count(self):
        try: version=self.plan.plan_version
        except AppointmentPlan.DoesNotExist: return 1
        return self.slot_links.filter(plan_version=version).count()

    @property
    def duration_minutes(self):
        return int((self.ends_at-self.slot.starts_at).total_seconds()//60)

    class Meta:
        indexes=[models.Index(fields=['patient','status'])]
        constraints=[models.UniqueConstraint(fields=['slot'],condition=Q(status__in=['booked','arrived','completed']),name='one_active_appointment_per_slot'),
            models.CheckConstraint(condition=Q(status__in=['booked','cancelled','no_show','arrived','completed']),name='appointment_status_valid')]

class TreatmentType(Entity):
    code=models.CharField(max_length=30,unique=True)
    name=models.CharField(max_length=100)
    unit=models.CharField(max_length=20)
    catalog_price=models.DecimalField(max_digits=12,decimal_places=2)
    active=models.BooleanField(default=True)
    class Meta: constraints=[models.CheckConstraint(condition=Q(catalog_price__gte=0),name='catalog_price_nonnegative')]

class Visit(Entity):
    appointment=models.OneToOneField(Appointment,on_delete=models.PROTECT,related_name='visit')
    dentist=models.ForeignKey(Dentist,on_delete=models.PROTECT)
    status=models.CharField(max_length=15,choices=[('in_progress','进行中'),('completed','已完成')],default='in_progress')
    started_at=models.DateTimeField()
    completed_at=models.DateTimeField(null=True,blank=True)
    clinical_notes=models.TextField(blank=True)
    special_needs_notes=models.TextField(blank=True)
    current_version=models.PositiveIntegerField(default=1)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(status='in_progress',completed_at__isnull=True)|Q(status='completed',completed_at__gte=F('started_at')),name='visit_completion_state')]

class VisitTreatment(Entity):
    visit=models.ForeignKey(Visit,on_delete=models.PROTECT,related_name='treatments')
    treatment_type=models.ForeignKey(TreatmentType,on_delete=models.PROTECT)
    name_snapshot=models.CharField(max_length=100)
    quantity=models.DecimalField(max_digits=10,decimal_places=2)
    unit_price_snapshot=models.DecimalField(max_digits=12,decimal_places=2)
    subtotal=models.DecimalField(max_digits=14,decimal_places=2)
    is_active=models.BooleanField(default=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(quantity__gt=0)&Q(unit_price_snapshot__gte=0)&Q(subtotal=Func(F('quantity')*F('unit_price_snapshot'),Value(2),function='ROUND')),name='treatment_amount_valid')]

class VisitAmendment(Entity):
    visit=models.ForeignKey(Visit,on_delete=models.PROTECT)
    base_version=models.PositiveIntegerField()
    proposed_changes=models.JSONField()
    reason=models.TextField()
    applicant=models.ForeignKey(User,on_delete=models.PROTECT,related_name='amendments_requested')
    reviewer=models.ForeignKey(User,null=True,blank=True,on_delete=models.PROTECT,related_name='amendments_reviewed')
    status=models.CharField(max_length=15,choices=[('pending','待审批'),('approved','已批准'),('rejected','已驳回')],default='pending')
    decided_at=models.DateTimeField(null=True,blank=True)
    decision_reason=models.TextField(blank=True)
    effective_version=models.PositiveIntegerField(null=True,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['visit'],condition=Q(status='pending'),name='one_pending_amendment')]

class Invoice(Entity):
    number=models.CharField(max_length=40,unique=True)
    visit=models.ForeignKey(Visit,on_delete=models.PROTECT,related_name='invoices')
    source_visit_version=models.PositiveIntegerField()
    replaces=models.OneToOneField('self',null=True,blank=True,on_delete=models.PROTECT,related_name='replacement')
    status=models.CharField(max_length=15,choices=[('issued','已出单'),('paid','已支付'),('void','已作废')],default='issued')
    total=models.DecimalField(max_digits=14,decimal_places=2)
    issued_by=models.ForeignKey(User,on_delete=models.PROTECT,related_name='invoices_issued')
    issued_at=models.DateTimeField()
    voided_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.PROTECT,related_name='invoices_voided')
    voided_at=models.DateTimeField(null=True,blank=True)
    void_reason=models.TextField(blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['visit'],condition=~Q(status='void'),name='one_effective_invoice'),
            models.CheckConstraint(condition=Q(total__gte=0)&Q(status__in=['issued','paid','void']),name='invoice_valid'),
            models.CheckConstraint(condition=~Q(replaces=F('id')),name='invoice_no_self_replacement')]

class InvoiceLine(Entity):
    invoice=models.ForeignKey(Invoice,on_delete=models.PROTECT,related_name='lines')
    visit_treatment=models.ForeignKey(VisitTreatment,on_delete=models.PROTECT)
    name_snapshot=models.CharField(max_length=100)
    quantity_snapshot=models.DecimalField(max_digits=10,decimal_places=2)
    unit_price_snapshot=models.DecimalField(max_digits=12,decimal_places=2)
    subtotal=models.DecimalField(max_digits=14,decimal_places=2)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['invoice','visit_treatment'],name='unique_invoice_source_line'),
            models.CheckConstraint(condition=Q(quantity_snapshot__gt=0)&Q(unit_price_snapshot__gte=0)&Q(subtotal=Func(F('quantity_snapshot')*F('unit_price_snapshot'),Value(2),function='ROUND')),name='invoice_line_amount_valid')]

class PaymentRecord(Entity):
    invoice=models.ForeignKey(Invoice,on_delete=models.PROTECT,related_name='payments')
    method=models.CharField(max_length=30)
    amount=models.DecimalField(max_digits=14,decimal_places=2)
    status=models.CharField(max_length=15,choices=[('pending','待支付'),('success','成功'),('failed','失败')])
    succeeded_at=models.DateTimeField(null=True,blank=True)
    recorded_by=models.ForeignKey(User,on_delete=models.PROTECT)
    request_reference=models.CharField(max_length=100,unique=True)
    class Meta:
        indexes=[models.Index(fields=['status','succeeded_at'])]
        constraints=[models.UniqueConstraint(fields=['invoice'],condition=Q(status='success'),name='one_successful_payment'),
            models.CheckConstraint(condition=Q(amount__gte=0),name='payment_amount_nonnegative')]

class CostRecord(Entity):
    clinic=models.ForeignKey(Clinic,on_delete=models.PROTECT)
    dentist=models.ForeignKey(Dentist,null=True,blank=True,on_delete=models.PROTECT)
    visit=models.ForeignKey(Visit,null=True,blank=True,on_delete=models.PROTECT)
    category=models.CharField(max_length=30,choices=[('supplies','医疗用品'),('operating','运营费用')])
    amount=models.DecimalField(max_digits=14,decimal_places=2)
    occurred_on=models.DateField()
    source_reference=models.CharField(max_length=100)
    description=models.TextField(blank=True)
    recorded_by=models.ForeignKey(User,on_delete=models.PROTECT)
    class Meta:
        indexes=[models.Index(fields=['clinic','occurred_on']),models.Index(fields=['dentist','occurred_on'])]
        constraints=[models.UniqueConstraint(fields=['clinic','source_reference'],name='cost_source_unique'),models.CheckConstraint(condition=Q(amount__gte=0),name='cost_nonnegative')]

class Role(Entity):
    code=models.CharField(max_length=30,unique=True)
    name=models.CharField(max_length=60)
    permissions=models.ManyToManyField(Permission,blank=True)

class RoleAssignment(Entity):
    user=models.ForeignKey(User,on_delete=models.PROTECT)
    role=models.ForeignKey(Role,on_delete=models.PROTECT)
    scope_type=models.CharField(max_length=10,choices=[('self','本人'),('clinic','诊所'),('global','全局')])
    clinic=models.ForeignKey(Clinic,null=True,blank=True,on_delete=models.PROTECT)
    valid_from=models.DateTimeField()
    valid_to=models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(scope_type='clinic',clinic__isnull=False)|Q(scope_type__in=['self','global'],clinic__isnull=True),name='role_scope_valid'),
            models.CheckConstraint(condition=Q(valid_to__isnull=True)|Q(valid_to__gt=F('valid_from')),name='role_period_valid'),
            models.UniqueConstraint(fields=['user','role','scope_type','clinic'],nulls_distinct=False,condition=Q(valid_to__isnull=True),name='unique_open_role_assignment')]

class AuditEvent(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    actor=models.ForeignKey(User,null=True,on_delete=models.PROTECT)
    source=models.CharField(max_length=30)
    object_type=models.CharField(max_length=60)
    object_id=models.UUIDField()
    action=models.CharField(max_length=40)
    before_value=models.JSONField(default=dict)
    after_value=models.JSONField(default=dict)
    reason=models.TextField(blank=True)
    occurred_at=models.DateTimeField(auto_now_add=True)
    class Meta: indexes=[models.Index(fields=['object_type','object_id','occurred_at'])]

class CommandReceipt(Entity):
    actor=models.ForeignKey(User,on_delete=models.PROTECT)
    operation=models.CharField(max_length=50)
    request_key=models.CharField(max_length=100)
    payload_hash=models.CharField(max_length=64)
    result_object_id=models.UUIDField()
    class Meta: constraints=[models.UniqueConstraint(fields=['actor','operation','request_key'],name='command_idempotency_unique')]


class ClinicalResource(Entity):
    clinic=models.ForeignKey(Clinic,on_delete=models.PROTECT)
    code=models.CharField(max_length=30)
    name=models.CharField(max_length=100)
    kind=models.CharField(max_length=20,choices=[('chair','牙椅'),('equipment','设备'),('assistant','辅助人员')])
    capacity=models.PositiveIntegerField(default=1)
    active=models.BooleanField(default=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['clinic','code'],name='resource_clinic_code'),
            models.CheckConstraint(condition=Q(capacity__gt=0),name='resource_positive_capacity')]


class ResourceUnavailable(Entity):
    resource=models.ForeignKey(ClinicalResource,on_delete=models.PROTECT)
    starts_at=models.DateTimeField()
    ends_at=models.DateTimeField()
    quantity=models.PositiveIntegerField(default=1)
    reason=models.TextField()
    active=models.BooleanField(default=True)
    class Meta:
        indexes=[models.Index(fields=['resource','starts_at','ends_at'])]
        constraints=[models.CheckConstraint(condition=Q(ends_at__gt=F('starts_at'),quantity__gt=0),name='resource_outage_valid')]


class TreatmentBookingProfile(Entity):
    treatment_type=models.OneToOneField(TreatmentType,on_delete=models.PROTECT)
    expected_minutes=models.PositiveIntegerField(default=30)
    turnover_minutes=models.PositiveIntegerField(default=0)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(expected_minutes__gt=0),name='profile_duration_positive')]


class TreatmentResourceRequirement(Entity):
    treatment_type=models.ForeignKey(TreatmentType,on_delete=models.PROTECT)
    resource=models.ForeignKey(ClinicalResource,on_delete=models.PROTECT)
    quantity=models.PositiveIntegerField(default=1)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['treatment_type','resource'],name='treatment_resource_unique'),
            models.CheckConstraint(condition=Q(quantity__gt=0),name='requirement_quantity_positive')]


class AppointmentPlan(Entity):
    appointment=models.OneToOneField(Appointment,on_delete=models.PROTECT,related_name='plan')
    plan_version=models.PositiveIntegerField(default=1)
    treatment_type=models.ForeignKey(TreatmentType,null=True,blank=True,on_delete=models.PROTECT)
    expected_minutes=models.PositiveIntegerField(default=30)
    turnover_minutes=models.PositiveIntegerField(default=0)
    ends_at=models.DateTimeField()
    resource_ends_at=models.DateTimeField()
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(expected_minutes__gt=0,plan_version__gt=0,resource_ends_at__gte=F('ends_at')),name='appointment_plan_valid')]


class AppointmentSlot(Entity):
    appointment=models.ForeignKey(Appointment,on_delete=models.PROTECT,related_name='slot_links')
    slot=models.ForeignKey(ScheduleSlot,on_delete=models.PROTECT,related_name='reservation_links')
    plan_version=models.PositiveIntegerField()
    is_active=models.BooleanField(default=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['appointment','plan_version','slot'],name='appointment_slot_version_unique'),
            models.UniqueConstraint(fields=['slot'],condition=Q(is_active=True),name='one_active_reservation_per_slot'),
            models.CheckConstraint(condition=Q(plan_version__gt=0),name='slot_plan_version_positive')]


class ResourceOccupancy(Entity):
    appointment=models.ForeignKey(Appointment,on_delete=models.PROTECT,related_name='resource_occupancies')
    resource=models.ForeignKey(ClinicalResource,on_delete=models.PROTECT)
    plan_version=models.PositiveIntegerField()
    starts_at=models.DateTimeField()
    ends_at=models.DateTimeField()
    quantity=models.PositiveIntegerField(default=1)
    is_active=models.BooleanField(default=True)
    class Meta:
        indexes=[models.Index(fields=['resource','starts_at','ends_at'])]
        constraints=[models.UniqueConstraint(fields=['appointment','plan_version','resource'],name='occupancy_version_unique'),
            models.CheckConstraint(condition=Q(ends_at__gt=F('starts_at'),quantity__gt=0,plan_version__gt=0),name='occupancy_valid')]
