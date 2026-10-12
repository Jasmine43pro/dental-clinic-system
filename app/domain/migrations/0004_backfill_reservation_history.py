from django.db import migrations


def forwards(apps,schema_editor):
    Appointment=apps.get_model('domain','Appointment')
    Plan=apps.get_model('domain','AppointmentPlan')
    Link=apps.get_model('domain','AppointmentSlot')
    for app in Appointment.objects.select_related('slot').iterator():
        Plan.objects.get_or_create(appointment_id=app.pk,defaults={'plan_version':app.version,'ends_at':app.slot.ends_at,'resource_ends_at':app.slot.ends_at,'expected_minutes':30,'turnover_minutes':0})
        Link.objects.get_or_create(appointment_id=app.pk,slot_id=app.slot_id,plan_version=app.version,
            defaults={'is_active':app.status in ['booked','arrived','completed']})


SQL='''
CREATE OR REPLACE FUNCTION dental_protect_slot_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (NEW.starts_at,NEW.ends_at,NEW.service_id,NEW.dentist_id,NEW.clinic_id)
    IS DISTINCT FROM (OLD.starts_at,OLD.ends_at,OLD.service_id,OLD.dentist_id,OLD.clinic_id)
    AND (EXISTS(SELECT 1 FROM domain_appointment WHERE slot_id=OLD.id)
         OR EXISTS(SELECT 1 FROM domain_appointmentslot WHERE slot_id=OLD.id)) THEN
   RAISE EXCEPTION 'Referenced slot identity is immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
'''
REVERSE='''
CREATE OR REPLACE FUNCTION dental_protect_slot_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (NEW.starts_at,NEW.ends_at,NEW.service_id,NEW.dentist_id,NEW.clinic_id)
    IS DISTINCT FROM (OLD.starts_at,OLD.ends_at,OLD.service_id,OLD.dentist_id,OLD.clinic_id)
    AND EXISTS(SELECT 1 FROM domain_appointment WHERE slot_id=OLD.id) THEN
   RAISE EXCEPTION 'Referenced slot identity is immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
'''


class Migration(migrations.Migration):
    dependencies=[('domain','0003_continuous_reservations_resources_identity')]
    operations=[migrations.RunPython(forwards,migrations.RunPython.noop),migrations.RunSQL(SQL,REVERSE)]
