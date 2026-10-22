-- ============================================================================
-- 007_speech_rate.sql —— 听写报词语速（todo13 #5，2026-10-19 甲方反馈）
-- 变更：settings 加 dictation_speech_rate 键
-- 口径：int 0~100（flutter_tts setSpeechRate = 值/100），默认 45（中速偏慢）；
--       管理端「练习设置」页可调，App 进听写拉 GET /api/settings 现取现用。
-- 幂等性：本文件由 migrate.py 按编号调度、应用记录防重；文件内不做重复执行防御。
-- ============================================================================

INSERT INTO settings (key, value, value_type, description, updated_at) VALUES
    ('dictation_speech_rate', '45', 'int', '听写报词 TTS 语速（0~100，App 端换算 setSpeechRate=值/100，默认 45 中速偏慢）', NULL);
