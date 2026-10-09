-- ============================================================================
-- 003_simplify.sql —— 简化版清理（WP-F 批次1，仅「删旧」半段）
--
-- 本文件由 WP-F 创建，**只写 DROP 段**：把旧「多用户背诵系统」的全部业务表删除，
-- 把仓库变成「单用户免登录英语单词工具」的干净地基。
--
-- ⚠️ 本文件**不写** 001-契约 §3.1 的四张新表（book_units / items / wrong_book /
--    practice_logs）—— 那是 WP-A（批次2）的职责，会在本文件**之后追加** CREATE 段。
--    两半段同处一个文件：WP-F 写 DROP，WP-A 补 CREATE，互不覆盖。
--
-- 幂等：全部 DROP TABLE/VIEW IF EXISTS，可重复执行。
-- 删除顺序：先删视图与子表（被引用方），再删父表，避免外键约束挡住 DROP。
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
