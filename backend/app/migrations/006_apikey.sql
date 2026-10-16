-- ============================================================================
-- 006_apikey.sql —— 管理端实时更换云端语音 API Key（二轮新增需求）
-- 变更：settings 加 cloud_api_key 键（百炼 key 权威源迁移到 settings 表）
-- 幂等性：本文件由 migrate.py 按编号调度、应用记录防重；文件内不做重复执行防御。
-- ============================================================================

-- 云端语音（百炼 DashScope）API Key：
-- - 非空时覆盖 backend/asr.yaml 的 aliyun.api_key（asr.yaml 退化为部署种子）；
-- - ASR 流式/非流式、仲裁、兜底、云端 TTS 全部经 key_store 每次请求现取 → 改完即生效；
-- - GET /api/settings 返回掩码（前4后4），明文只在 PUT 写入与 DB 中。
INSERT INTO settings (key, value, value_type, description, updated_at) VALUES
    ('cloud_api_key', '', 'string', '云端语音（百炼）API Key：非空时覆盖 asr.yaml 的 aliyun.api_key，修改即时生效无需重启', NULL);
