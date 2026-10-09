/// 背诵场次数据模型 —— 简化版 WP-C。
///
/// `SpellRun` 是 `POST /api/practice/start` 的客户端形状
/// （契约 §4.1），也是答题页 → 结算页一路传递的会话对象；
/// `SpellItemResult` 记每个题的判定结果，settle 前逐个上报。
library;

import 'package:flutter/foundation.dart';

/// 一个词条（start 响应 `items` 元素：`{id,en,pos,zh,alt_zh}`）。
@immutable
class SpellItem {
  const SpellItem({
    required this.id,
    required this.en,
    required this.pos,
    required this.zh,
    this.altZh = '',
  });

  final int id;
  final String en;
  final String pos;
  final String zh;
  final String altZh;

  /// 逐字母方格的字母序列：**去空格与连字符**（契约 §6.4）。
  List<String> get letters => en
      .replaceAll(RegExp(r'[\s\-]'), '')
      .split('')
      .where((c) => c.isNotEmpty)
      .toList();
}

/// 一次背诵场次。
@immutable
class SpellRun {
  const SpellRun({
    required this.sessionId,
    required this.mode,
    required this.unitName,
    required this.typeName,
    required this.items,
    required this.startedAt,
  });

  /// practice 会话 id（字符串，WS 与 answer/settle 原样透传）。
  final String sessionId;

  /// `spell_zh2en` / `spell_en2zh`（§7 枚举）。
  final String mode;

  final String unitName;
  final String typeName;
  final List<SpellItem> items;
  final DateTime startedAt;

  bool get isZh2En => mode == 'spell_zh2en';

  int get total => items.length;
}

/// 单题判定结果（内存态；上报走 `/api/practice/answer`，留痕见 §6.4）。
@immutable
class SpellItemResult {
  const SpellItemResult({
    required this.itemId,
    required this.correct,
    this.answer = '',
  });

  final int itemId;
  final bool correct;

  /// 上报的 answer：zh2en=拼出的字母串（跳过为空），en2zh=转写文本。
  final String answer;
}
