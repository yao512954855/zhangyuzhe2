/// 一次练习场次 —— 契约 §9.1 `POST /api/sessions` 的响应。
///
/// ⚠️ **响应里绝不含答案**（契约 §9.1 明文）：英语只给 `prompt`（题目侧），
/// 语文只给课文元信息与背诵范围。判定 100% 在服务端，客户端**不要**尝试
/// 自己比对答案 —— 既拿不到依据，也等于把作弊入口送到学生手上。
///
/// 本文件归 WP-APP-02；APP-03（英语练习页）/ APP-04（语文背诵页）
/// 直接 import 复用，**不要另建一份**，否则场次字段一改就要改两处。
library;

import 'package:flutter/foundation.dart';

/// 题序里的一道题（英语 = 词条，语文 = 课文）。
@immutable
class PracticeItem {
  const PracticeItem({
    required this.seq,
    this.wordId,
    this.textId,
    this.mode,
    this.prompt,
    this.exampleCn,
    this.requireSpell = true,
    this.targetLen = 0,
    this.title,
    this.author,
    this.totalChars = 0,
    this.reciteScope = const <int>[],
  });

  factory PracticeItem.fromJson(Map<String, dynamic> json) {
    final rawScope = json['recite_scope'];
    return PracticeItem(
      seq: _int(json['seq']),
      wordId: json['word_id'] == null ? null : _int(json['word_id']),
      textId: json['text_id'] == null ? null : _int(json['text_id']),
      mode: json['mode']?.toString(),
      prompt: json['prompt']?.toString(),
      exampleCn: json['example_cn']?.toString(),
      requireSpell: _bool(json['require_spell'], fallback: true),
      targetLen: _int(json['target_len']),
      title: json['title']?.toString(),
      author: json['author']?.toString(),
      totalChars: _int(json['total_chars']),
      reciteScope: rawScope is List
          ? rawScope.map(_int).where((e) => e >= 0).toList(growable: false)
          : const <int>[],
    );
  }

  final int seq;

  /// 英语题：词条 id。
  final int? wordId;

  /// 语文题：课文 id。
  final int? textId;

  final String? mode;

  /// 题目侧内容（英语 = 中文提示或英文提示；语文 = 课文标题）。
  final String? prompt;

  final String? exampleCn;

  /// 是否要拼读（英语）。
  final bool requireSpell;

  /// 目标词长度 —— **只用于画几个占位框**，不泄露内容（契约 §9.1）。
  final int targetLen;

  // ---- 语文 ----
  final String? title;
  final String? author;
  final int totalChars;

  /// 需要背诵的段落序号。
  final List<int> reciteScope;

  bool get isEnglish => wordId != null;

  @override
  bool operator ==(Object other) => other is PracticeItem && other.seq == seq;

  @override
  int get hashCode => seq.hashCode;
}

/// 一次练习场次。
@immutable
class PracticeSession {
  const PracticeSession({
    required this.sessionId,
    required this.items,
    this.unitId,
    this.unitName,
    this.contentType = 'english',
    this.timeLimitMs = 0,
    this.startedAt,
    this.replayRequest,
  });

  factory PracticeSession.fromJson(Map<String, dynamic> json) {
    final rawItems = json['items'];
    final items = <PracticeItem>[];
    if (rawItems is List) {
      for (final raw in rawItems) {
        if (raw is Map) {
          items.add(PracticeItem.fromJson(Map<String, dynamic>.from(raw)));
        }
      }
    }
    return PracticeSession(
      sessionId: _int(json['session_id']),
      unitId: json['unit_id'] == null ? null : _int(json['unit_id']),
      unitName: json['unit_name']?.toString(),
      contentType: json['content_type']?.toString() ?? 'english',
      items: items,
      timeLimitMs: _int(json['time_limit_ms']),
      startedAt: json['started_at']?.toString(),
    );
  }

  final int sessionId;
  final int? unitId;
  final String? unitName;
  final String contentType;
  final List<PracticeItem> items;

  /// 单题限时（毫秒），0 = 不限时。
  final int timeLimitMs;

  final String? startedAt;

  /// 组卷请求体 —— **本地字段，不在服务端响应里**（`fromJson` 不填）。
  ///
  /// 「再练一次」按原配置重开时原样重发（2026-09-29 姚工拍板：真重开，
  /// 不再回首页）。由组卷调用方（`PracticeConfigController.submit`）在拿到
  /// 响应后补上 —— 所有英语入口都走它，所以一处补全 = 处处可用。
  final Map<String, dynamic>? replayRequest;

  /// 补上「重开用的请求体」，返回带该字段的副本。
  PracticeSession withReplay(Map<String, dynamic> request) => PracticeSession(
        sessionId: sessionId,
        unitId: unitId,
        unitName: unitName,
        contentType: contentType,
        items: items,
        timeLimitMs: timeLimitMs,
        startedAt: startedAt,
        replayRequest: request,
      );

  int get itemCount => items.length;

  bool get isEmpty => items.isEmpty;

  @override
  bool operator ==(Object other) => other is PracticeSession && other.sessionId == sessionId;

  @override
  int get hashCode => sessionId.hashCode;
}

int _int(Object? value) {
  if (value is int) {
    return value;
  }
  if (value is num) {
    return value.toInt();
  }
  return int.tryParse(value?.toString() ?? '') ?? 0;
}

bool _bool(Object? value, {required bool fallback}) {
  if (value is bool) {
    return value;
  }
  if (value == null) {
    return fallback;
  }
  final text = value.toString().trim().toLowerCase();
  if (const {'1', 'true', 'yes'}.contains(text)) {
    return true;
  }
  if (const {'0', 'false', 'no'}.contains(text)) {
    return false;
  }
  return fallback;
}
