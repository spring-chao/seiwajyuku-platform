-- 0053 rollback is loss-averse.  Run it only before Volunteer 2.0 data or a
-- person-level employee WeChat binding exists; otherwise restore a snapshot.

CREATE TEMPORARY TABLE volunteer2_rollback_guard (n INT CHECK(n=0));
INSERT INTO volunteer2_rollback_guard
SELECT
    (SELECT COUNT(*) FROM volunteer_service_units)
    + (SELECT COUNT(*) FROM volunteer_appointment_recommendation_rules)
    + (SELECT COUNT(*) FROM volunteer_appointment_links)
    + (SELECT COUNT(*) FROM volunteer_appointments WHERE volunteer_service_unit_id IS NOT NULL)
    + (SELECT COUNT(*) FROM wechat_member_bindings WHERE person_id IS NOT NULL OR verified_user_id IS NOT NULL)
    + (SELECT COUNT(*) FROM volunteer_appointments WHERE appointment_key IN (
        'volunteer_shuku_chair', 'volunteer_shuku_learning_vice_chair',
        'volunteer_shuku_operations_vice_chair', 'volunteer_shuku_development_vice_chair',
        'volunteer_shuku_director', 'volunteer_shuku_supervisor_chair',
        'volunteer_shuku_supervisor', 'volunteer_center_chair',
        'volunteer_center_learning_vice_chair', 'volunteer_center_learning_director',
        'volunteer_center_operations_vice_chair', 'volunteer_center_operations_director',
        'volunteer_center_development_vice_chair', 'volunteer_center_development_director',
        'volunteer_center_supervisor', 'volunteer_committee_learning',
        'volunteer_committee_operations', 'volunteer_committee_development'
    ));
DROP TEMPORARY TABLE volunteer2_rollback_guard;

ALTER TABLE wechat_member_bindings
    DROP FOREIGN KEY fk_wechat_binding_person,
    DROP FOREIGN KEY fk_wechat_binding_verified_user,
    DROP INDEX idx_wechat_member_bindings_person,
    DROP COLUMN verified_user_id,
    DROP COLUMN person_id,
    MODIFY COLUMN member_id BIGINT NOT NULL;

ALTER TABLE volunteer_appointments
    DROP FOREIGN KEY fk_volunteer_appointments_service_unit,
    DROP INDEX idx_volunteer_appointments_service_unit,
    DROP COLUMN volunteer_service_unit_id;

DROP TABLE volunteer_appointment_links;
DROP TABLE volunteer_appointment_recommendation_rules;
DROP TABLE volunteer_service_units;
DROP TABLE volunteer_position_profiles;
DELETE FROM volunteer_position_catalog WHERE position_key IN (
    'volunteer_shuku_chair', 'volunteer_shuku_learning_vice_chair',
    'volunteer_shuku_operations_vice_chair', 'volunteer_shuku_development_vice_chair',
    'volunteer_shuku_director', 'volunteer_shuku_supervisor_chair',
    'volunteer_shuku_supervisor', 'volunteer_center_chair',
    'volunteer_center_learning_vice_chair', 'volunteer_center_learning_director',
    'volunteer_center_operations_vice_chair', 'volunteer_center_operations_director',
    'volunteer_center_development_vice_chair', 'volunteer_center_development_director',
    'volunteer_center_supervisor', 'volunteer_committee_learning',
    'volunteer_committee_operations', 'volunteer_committee_development'
);

DELETE FROM schema_migrations WHERE version='0053_volunteer2_service_organizations.sql';
