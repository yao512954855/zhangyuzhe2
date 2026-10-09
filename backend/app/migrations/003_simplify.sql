-- ============================================================================
-- 003_simplify.sql —— 简化版数据模型（全量：WP-F 删旧半段 + WP-A 建新半段）
--
-- 两半段同处一个文件：
--   - 「删旧」半段（下方 DROP 段 + asr_logs 去外键重建段）由 WP-F（批次1）写入
--   - 「建新」半段（文件末尾的四张新表 CREATE）由 WP-A（批次2）补全，
--     DDL 与《01-共享契约冻结》§3.1 逐字一致，不得改动
--
-- 幂等：DROP 全部 IF EXISTS；四张新表建前同样先 DROP IF EXISTS（契约 §3.1
-- 「幂等：全部 DROP TABLE IF EXISTS + CREATE」——手工重放本文件不会撞表）。
--
-- 保留表（不删）：
--   - settings          ：系统配置（含 ASR 配置键），契约 §3.2 明确保留
--   - schema_version    ：迁移版本追踪，migrate.py 每次启动会自愈重建（视为基础设施）
--   - asr_logs          ：保留 —— 保留的 asr/ 子系统与 asr_daily_rollup 任务仍读写，
--                         属「被保留模块依赖的表」，不在 §3.2 删除清单内
--   - asr_daily_stats   ：同上，保留
-- ============================================================================


-- ---- 视图（引用了被删表，先删）----
DROP VIEW IF EXISTS v_word_error_ranking;
DROP VIEW IF EXISTS v_student_unit_progress;
DROP VIEW IF EXISTS v_class_today_activity;


-- ---- asr_logs 解耦已删除的 sessions / users（WP-F 拆除性修改）----
-- `asr_logs.session_id` / `student_id` 原外键指向 `sessions` / `users`，这两张表属
-- 「多用户背诵系统」，在本文件后续被删除。但 `asr_logs` 是保留表（保留的 asr/
-- 子系统与 asr_daily_rollup 任务仍读写），故仅去掉外键约束、数据列与数据原样保留，
-- 使其引用已不存在的父表后写入不再被外键校验阻断（SQLite foreign_keys=ON 时，
-- 父表被删后向子表插入会因「no such table」直接失败）。
CREATE TABLE IF NOT EXISTS asr_logs_new (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    student_id INTEGER,
    provider   TEXT    NOT NULL,
    model      TEXT,
    lang       TEXT,
    task_type  TEXT,
    audio_ms   INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    status     TEXT    NOT NULL CHECK (status IN ('ok', 'empty', 'timeout', 'error')),
    error_code TEXT,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
INSERT INTO asr_logs_new
    (id, session_id, student_id, provider, model, lang, task_type,
     audio_ms, latency_ms, status, error_code, created_at)
SELECT
    id, session_id, student_id, provider, model, lang, task_type,
    audio_ms, latency_ms, status, error_code, created_at
FROM asr_logs;
DROP TABLE asr_logs;
ALTER TABLE asr_logs_new RENAME TO asr_logs;
CREATE INDEX IF NOT EXISTS idx_asr_logs_time ON asr_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_asr_logs_stat ON asr_logs(provider, status, created_at DESC);


-- ---- 子表 / 被引用方 ----
DROP TABLE IF EXISTS answers;
DROP TABLE IF EXISTS recitation_events;
DROP TABLE IF EXISTS recitations;
DROP TABLE IF EXISTS homework_submissions;
DROP TABLE IF EXISTS homework;
DROP TABLE IF EXISTS srs_review_plan;
DROP TABLE IF EXISTS practice_plans;
DROP TABLE IF EXISTS content_versions;
DROP TABLE IF EXISTS word_mastery;
DROP TABLE IF EXISTS user_badges;
DROP TABLE IF EXISTS user_points;
DROP TABLE IF EXISTS audit_logs;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS words;
DROP TABLE IF EXISTS texts;
DROP TABLE IF EXISTS units;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS classes;


-- ============================================================================
-- 「建新」半段（WP-A 批次2）—— 契约《01-共享契约冻结》§3.1，DDL 逐字冻结
-- ============================================================================

-- 先删四张新表（幂等重放用；先子后父，避免外键挡 DROP）
DROP TABLE IF EXISTS practice_logs;
DROP TABLE IF EXISTS wrong_book;
DROP TABLE IF EXISTS items;
DROP TABLE IF EXISTS book_units;

PRAGMA foreign_keys = ON;

CREATE TABLE book_units (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,                     -- 如 Starter Unit 1
    sort        INTEGER NOT NULL DEFAULT 0,           -- 升序
    created_at  TEXT    NOT NULL
);

CREATE TABLE items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id     INTEGER NOT NULL REFERENCES book_units(id) ON DELETE CASCADE,
    type        TEXT    NOT NULL CHECK (type IN ('word','phrase','sentence')),
    en          TEXT    NOT NULL,                     -- 英文（唯一性按 §3.3 软约束）
    pos         TEXT    NOT NULL DEFAULT '',          -- 词性，选填，如 adj. / v.
    zh          TEXT    NOT NULL,                     -- 中文释义
    alt_zh      TEXT    NOT NULL DEFAULT '',          -- 其他可接受答案，顿号「、」分隔
    sort        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);
CREATE INDEX idx_items_unit ON items(unit_id, type, sort);

CREATE TABLE wrong_book (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id        INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    mode           TEXT    NOT NULL CHECK (mode IN ('listen','spell_zh2en','spell_en2zh')),
    wrong_count    INTEGER NOT NULL DEFAULT 1,
    last_wrong_at  TEXT    NOT NULL,
    resolved_at    TEXT                                  -- 非空 = 已移出错题本
);
CREATE INDEX idx_wrong_open ON wrong_book(item_id, mode) WHERE resolved_at IS NULL;

CREATE TABLE practice_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id      INTEGER NOT NULL,
    type         TEXT    NOT NULL CHECK (type IN ('word','phrase','sentence')),
    mode         TEXT    NOT NULL CHECK (mode IN ('listen','spell_zh2en','spell_en2zh')),
    total        INTEGER NOT NULL,                    -- 本次总题数
    correct      INTEGER NOT NULL,                    -- 正确个数
    wrong        INTEGER NOT NULL,                    -- 错误个数
    duration_ms  INTEGER NOT NULL,
    created_at   TEXT    NOT NULL
);
