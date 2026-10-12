-- Install using Django migrate; this export does not initialize framework tables.
BEGIN;
--
-- Raw Python operation
--
-- THIS OPERATION CANNOT BE WRITTEN AS SQL
--
-- Raw SQL operation
--

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

COMMIT;
