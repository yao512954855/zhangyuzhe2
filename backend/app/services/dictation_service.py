"""听写批改服务 —— WP-A 建壳，真实现归 WP-D（契约《01-共享契约冻结》§6.3）。

⚠️ **本文件是壳**：批次2（WP-A）只占位，`grade_handwriting` 抛 `20005`。
批次3 由 WP-D 按契约 §6.3 在**同一文件**内填实现（文件独占权随批次移交 D，
D 不得改壳里已有的函数签名与异常语义）。契约签名冻结如下，D 直接填体：

    def grade_handwriting(image_bytes: bytes, words: list[str]) -> dict:
        调 RecognizeHandwriting（二进制 body，need_rotate=True）→
        prism_wordsInfo 按 (y 行带, x) 排序 → 与 words 逐个比对
        （忽略大小写/空格/连字符；置信度 <85 的对齐标 uncertain）。
        返回 {"results":[{"word","recognized","correct","confidence","uncertain"}],
              "ocr_content": 全文}。
        失败抛 BizError(20005)；图片无效抛 BizError(20006)。

    class _OcrClient:            # 单例，懒加载；AK 读 OCR_ACCESS_KEY_ID/SECRET
        def recognize(self, image_bytes: bytes) -> dict: ...
"""

from __future__ import annotations

from app.core.errors import BizError, ErrorCode


def grade_handwriting(image_bytes: bytes, words: list[str]) -> dict:
    """契约 §6.3 签名 —— WP-A 阶段一律占位失败（20005）。"""
    raise BizError(ErrorCode.OCR_GRADE_FAILED)
