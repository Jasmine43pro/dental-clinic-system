-- Install using Django migrate; this export does not initialize framework tables.
BEGIN;
--
-- Add field identity_verified_at to patient
--
ALTER TABLE "domain_patient" ADD COLUMN "identity_verified_at" timestamp with time zone NULL;
--
-- Add field identity_verified_by to patient
--
ALTER TABLE "domain_patient" ADD COLUMN "identity_verified_by_id" uuid NULL CONSTRAINT "domain_patient_identity_verified_by_357c3ded_fk_domain_us" REFERENCES "domain_user"("id") DEFERRABLE INITIALLY DEFERRED; SET CONSTRAINTS "domain_patient_identity_verified_by_357c3ded_fk_domain_us" IMMEDIATE;
--
-- Alter field phone on patient
--
CREATE INDEX "domain_patient_phone_1c578c43" ON "domain_patient" ("phone");
--
-- Create model ClinicalResource
--
CREATE TABLE "domain_clinicalresource" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone NOT NULL, "updated_at" timestamp with time zone NOT NULL, "code" varchar(30) NOT NULL, "name" varchar(100) NOT NULL, "kind" varchar(20) NOT NULL, "capacity" integer NOT NULL CHECK ("capacity" >= 0), "active" boolean NOT NULL, "clinic_id" uuid NOT NULL);
--
-- Create model ResourceOccupancy
--
CREATE TABLE "domain_resourceoccupancy" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone NOT NULL, "updated_at" timestamp with time zone NOT NULL, "plan_version" integer NOT NULL CHECK ("plan_version" >= 0), "starts_at" timestamp with time zone NOT NULL, "ends_at" timestamp with time zone NOT NULL, "quantity" integer NOT NULL CHECK ("quantity" >= 0), "is_active" boolean NOT NULL, "appointment_id" uuid NOT NULL, "resource_id" uuid NOT NULL);
--
-- Create model ResourceUnavailable
--
CREATE TABLE "domain_resourceunavailable" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone NOT NULL, "updated_at" timestamp with time zone NOT NULL, "starts_at" timestamp with time zone NOT NULL, "ends_at" timestamp with time zone NOT NULL, "quantity" integer NOT NULL CHECK ("quantity" >= 0), "reason" text NOT NULL, "active" boolean NOT NULL, "resource_id" uuid NOT NULL);
--
-- Create model TreatmentBookingProfile
--
CREATE TABLE "domain_treatmentbookingprofile" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone NOT NULL, "updated_at" timestamp with time zone NOT NULL, "expected_minutes" integer NOT NULL CHECK ("expected_minutes" >= 0), "turnover_minutes" integer NOT NULL CHECK ("turnover_minutes" >= 0), "treatment_type_id" uuid NOT NULL UNIQUE);
--
-- Create model TreatmentResourceRequirement
--
CREATE TABLE "domain_treatmentresourcerequirement" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone NOT NULL, "updated_at" timestamp with time zone NOT NULL, "quantity" integer NOT NULL CHECK ("quantity" >= 0), "resource_id" uuid NOT NULL, "treatment_type_id" uuid NOT NULL);
--
-- Create model AppointmentPlan
--
CREATE TABLE "domain_appointmentplan" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone NOT NULL, "updated_at" timestamp with time zone NOT NULL, "plan_version" integer NOT NULL CHECK ("plan_version" >= 0), "expected_minutes" integer NOT NULL CHECK ("expected_minutes" >= 0), "turnover_minutes" integer NOT NULL CHECK ("turnover_minutes" >= 0), "ends_at" timestamp with time zone NOT NULL, "resource_ends_at" timestamp with time zone NOT NULL, "appointment_id" uuid NOT NULL UNIQUE, "treatment_type_id" uuid NULL, CONSTRAINT "appointment_plan_valid" CHECK (("expected_minutes" > 0 AND "plan_version" > 0 AND "resource_ends_at" >= ("ends_at"))));
--
-- Create model AppointmentSlot
--
CREATE TABLE "domain_appointmentslot" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone NOT NULL, "updated_at" timestamp with time zone NOT NULL, "plan_version" integer NOT NULL CHECK ("plan_version" >= 0), "is_active" boolean NOT NULL, "appointment_id" uuid NOT NULL, "slot_id" uuid NOT NULL, CONSTRAINT "appointment_slot_version_unique" UNIQUE ("appointment_id", "plan_version", "slot_id"), CONSTRAINT "slot_plan_version_positive" CHECK ("plan_version" > 0));
--
-- Create constraint resource_clinic_code on model clinicalresource
--
ALTER TABLE "domain_clinicalresource" ADD CONSTRAINT "resource_clinic_code" UNIQUE ("clinic_id", "code");
--
-- Create constraint resource_positive_capacity on model clinicalresource
--
ALTER TABLE "domain_clinicalresource" ADD CONSTRAINT "resource_positive_capacity" CHECK ("capacity" > 0);
--
-- Create index domain_reso_resourc_56c192_idx on field(s) resource, starts_at, ends_at of model resourceoccupancy
--
CREATE INDEX "domain_reso_resourc_56c192_idx" ON "domain_resourceoccupancy" ("resource_id", "starts_at", "ends_at");
--
-- Create constraint occupancy_version_unique on model resourceoccupancy
--
ALTER TABLE "domain_resourceoccupancy" ADD CONSTRAINT "occupancy_version_unique" UNIQUE ("appointment_id", "plan_version", "resource_id");
--
-- Create constraint occupancy_valid on model resourceoccupancy
--
ALTER TABLE "domain_resourceoccupancy" ADD CONSTRAINT "occupancy_valid" CHECK (("ends_at" > ("starts_at") AND "plan_version" > 0 AND "quantity" > 0));
--
-- Create index domain_reso_resourc_d3565e_idx on field(s) resource, starts_at, ends_at of model resourceunavailable
--
CREATE INDEX "domain_reso_resourc_d3565e_idx" ON "domain_resourceunavailable" ("resource_id", "starts_at", "ends_at");
--
-- Create constraint resource_outage_valid on model resourceunavailable
--
ALTER TABLE "domain_resourceunavailable" ADD CONSTRAINT "resource_outage_valid" CHECK (("ends_at" > ("starts_at") AND "quantity" > 0));
--
-- Create constraint profile_duration_positive on model treatmentbookingprofile
--
ALTER TABLE "domain_treatmentbookingprofile" ADD CONSTRAINT "profile_duration_positive" CHECK ("expected_minutes" > 0);
--
-- Create constraint treatment_resource_unique on model treatmentresourcerequirement
--
ALTER TABLE "domain_treatmentresourcerequirement" ADD CONSTRAINT "treatment_resource_unique" UNIQUE ("treatment_type_id", "resource_id");
--
-- Create constraint requirement_quantity_positive on model treatmentresourcerequirement
--
ALTER TABLE "domain_treatmentresourcerequirement" ADD CONSTRAINT "requirement_quantity_positive" CHECK ("quantity" > 0);
CREATE INDEX "domain_patient_identity_verified_by_id_357c3ded" ON "domain_patient" ("identity_verified_by_id");
ALTER TABLE "domain_clinicalresource" ADD CONSTRAINT "domain_clinicalresource_clinic_id_af52ab67_fk_domain_clinic_id" FOREIGN KEY ("clinic_id") REFERENCES "domain_clinic" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "domain_clinicalresource_clinic_id_af52ab67" ON "domain_clinicalresource" ("clinic_id");
ALTER TABLE "domain_resourceoccupancy" ADD CONSTRAINT "domain_resourceoccup_appointment_id_86c3a08e_fk_domain_ap" FOREIGN KEY ("appointment_id") REFERENCES "domain_appointment" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "domain_resourceoccupancy" ADD CONSTRAINT "domain_resourceoccup_resource_id_78e6d823_fk_domain_cl" FOREIGN KEY ("resource_id") REFERENCES "domain_clinicalresource" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "domain_resourceoccupancy_appointment_id_86c3a08e" ON "domain_resourceoccupancy" ("appointment_id");
CREATE INDEX "domain_resourceoccupancy_resource_id_78e6d823" ON "domain_resourceoccupancy" ("resource_id");
ALTER TABLE "domain_resourceunavailable" ADD CONSTRAINT "domain_resourceunava_resource_id_4a2b962a_fk_domain_cl" FOREIGN KEY ("resource_id") REFERENCES "domain_clinicalresource" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "domain_resourceunavailable_resource_id_4a2b962a" ON "domain_resourceunavailable" ("resource_id");
ALTER TABLE "domain_treatmentbookingprofile" ADD CONSTRAINT "domain_treatmentbook_treatment_type_id_0ab201f9_fk_domain_tr" FOREIGN KEY ("treatment_type_id") REFERENCES "domain_treatmenttype" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "domain_treatmentresourcerequirement" ADD CONSTRAINT "domain_treatmentreso_resource_id_dfd9f3cb_fk_domain_cl" FOREIGN KEY ("resource_id") REFERENCES "domain_clinicalresource" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "domain_treatmentresourcerequirement" ADD CONSTRAINT "domain_treatmentreso_treatment_type_id_b761b527_fk_domain_tr" FOREIGN KEY ("treatment_type_id") REFERENCES "domain_treatmenttype" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "domain_treatmentresourcerequirement_resource_id_dfd9f3cb" ON "domain_treatmentresourcerequirement" ("resource_id");
CREATE INDEX "domain_treatmentresourcerequirement_treatment_type_id_b761b527" ON "domain_treatmentresourcerequirement" ("treatment_type_id");
ALTER TABLE "domain_appointmentplan" ADD CONSTRAINT "domain_appointmentpl_appointment_id_89ac696d_fk_domain_ap" FOREIGN KEY ("appointment_id") REFERENCES "domain_appointment" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "domain_appointmentplan" ADD CONSTRAINT "domain_appointmentpl_treatment_type_id_91c4104f_fk_domain_tr" FOREIGN KEY ("treatment_type_id") REFERENCES "domain_treatmenttype" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "domain_appointmentplan_treatment_type_id_91c4104f" ON "domain_appointmentplan" ("treatment_type_id");
ALTER TABLE "domain_appointmentslot" ADD CONSTRAINT "domain_appointmentsl_appointment_id_7bf53a7d_fk_domain_ap" FOREIGN KEY ("appointment_id") REFERENCES "domain_appointment" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "domain_appointmentslot" ADD CONSTRAINT "domain_appointmentsl_slot_id_e88c7308_fk_domain_sc" FOREIGN KEY ("slot_id") REFERENCES "domain_scheduleslot" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX "one_active_reservation_per_slot" ON "domain_appointmentslot" ("slot_id") WHERE "is_active";
CREATE INDEX "domain_appointmentslot_appointment_id_7bf53a7d" ON "domain_appointmentslot" ("appointment_id");
CREATE INDEX "domain_appointmentslot_slot_id_e88c7308" ON "domain_appointmentslot" ("slot_id");
COMMIT;
