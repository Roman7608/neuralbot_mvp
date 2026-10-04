-- Роль 7 больше не используется в коде админки. Бывшие учётки → директор (6).
-- Журнал переводов бота и /api/voice-bot/* — только суперадмин (8, admin1).
UPDATE admin_users SET role_id = 6 WHERE role_id = 7;

ALTER TABLE admin_users DROP CONSTRAINT IF EXISTS admin_users_role_id_check;
ALTER TABLE admin_users ADD CONSTRAINT admin_users_role_id_check
  CHECK (role_id >= 1 AND role_id <= 8 AND role_id != 7);
