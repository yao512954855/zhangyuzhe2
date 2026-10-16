/// 错题本数据模型与重做组卷 —— 简化版 WP-E。
///
/// `WrongItem` 是 `GET /api/wrongbook` 响应 `items` 元素的客户端形状
/// （契约 §4.1，10 个字段）；`groupForRedo` 把未解决错题按
/// 「listen 一组、spell 按 (mode, unit, type) 一组」拆成重做组
/// （功能点 3：listen 走听写链路不需要组卷，spell 组要发
/// `practice/start` 拿真会话，所以必须按 unit+type 细分）。
library;

import 'package:flutter/foundation.dart';

/// `GET /api/wrongbook` 的一条未解决记录（契约 v1.3：含 mastered_at）。
@immutable
class WrongItem {
  const WrongItem({
    required this.itemId,
    required this.en,
    required this.pos,
    required this.zh,
    required this.mode,
    required this.wrongCount,
    required this.lastWrongAt,
    required this.unitId,
    required this.unitName,
    required this.type,
    this.masteredAt = '',
  });

  factory WrongItem.fromJson(Map<String, dynamic> json) {
    return WrongItem(
      itemId: _toInt(json['item_id']),
      en: json['en']?.toString() ?? '',
      pos: json['pos']?.toString() ?? '',
      zh: json['zh']?.toString() ?? '',
      mode: json['mode']?.toString() ?? '',
      wrongCount: _toInt(json['wrong_count']),
      lastWrongAt: json['last_wrong_at']?.toString() ?? '',
      unitId: _toInt(json['unit_id']),
      unitName: json['unit_name']?.toString() ?? '',
      type: json['type']?.toString() ?? '',
      masteredAt: json['mastered_at']?.toString() ?? '',
    );
  }

  /// 同一条内容可能同时挂在多个 mode 下，列表身份 = `(item_id, mode)`。
  String get key => '$itemId|$mode';

  /// 非空 = 已掌握（判对保留显示，契约 v1.3 §3.4 三态）。
  final String masteredAt;

  bool get isMastered => masteredAt.isNotEmpty;

  final int itemId;
  final String en;

  /// 词性，选填（空串不显示）。
  final String pos;
  final String zh;

  /// `listen` / `spell_zh2en` / `spell_en2zh`（§7 枚举，禁止第二套命名）。
  final String mode;
  final int wrongCount;

  /// ISO8601 UTC（展示用 `formatRelativeDay` 转「今天/昨天/N 天前」）。
  final String lastWrongAt;
  final int unitId;
  final String unitName;

  /// `word` / `phrase` / `sentence`（§7 枚举）。
  final String type;
}

int _toInt(Object? v) {
  if (v is int) {
    return v;
  }
  if (v is num) {
    return v.toInt();
  }
  return int.tryParse(v?.toString() ?? '') ?? 0;
}

/// mode → 中文徽标文案（契约 §7 枚举，全局唯一）。
const modeLabels = <String, String>{
  'listen': '听写',
  'spell_zh2en': '看中文背英文',
  'spell_en2zh': '看英文背中文',
};

/// status 过滤取值与文案（契约 §4.1：unmastered 默认 | mastered | all）。
const statusLabels = <String, String>{
  'unmastered': '未掌握',
  'mastered': '已掌握',
  'all': '全部',
};

/// 重做组的展示顺序：听写 → 看中文背英文 → 看英文背中文。
const redoModeOrder = <String>['listen', 'spell_zh2en', 'spell_en2zh'];

/// 一组一起重做的错题。
@immutable
class RedoGroup {
  const RedoGroup({
    required this.mode,
    required this.items,
    this.unitId = 0,
    this.unitName = '',
    this.type = '',
  });

  /// `listen` / `spell_zh2en` / `spell_en2zh`。
  final String mode;

  final List<WrongItem> items;

  /// spell 组发 `practice/start` 用的单元与类型（listen 组不需要，留空）。
  final int unitId;
  final String unitName;
  final String type;

  String get label => modeLabels[mode] ?? mode;

  int get count => items.length;
}

/// 把未解决错题拆成重做组（纯函数，单测可直接断言 U4）。
///
/// - `listen`：合并成一组（听写链路一次拍一批，不分组卷）；
/// - `spell_zh2en` / `spell_en2zh`：按 `(mode, unit_id, type)` 细分 ——
///   `practice/start` 的请求体是 `{unit_id, type, mode}`（契约 §4.1）。
/// 组顺序按 [redoModeOrder]，同 mode 内按单元名出现顺序。
List<RedoGroup> groupForRedo(List<WrongItem> items) {
  final groups = <RedoGroup>[];
  final listen = items.where((it) => it.mode == 'listen').toList();
  if (listen.isNotEmpty) {
    final names = <String>[];
    for (final it in listen) {
      if (!names.contains(it.unitName)) {
        names.add(it.unitName);
      }
    }
    groups.add(RedoGroup(
      mode: 'listen',
      items: listen,
      unitName: names.join(' · '),
    ));
  }
  for (final mode in const ['spell_zh2en', 'spell_en2zh']) {
    final unitIds = <int>[];
    for (final it in items.where((e) => e.mode == mode)) {
      if (!unitIds.contains(it.unitId)) {
        unitIds.add(it.unitId);
      }
    }
    for (final unitId in unitIds) {
      final inUnit = items
          .where((it) => it.mode == mode && it.unitId == unitId)
          .toList();
      final types = <String>[];
      for (final it in inUnit) {
        if (!types.contains(it.type)) {
          types.add(it.type);
        }
      }
      for (final type in types) {
        groups.add(RedoGroup(
          mode: mode,
          items: inUnit.where((it) => it.type == type).toList(),
          unitId: unitId,
          unitName: inUnit.first.unitName,
          type: type,
        ));
      }
    }
  }
  return groups;
}
