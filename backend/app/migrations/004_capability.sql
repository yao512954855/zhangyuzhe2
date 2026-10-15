-- ============================================================================
-- 004_capability.sql —— AI 能力三模式（docs/localize-dev/01-契约冻结 §2）
--
-- 只加 settings 种子，不动任何表结构。幂等：INSERT OR IGNORE。
-- capability_mode 由管理端「AI 能力模式」页热改写（api/capabilities.py），
-- .env 的 CAP_TTS/CAP_OCR/CAP_ASR 非空时可按能力锁定覆盖（优先级更高）。
-- ============================================================================
INSERT OR IGNORE INTO settings (key, value, value_type, description) VALUES
('capability_mode', 'hybrid', 'string', 'AI能力模式：local/hybrid/cloud，管理端可改');
