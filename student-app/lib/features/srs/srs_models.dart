/// SRS 间隔重复 —— 数据模型（WP-2F，二期）。
///
/// 字段名与后端契约 §15.2 完全一致（不驼峰转换，见契约 §2）：
/// `GET /api/me/srs/due` → `{items:[{word_id,unit_id,due_at,interval_days,ease,reps,lapses,en,cn,type,unit_name}]}`
/// `POST /api/me/srs/review` → `{next_due,interval_days,reps,ease,lapses}`
library;

import 'package:flutter/foundation.dart';

/// 一条到期复习计划。
@immutable
class SrsItem {
  const SrsItem({
    required this.wordId,
    required this.unitId,
    required this.dueAt,
    this.intervalDays = 1,
    this.ease = 2.5,
    this.reps = 0,
    this.lapses = 0,
    this.en,
    this.cn,
    this.type,
    this.unitName,
  });

  factory SrsItem.fromJson(Map<String, dynamic> json) => SrsItem(
        wordId: _toInt(json['word_id']),
        unitId: _toInt(json['unit_id']),
        dueAt: json['due_at']?.toString() ?? '',
        intervalDays: _toInt(json['interval_days'], 1),
        ease: _toDouble(json['ease'], 2.5),
        reps: _toInt(json['reps']),
        lapses: _toInt(json['lapses']),
        en: json['en']?.toString(),
        cn: json['cn']?.toString(),
        type: json['type']?.toString(),
        unitName: json['unit_name']?.toString(),
      );

  final int wordId;
  final int unitId;

  /// 下次到期时间（ISO 8601 UTC）。
  final String dueAt;

  /// 当前间隔天数（SM-2）。
  final int intervalDays;

  /// 记忆难度系数（SM-2，>=1.3）。
  final double ease;

  /// 连续答对次数。
  final int reps;

  /// 遗忘次数。
  final int lapses;

  /// 展示用：英文。
  final String? en;

  /// 展示用：中文。
  final String? cn;

  /// 展示用：词类型（word/phrase/sentence）。
  final String? type;

  /// 展示用：单元名。
  final String? unitName;
}

/// `GET /api/me/srs/due` 的响应。
@immutable
class SrsDuePage {
  const SrsDuePage({this.items = const []});

  factory SrsDuePage.fromJson(Map<String, dynamic> json) {
    final raw = json['items'];
    final items = <SrsItem>[];
    if (raw is List) {
      for (final e in raw) {
        if (e is Map) {
          items.add(SrsItem.fromJson(Map<String, dynamic>.from(e)));
        }
      }
    }
    return SrsDuePage(items: items);
  }

  final List<SrsItem> items;
}

/// `POST /api/me/srs/review` 的响应。
@immutable
class SrsReviewResult {
  const SrsReviewResult({
    required this.nextDue,
    required this.intervalDays,
    required this.reps,
    this.ease = 2.5,
    this.lapses = 0,
  });

  factory SrsReviewResult.fromJson(Map<String, dynamic> json) => SrsReviewResult(
        nextDue: json['next_due']?.toString() ?? '',
        intervalDays: _toInt(json['interval_days']),
        reps: _toInt(json['reps']),
        ease: _toDouble(json['ease'], 2.5),
        lapses: _toInt(json['lapses']),
      );

  /// 下次复习时间（ISO 8601 UTC）。
  final String nextDue;

  /// 新的间隔天数（SM-2 推进结果）。
  final int intervalDays;

  /// 新的连续答对次数。
  final int reps;

  /// 新的记忆难度系数。
  final double ease;

  /// 新的遗忘次数。
  final int lapses;
}

int _toInt(Object? value, [int fallback = 0]) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  return int.tryParse(value?.toString() ?? '') ?? fallback;
}

double _toDouble(Object? value, [double fallback = 0.0]) {
  if (value is double) return value;
  if (value is num) return value.toDouble();
  return double.tryParse(value?.toString() ?? '') ?? fallback;
}
