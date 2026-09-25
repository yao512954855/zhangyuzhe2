-- ============================================================================
-- 001_init.sql —— 背记系统初始 Schema
--
-- 事实来源：docs/背记系统数据库设计.md v1.0（§3 表结构 / §4 初始数据 / §5 统计视图）
-- 契约依据：docs/dev/01-共享契约冻结.md §4
--
-- ⚠️ 本文件只写一次，之后永不修改（含"就补一个字段"）。
--    任何结构变更 → 新建 002_xxx.sql、003_xxx.sql（编号递增，不得跳号复用）。
--
-- 幂等要求：全部使用 IF NOT EXISTS / INSERT OR IGNORE，可重复执行。
-- 执行方式：由 app/core/migrate.py 在单事务内执行。
-- ============================================================================


-- ============================================================================
-- §3.1 classes —— 班级
-- ============================================================================
CREATE TABLE IF NOT EXISTS classes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    grade       INTEGER NOT NULL CHECK (grade BETWEEN 1 AND 9),
    semester    TEXT    NOT NULL CHECK (semester IN ('first', 'second')),
    school_year TEXT,
    remark      TEXT,
    status      TEXT    NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'archived')),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_classes_name ON classes(name) WHERE status = 'active';


-- ============================================================================
-- §3.2 users —— 用户（管理者 / 学生）
-- ============================================================================
CREATE TABLE IF NOT EXISTS users (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    role               TEXT    NOT NULL CHECK (role IN ('admin', 'student')),
    username           TEXT    NOT NULL COLLATE NOCASE,
    real_name          TEXT    NOT NULL,
    password_hash      TEXT    NOT NULL,
    class_id           INTEGER REFERENCES classes(id) ON DELETE SET NULL,
    register_source    TEXT    NOT NULL DEFAULT 'import'
                               CHECK (register_source IN ('import', 'self')),
    preferred_grade    INTEGER CHECK (preferred_grade IS NULL
                                      OR preferred_grade BETWEEN 1 AND 9),
    preferred_semester TEXT    CHECK (preferred_semester IS NULL
                                      OR preferred_semester IN ('first', 'second')),
    last_login_at      TEXT,
    status             TEXT    NOT NULL DEFAULT 'active'
                               CHECK (status IN ('active', 'disabled')),
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at         TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_class       ON users(class_id, role, status);
CREATE INDEX IF NOT EXISTS idx_users_reg_source  ON users(register_source, status);


-- ============================================================================
-- §3.3 units —— 单元（内容容器）
-- ============================================================================
CREATE TABLE IF NOT EXISTS units (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    subject    TEXT    NOT NULL CHECK (subject IN ('chinese', 'english')),
    grade      INTEGER NOT NULL CHECK (grade BETWEEN 1 AND 9),
    semester   TEXT    NOT NULL CHECK (semester IN ('first', 'second')),
    name       TEXT    NOT NULL,
    order_no   INTEGER NOT NULL DEFAULT 0,
    remark     TEXT,
    status     TEXT    NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'inactive')),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_units       ON units(subject, grade, semester, name);
CREATE INDEX IF NOT EXISTS idx_units_lookup      ON units(subject, grade, semester, status, order_no);


-- ============================================================================
-- §3.4 words —— 英语词条
-- ============================================================================
CREATE TABLE IF NOT EXISTS words (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id       INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    type          TEXT    NOT NULL CHECK (type IN ('word', 'phrase', 'sentence')),
    en            TEXT    NOT NULL,
    cn            TEXT    NOT NULL,
    accepted      TEXT,
    phonetic      TEXT,
    pos           TEXT,
    example_en    TEXT,
    example_cn    TEXT,
    require_spell INTEGER NOT NULL DEFAULT 1 CHECK (require_spell IN (0, 1)),
    order_no      INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'inactive')),
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at    TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_words       ON words(unit_id, en);
CREATE INDEX IF NOT EXISTS idx_words_pick        ON words(unit_id, status, order_no);
CREATE INDEX IF NOT EXISTS idx_words_type        ON words(unit_id, type, status);


-- ============================================================================
-- §3.5 texts —— 语文课文
--
-- 关键约束：content_plain 与 content_pinyin 按空格切分后长度必须严格相等。
--           （校验在 BE-03 导入时执行，非 DB 约束）
-- ============================================================================
CREATE TABLE IF NOT EXISTS texts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id        INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    title          TEXT    NOT NULL,
    author         TEXT,
    content_raw    TEXT    NOT NULL,
    content_plain  TEXT    NOT NULL,
    content_pinyin TEXT    NOT NULL,
    paragraphs     TEXT    NOT NULL,
    recite_scope   TEXT    NOT NULL,
    total_chars    INTEGER NOT NULL DEFAULT 0,
    order_no       INTEGER NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'inactive')),
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at     TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_texts       ON texts(unit_id, title);
CREATE INDEX IF NOT EXISTS idx_texts_pick        ON texts(unit_id, status, order_no);


-- ============================================================================
-- §3.6 sessions —— 练习场次
-- ============================================================================
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    unit_id       INTEGER REFERENCES units(id) ON DELETE SET NULL,
    content_type  TEXT    NOT NULL CHECK (content_type IN ('english', 'chinese')),
    strategy      TEXT    NOT NULL,
    total_items   INTEGER NOT NULL DEFAULT 0,
    correct_items INTEGER NOT NULL DEFAULT 0,
    hint_count    INTEGER NOT NULL DEFAULT 0,
    time_limit_ms INTEGER NOT NULL DEFAULT 0,
    score         REAL,
    status        TEXT    NOT NULL DEFAULT 'ongoing'
                          CHECK (status IN ('ongoing', 'finished', 'abandoned')),
    started_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ended_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_student ON sessions(student_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_unit    ON sessions(unit_id, content_type, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_status  ON sessions(status, started_at DESC);


-- ============================================================================
-- §3.7 answers —— 英语答题明细
--
-- 每条 word 的完整答题产生 2 条记录：step=1 说词 / step=2 拼读。
-- expected 为标准答案快照，词条被改删后历史记录仍可还原。
-- ============================================================================
CREATE TABLE IF NOT EXISTS answers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    student_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    word_id         INTEGER REFERENCES words(id) ON DELETE SET NULL,
    mode            TEXT    NOT NULL CHECK (mode IN ('cn2en', 'en2cn')),
    step            INTEGER NOT NULL DEFAULT 1 CHECK (step IN (1, 2)),
    expected        TEXT    NOT NULL,
    asr_text        TEXT,
    letter_seq      TEXT,
    target_seq      TEXT,
    answer_text     TEXT,
    is_correct      INTEGER NOT NULL DEFAULT 0 CHECK (is_correct IN (0, 1)),
    match_type      TEXT    NOT NULL DEFAULT 'wrong'
                            CHECK (match_type IN ('exact', 'loose', 'fuzzy',
                                                  'wrong', 'timeout', 'empty')),
    asr_confidence  REAL,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    first_voice_ms  INTEGER,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    used_keyboard   INTEGER NOT NULL DEFAULT 0 CHECK (used_keyboard IN (0, 1)),
    hint_level      INTEGER NOT NULL DEFAULT 0 CHECK (hint_level BETWEEN 0 AND 3),
    used_hint_count INTEGER NOT NULL DEFAULT 0,
    appealed        INTEGER NOT NULL DEFAULT 0 CHECK (appealed IN (0, 1)),
    appeal_result   TEXT CHECK (appeal_result IS NULL
                                OR appeal_result IN ('upheld', 'rejected')),
    provider        TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_answers_session      ON answers(session_id, id);
CREATE INDEX IF NOT EXISTS idx_answers_stu_word     ON answers(student_id, word_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_answers_appeal       ON answers(appealed, appeal_result, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_answers_stu_created  ON answers(student_id, created_at DESC);


-- ============================================================================
-- §3.8 recitations —— 语文背诵记录
-- ============================================================================
CREATE TABLE IF NOT EXISTS recitations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    student_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text_id        INTEGER REFERENCES texts(id) ON DELETE SET NULL,
    scope_snapshot TEXT,
    transcript     TEXT,
    total_chars    INTEGER NOT NULL DEFAULT 0,
    matched_chars  INTEGER NOT NULL DEFAULT 0,
    missed_chars   TEXT,
    hint_count     TEXT,
    restart_count  INTEGER NOT NULL DEFAULT 0,
    duration_ms    INTEGER NOT NULL DEFAULT 0,
    first_voice_ms INTEGER,
    covered_ratio  REAL    NOT NULL DEFAULT 0,
    score          REAL,
    grade          TEXT,
    finish_reason  TEXT    CHECK (finish_reason IN ('completed', 'manual', 'timeout')),
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_recitations_student ON recitations(student_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recitations_text    ON recitations(text_id, created_at DESC);


-- ============================================================================
-- §3.9 recitation_events —— 背诵事件流（一期只写不读，驱动二期结算回看）
-- ============================================================================
CREATE TABLE IF NOT EXISTS recitation_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    recitation_id INTEGER NOT NULL REFERENCES recitations(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    pos           INTEGER NOT NULL,
    char          TEXT,
    event_type    TEXT    NOT NULL
                          CHECK (event_type IN ('matched', 'missed', 'hint',
                                                'restart', 'noise')),
    hint_level    INTEGER,
    trigger_by    TEXT    CHECK (trigger_by IS NULL
                                 OR trigger_by IN ('manual', 'auto')),
    elapsed_ms    INTEGER,
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_recitation_events ON recitation_events(recitation_id, seq);


-- ============================================================================
-- §3.10 word_mastery —— 词条掌握度（物化表）
--
-- 写入方唯一：WP-BE-04。其他包只能读（契约 §4.2）。
-- 首页查掌握状态 O(1)，无需实时聚合 answers。
-- ============================================================================
CREATE TABLE IF NOT EXISTS word_mastery (
    student_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    word_id         INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    streak          INTEGER NOT NULL DEFAULT 0,
    total_count     INTEGER NOT NULL DEFAULT 0,
    correct_count   INTEGER NOT NULL DEFAULT 0,
    wrong_count     INTEGER NOT NULL DEFAULT 0,
    hint_total      INTEGER NOT NULL DEFAULT 0,
    last_result     INTEGER CHECK (last_result IS NULL OR last_result IN (0, 1)),
    last_answer_at  TEXT,
    mastered_at     TEXT,
    avg_duration_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (student_id, word_id)
);

CREATE INDEX IF NOT EXISTS idx_mastery_student  ON word_mastery(student_id, mastered_at);
CREATE INDEX IF NOT EXISTS idx_mastery_wrong    ON word_mastery(student_id, wrong_count DESC);


-- ============================================================================
-- §3.11 asr_logs —— 语音调用日志（明细保留 90 天）
-- ============================================================================
CREATE TABLE IF NOT EXISTS asr_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
    student_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
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

CREATE INDEX IF NOT EXISTS idx_asr_logs_time ON asr_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_asr_logs_stat ON asr_logs(provider, status, created_at DESC);


-- ============================================================================
-- §3.12 asr_daily_stats —— 语音用量日汇总
-- ============================================================================
CREATE TABLE IF NOT EXISTS asr_daily_stats (
    stat_date      TEXT    NOT NULL,
    provider       TEXT    NOT NULL,
    call_count     INTEGER NOT NULL DEFAULT 0,
    total_audio_ms INTEGER NOT NULL DEFAULT 0,
    avg_latency_ms INTEGER,
    ok_count       INTEGER NOT NULL DEFAULT 0,
    empty_count    INTEGER NOT NULL DEFAULT 0,
    error_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (stat_date, provider)
);


-- ============================================================================
-- §3.13 settings —— 系统配置（**全项目唯一读取入口是 SettingsService**）
-- ============================================================================
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    value_type  TEXT NOT NULL DEFAULT 'string'
                     CHECK (value_type IN ('string', 'int', 'bool', 'json')),
    description TEXT,
    updated_at  TEXT
);


-- ============================================================================
-- §3.14 audit_logs —— 管理端操作日志
-- ============================================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action      TEXT    NOT NULL,
    target_type TEXT,
    target_id   INTEGER,
    detail      TEXT,
    ip          TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id, created_at DESC);


-- ============================================================================
-- §3.15 schema_version —— 版本管理（记录由 migrate.py 写入，不在本文件内）
-- ============================================================================
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    description TEXT
);


-- ============================================================================
-- §4 初始数据 —— settings 种子值
--
-- 数量说明：本条实际为 20 条，与《数据库设计》§4 原文逐条一致。
--   ⚠️ 10a 规格 F5/U1 写作「21 项」，与事实来源（数据库设计 §4）不符，
--      已按事实来源实现并上报（见交付说明）。
-- value_type 严格区分 string / int / bool / json（契约 §8 枚举表）。
-- ============================================================================
INSERT OR IGNORE INTO settings (key, value, value_type, description) VALUES
('time_limit_behavior',    'A',         'string', '限时到点行为：A自动判错 / B跳过 / C继续但标记'),
('retry_score_penalty',    '10',        'int',    '每次重试扣分'),
('keyboard_score_penalty', '15',        'int',    '使用键盘输入扣分'),
('hint_score_l1',          '20',        'int',    '一档提示扣分'),
('hint_score_l2',          '40',        'int',    '二档提示扣分'),
('hint_score_l3',          '60',        'int',    '三档提示扣分'),
('silence_hint_ms',        '8000',      'int',    '连续静音自动提示阈值（毫秒）'),
('miss_streak_hint',       '5',         'int',    '连续漏字触发提示的阈值'),
('mastery_streak',         '3',         'int',    '连续答对多少次数算「已掌握」'),
('recite_weight_cover',    '0.6',       'string', '背诵评分：覆盖比例权重'),
('recite_weight_accuracy', '0.25',      'string', '背诵评分：正确率权重'),
('recite_weight_hint',     '0.15',      'string', '背诵评分：提示惩罚权重'),
('asr_provider',           'aliyun',    'string', '主语音厂商'),
('asr_provider_backup',    'volcengine','string', '备用语音厂商（空则不启用）'),
('asr_align_window',       '20',        'int',    '语文对齐滑动窗口大小'),
('asr_noise_threshold',    '0.3',       'string', '识别噪声判定阈值'),
('eye_rest_minutes',       '20',        'int',    '护眼休息提醒间隔（分钟）'),
('eye_rest_enabled',       '1',         'bool',   '是否开启护眼休息提醒'),
('eye_warm_mode_auto',     '1',         'bool',   '是否按时间段自动切换暖色模式'),
('page_size_default',      '20',        'int',    '列表默认分页大小');


-- ============================================================================
-- §5 统计视图
-- ============================================================================

-- §5.1 v_word_error_ranking —— 错题榜
CREATE VIEW IF NOT EXISTS v_word_error_ranking AS
SELECT
    w.id            AS word_id,
    w.en,
    w.cn,
    w.unit_id,
    COUNT(*)                                         AS error_count,
    COUNT(DISTINCT a.student_id)                     AS student_count,
    MAX(a.created_at)                                AS last_error_at
FROM answers a
JOIN words w ON w.id = a.word_id
WHERE a.is_correct = 0
  AND a.match_type <> 'empty'
GROUP BY w.id, w.en, w.cn, w.unit_id;

-- §5.2 v_student_unit_progress —— 学生单元进度
CREATE VIEW IF NOT EXISTS v_student_unit_progress AS
SELECT
    m.student_id,
    w.unit_id,
    COUNT(*)                                        AS word_total,
    SUM(CASE WHEN m.total_count > 0 THEN 1 ELSE 0 END) AS word_practiced,
    SUM(CASE WHEN m.mastered_at IS NOT NULL THEN 1 ELSE 0 END) AS word_mastered,
    SUM(m.correct_count)                            AS correct_total,
    SUM(m.total_count)                              AS answer_total
FROM word_mastery m
JOIN words w ON w.id = m.word_id
GROUP BY m.student_id, w.unit_id;

-- §5.3 v_class_today_activity —— 班级今日活动
CREATE VIEW IF NOT EXISTS v_class_today_activity AS
SELECT
    u.class_id,
    DATE(s.started_at)                              AS stat_date,
    COUNT(DISTINCT s.student_id)                    AS active_students,
    COUNT(s.id)                                     AS session_count,
    SUM(s.total_items)                              AS item_total,
    SUM(s.correct_items)                            AS item_correct
FROM sessions s
JOIN users u ON u.id = s.student_id
WHERE s.status = 'finished'
GROUP BY u.class_id, DATE(s.started_at);
