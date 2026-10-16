-- ============================================================================
-- 005_todo11.sql —— 二轮需求（todo11 / 契约 v1.3 §3.1b 冻结产物）
-- 变更：book_units 二级化 / wrong_book 已掌握 / practice_logs 多选重建 / settings 3 键
-- 幂等性：本文件由 migrate.py 按编号调度、应用记录防重；文件内不做重复执行防御。
-- ============================================================================

-- 1) 单元二级化（todo11 B1）------------------------------------------------
-- 空 = 一级；二级只能挂一级下（parent 的 parent 必须为空——服务层校验，DB 不加 CHECK）
ALTER TABLE book_units ADD COLUMN parent_id INTEGER REFERENCES book_units(id);

-- 2) 错题本「已掌握」状态（todo11 A7.2：判对不移出，保留显示+标记）--------
ALTER TABLE wrong_book ADD COLUMN mastered_at TEXT;

-- 3) practice_logs 重建：type 单值 → types JSON 数组；unit_id → unit_ids ---
CREATE TABLE practice_logs_v2 (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_ids     TEXT    NOT NULL,                    -- JSON 数组，如 [7,8]
    types        TEXT    NOT NULL,                    -- JSON 数组，如 ["word","phrase"]
    mode         TEXT    NOT NULL CHECK (mode IN ('listen','spell_zh2en','spell_en2zh')),
    total        INTEGER NOT NULL,                    -- 本次总题数
    correct      INTEGER NOT NULL,                    -- 正确个数
    wrong        INTEGER NOT NULL,                    -- 错误个数
    duration_ms  INTEGER NOT NULL,
    created_at   TEXT    NOT NULL
);
INSERT INTO practice_logs_v2 (id, unit_ids, types, mode, total, correct, wrong, duration_ms, created_at)
    SELECT id,
           json_array(unit_id),
           json_array(type),
           mode, total, correct, wrong, duration_ms, created_at
    FROM practice_logs;                               -- 历史行转单元素数组
DROP TABLE practice_logs;
ALTER TABLE practice_logs_v2 RENAME TO practice_logs;

-- 4) 设置键（复用 001 settings 表；todo11 A3/A5 倒计时，管理端可调）---------
INSERT INTO settings (key, value, value_type, description, updated_at) VALUES
    ('dictation_countdown_s',      '3',  'int',    '听写：报词后 N 秒自动报下一个词（1~10）',   NULL),
    ('spell_verdict_countdown_s',  '3',  'int',    '背诵：判定后 N 秒无操作自动下一题（1~10）', NULL),
    ('dictation_hint',             'zh', 'string', '听写报词显示语言：zh | en',                 NULL);
