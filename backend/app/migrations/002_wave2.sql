-- ============================================================================
-- 002_wave2.sql —— 二期结构（2026-09-25）
--
-- 事实来源：docs/dev/50-二期工作包拆解.md §2
-- 契约依据：docs/dev/01-共享契约冻结.md §15（二期契约）
-- 裁决依据：姚工 2026-09-25「二期 13 条全做」
--
-- ⚠️ 本文件只写一次，之后永不修改（含"就补一个字段"）。
--    任何结构变更 → 新建 003_xxx.sql（编号递增，不得跳号复用）。
-- ⚠️ 001_init.sql 已冻结，永远不得修改。
--
-- 幂等要求：全部使用 IF NOT EXISTS，可重复执行。
-- 执行方式：由 app/core/migrate.py 在单事务内执行。
-- ⚠️ 迁移脚本只放结构 + settings 种子值，**不放业务数据**。
-- ============================================================================


-- ============================================================================
-- §2.1 practice_plans —— 保存「我的练习方案」（WP-2H 唯一写入方）
-- payload 为 JSON 快照：scope / count / order / mode / time_limit_ms / text_ids…
-- ============================================================================
CREATE TABLE IF NOT EXISTS practice_plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    subject    TEXT    NOT NULL CHECK (subject IN ('chinese', 'english')),
    payload    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_practice_plans_student
    ON practice_plans(student_id, subject);


-- ============================================================================
-- §2.1 srs_review_plan —— 间隔重复复习计划（WP-2F 唯一写入方）
-- SM-2 参数：ease 默认 2.5、初始间隔 1 天（算法参数待姚工确认，见 50-文档 §10）
-- ============================================================================
CREATE TABLE IF NOT EXISTS srs_review_plan (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    unit_id          INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    word_id          INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    due_at           TEXT    NOT NULL,
    interval_days    INTEGER NOT NULL DEFAULT 1 CHECK (interval_days >= 0),
    ease             REAL    NOT NULL DEFAULT 2.5 CHECK (ease >= 1.3),
    reps             INTEGER NOT NULL DEFAULT 0,
    lapses           INTEGER NOT NULL DEFAULT 0,
    last_reviewed_at TEXT,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_srs_student_word
    ON srs_review_plan(student_id, word_id);
CREATE INDEX IF NOT EXISTS idx_srs_due
    ON srs_review_plan(student_id, due_at);


-- ============================================================================
-- §2.1 homework —— 作业定义（WP-2G 唯一写入方）
-- unit_ids 为 JSON 数组
-- ============================================================================
CREATE TABLE IF NOT EXISTS homework (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id   INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT    NOT NULL,
    subject    TEXT    NOT NULL CHECK (subject IN ('chinese', 'english')),
    unit_ids   TEXT    NOT NULL,
    due_at     TEXT,
    status     TEXT    NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'closed')),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_homework_class
    ON homework(class_id, status, due_at);


-- ============================================================================
-- §2.1 homework_submissions —— 作业完成情况（WP-2G 唯一写入方）
-- ============================================================================
CREATE TABLE IF NOT EXISTS homework_submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    homework_id INTEGER NOT NULL REFERENCES homework(id) ON DELETE CASCADE,
    student_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id  INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    status      TEXT    NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'done')),
    finished_at TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_homework_submission
    ON homework_submissions(homework_id, student_id);
CREATE INDEX IF NOT EXISTS idx_homework_sub_student
    ON homework_submissions(student_id, status);


-- ============================================================================
-- §2.1 content_versions —— 内容版本快照（WP-2I 唯一写入方）
-- snapshot 为 JSON 全字段；op ∈ create|update|disable|delete
-- ============================================================================
CREATE TABLE IF NOT EXISTS content_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT    NOT NULL CHECK (entity_type IN ('unit', 'word', 'text')),
    entity_id   INTEGER NOT NULL,
    version     INTEGER NOT NULL,
    snapshot    TEXT    NOT NULL,
    op          TEXT    NOT NULL
                        CHECK (op IN ('create', 'update', 'disable', 'delete')),
    operator_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_content_version
    ON content_versions(entity_type, entity_id, version);
CREATE INDEX IF NOT EXISTS idx_content_versions_entity
    ON content_versions(entity_type, entity_id, created_at);


-- ============================================================================
-- §2.1 user_badges / user_points —— 游戏化激励（WP-2K 唯一写入方）
-- ============================================================================
CREATE TABLE IF NOT EXISTS user_badges (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    badge_code TEXT    NOT NULL,
    awarded_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_user_badge
    ON user_badges(student_id, badge_code);

CREATE TABLE IF NOT EXISTS user_points (
    student_id  INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    points      INTEGER NOT NULL DEFAULT 0 CHECK (points >= 0),
    streak_days INTEGER NOT NULL DEFAULT 0 CHECK (streak_days >= 0),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
