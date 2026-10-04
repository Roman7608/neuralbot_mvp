-- Пользователи админки с ролями (1-6).
-- Роли: 1=Менеджер ОП, 2=Ассистент СТО, 3=Хостес, 4=Руководитель ОП, 5=Руководитель СТО, 6=Полный доступ
-- Выполнить: psql -h localhost -U analytics_user -d vikingi_analytics -f database/migrations/005_admin_users.sql

CREATE TABLE IF NOT EXISTS admin_users (
    id SERIAL PRIMARY KEY,
    login VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role_id INTEGER NOT NULL CHECK (role_id BETWEEN 1 AND 6),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_admin_users_login ON admin_users(login);
CREATE INDEX IF NOT EXISTS idx_admin_users_role ON admin_users(role_id);

COMMENT ON TABLE admin_users IS 'Пользователи админ-панели: логин, хеш пароля, роль 1-6';
