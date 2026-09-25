/// 作业数据模型 —— WP-2G（二期契约 §15.2：`GET /api/me/homework`）。
///
/// 字段名沿用后端 snake_case 原样（契约 §2，不驼峰转换）。
/// 响应：`{items:[{id,title,subject,due_at,status}]}`，
/// `status` 为该生提交状态（pending / done）。
library;

import 'package:flutter/foundation.dart';

import '../home/models.dart';

/// 作业提交状态。
enum HomeworkStatus {
  /// 待提交。
  pending('pending', '待提交'),

  /// 已提交。
  done('done', '已完成');

  const HomeworkStatus(this.wire, this.label);

  final String wire;
  final String label;

  /// 未知值降级为 [HomeworkStatus.pending]（宁可显示"待提交"，也不因脏数据崩页）。
  static HomeworkStatus fromJson(Object? raw) {
    final value = raw?.toString();
    for (final s in HomeworkStatus.values) {
      if (s.wire == value) return s;
    }
    return HomeworkStatus.pending;
  }
}

/// 一条作业（服务端 JSON → 对象）。
@immutable
class HomeworkItem {
  const HomeworkItem({
    required this.id,
    required this.title,
    required this.subject,
    this.dueAt,
    required this.status,
  });

  factory HomeworkItem.fromJson(Map<String, dynamic> json) => HomeworkItem(
        id: _toInt(json['id']),
        title: json['title']?.toString() ?? '',
        subject: Subject.tryParse(json['subject']) ?? Subject.english,
        dueAt: json['due_at']?.toString(),
        status: HomeworkStatus.fromJson(json['status']),
      );

  final int id;
  final String title;
  final Subject subject;

  /// 截止时间（ISO 8601 UTC），无截止时为 `null`。
  final String? dueAt;

  final HomeworkStatus status;

  /// 截止日期的展示文案（取 ISO 串前 10 位 `YYYY-MM-DD`，没有则"无截止时间"）。
  String get dueText {
    if (dueAt == null || dueAt!.length < 10) return '无截止时间';
    return dueAt!.substring(0, 10);
  }
}

List<HomeworkItem> parseHomeworkItems(Object? raw) {
  if (raw is! List) return const [];
  final items = <HomeworkItem>[];
  for (final e in raw) {
    if (e is Map) {
      items.add(HomeworkItem.fromJson(Map<String, dynamic>.from(e)));
    }
  }
  return items;
}

int _toInt(Object? value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  return int.tryParse(value?.toString() ?? '') ?? 0;
}
