BEGIN;
--
-- Raw SQL operation
--

ALTER TABLE domain_scheduleslot ADD CONSTRAINT slot_service_identity
  FOREIGN KEY (service_id,dentist_id,clinic_id)
  REFERENCES domain_dentistclinic (id,dentist_id,clinic_id);
ALTER TABLE domain_visit ADD CONSTRAINT completed_visit_has_time
  CHECK (status <> 'completed' OR completed_at IS NOT NULL);
ALTER TABLE domain_paymentrecord ADD CONSTRAINT payment_state_valid
  CHECK (status IN ('pending','success','failed') AND (status <> 'success' OR succeeded_at IS NOT NULL));
ALTER TABLE domain_visitamendment ADD CONSTRAINT amendment_state_valid
  CHECK (status IN ('pending','approved','rejected') AND
    (status='pending' OR (reviewer_id IS NOT NULL AND decided_at IS NOT NULL)));
CREATE FUNCTION dental_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Audit events are append only' USING ERRCODE='23514'; END $$;
CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON domain_auditevent
  FOR EACH ROW EXECUTE FUNCTION dental_audit_append_only();
CREATE FUNCTION dental_protect_slot_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (NEW.starts_at,NEW.ends_at,NEW.service_id,NEW.dentist_id,NEW.clinic_id)
    IS DISTINCT FROM (OLD.starts_at,OLD.ends_at,OLD.service_id,OLD.dentist_id,OLD.clinic_id)
    AND EXISTS(SELECT 1 FROM domain_appointment WHERE slot_id=OLD.id) THEN
   RAISE EXCEPTION 'Referenced slot identity is immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER slot_history_immutable BEFORE UPDATE ON domain_scheduleslot
 FOR EACH ROW EXECUTE FUNCTION dental_protect_slot_history();

COMMIT;
