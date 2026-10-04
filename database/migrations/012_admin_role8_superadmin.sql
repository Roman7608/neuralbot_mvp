-- Роль 8: суперадмин (логин admin1 и др.) — см. docs/инструкции/00_ИНСТРУКЦИЯ_ADMIN1.md
ALTER TABLE admin_users DROP CONSTRAINT IF EXISTS admin_users_role_id_check;
ALTER TABLE admin_users ADD CONSTRAINT admin_users_role_id_check CHECK (role_id >= 1 AND role_id <= 8);
