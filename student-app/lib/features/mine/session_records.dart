/// 「我的记录」场次模型 + 控制器 —— WP-APP-05 F2（U3）。
///
/// 数据来自 `GET /api/sessions` 与 `GET /api/sessions/{id}`
/// （**契约 §14.1 缺口 7** → 接口设计 §9.5，2026-09-25 补齐）。
///
/// 在此之前契约只有 3 个 `POST`、没有读取侧接口，页面只能渲染 5 个汇总数字
/// （§八 #75）；现在列表与单次详情都是**服务端真数据**，客户端不造一行。
///
/// ### 两条与服务端对齐的「不给假选项」规则
///
/// - `status == 'ongoing'` 的场次**不可点**：服务端对进行中的场次**拒绝**下发
///   逐题明细（`expected` 就是答案，中途返回等于泄题）。客户端点下去必然报错，
///   所以列表里直接按 [SessionRecord.canOpenDetail] 收起入口。
/// - `score` 为 `null` 时**不显示 0 分**，改显示「—」：进行中的场次本来就没有成绩。
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';

/// `sessions.status` 的中文标签（取值域由契约冻结，不存在第四种）。
String sessionStatusLabel(String status) => switch (status) {
      'ongoing' => '进行中',
      'abandoned' => '中途退出',
      _ => '已完成',
    };

/// 列表 / 详情共用的场次摘要（`GET /api/sessions` 的一条）。
class SessionRecord {
  const SessionRecord({
    required this.sessionId,
    required this.contentType,
    this.unitId,
    this.unitName,
    this.status = 'finished',
    this.totalItems = 0,
    this.correctItems = 0,
    this.accuracy = 0,
    this.score,
    this.hintCount = 0,
    this.startedAt,
    this.endedAt,
    this.durationMs = 0,
  });

  factory SessionRecord.fromJson(Map<String, dynamic> json) => SessionRecord(
        sessionId: _int(json['session_id']),
        contentType: _text(json['content_type']) ?? 'english',
        unitId: _intOrNull(json['unit_id']),
        unitName: _text(json['unit_name']),
        status: _text(json['status']) ?? 'finished',
        totalItems: _int(json['total_items']),
        correctItems: _int(json['correct_items']),
        accuracy: _double(json['accuracy']),
        score: _doubleOrNull(json['score']),
        hintCount: _int(json['hint_count']),
        startedAt: _text(json['started_at']),
        endedAt: _text(json['ended_at']),
        durationMs: _int(json['duration_ms']),
      );

  final int sessionId;
  final String contentType;
  final int? unitId;
  final String? unitName;
  final String status;
  final int totalItems;
  final int correctItems;
  final double accuracy;

  /// 进行中的场次没有成绩 → `null`（**不要拿 0 冒充**）。
  final double? score;

  final int hintCount;
  final String? startedAt;
  final String? endedAt;
  final int durationMs;

  bool get isOngoing => status == 'ongoing';
  bool get isChinese => contentType == 'chinese';

  /// 能不能进详情。服务端对 `ongoing` 场次是**拒绝**的，所以这里也不给入口。
  bool get canOpenDetail => !isOngoing;

  /// 学科标签，用在单元名之前。
  String get subjectLabel => isChinese ? '语文背诵' : '英语练习';

  /// 标题行：优先「学科 · 单元名」，单元没了就只显示学科。
  String get title => switch (unitName) {
        final String name when name.isNotEmpty => '$subjectLabel · $name',
        _ => subjectLabel,
      };
}

/// 英语场次的逐项明细（一个词条一行）。
class SessionItem {
  const SessionItem({
    this.wordId,
    this.en,
    this.cn,
    this.state = 'wrong',
    this.yourAnswer,
    this.expected,
    this.matchType,
    this.steps = const [],
    this.hintLevel = 0,
    this.usedKeyboard = false,
  });

  factory SessionItem.fromJson(Map<String, dynamic> json) => SessionItem(
        wordId: _intOrNull(json['word_id']),
        en: _text(json['en']),
        cn: _text(json['cn']),
        state: _text(json['state']) ?? 'wrong',
        yourAnswer: _text(json['your_answer']),
        expected: _text(json['expected']),
        matchType: _text(json['match_type']),
        steps: _intList(json['steps']),
        hintLevel: _int(json['hint_level']),
        usedKeyboard: json['used_keyboard'] == true,
      );

  final int? wordId;
  final String? en;
  final String? cn;

  /// `correct` / `wrong` / `empty`（与结算页同一套项级口径）。
  final String state;

  final String? yourAnswer;
  final String? expected;
  final String? matchType;
  final List<int> steps;
  final int hintLevel;
  final bool usedKeyboard;

  bool get isCorrect => state == 'correct';
  bool get isEmpty => state == 'empty';
}

/// 语文漏字：`pos` 是**背诵范围内**的字符下标，`char` 由服务端重建。
class MissedChar {
  const MissedChar({required this.pos, this.char});

  factory MissedChar.fromJson(Map<String, dynamic> json) => MissedChar(
        pos: _int(json['pos']),
        char: _text(json['char']),
      );

  final int pos;
  final String? char;
}

/// 语文场次的按篇明细。
class RecitationRecord {
  const RecitationRecord({
    required this.recitationId,
    this.textId,
    this.title,
    this.totalChars = 0,
    this.matchedChars = 0,
    this.coveredRatio = 0,
    this.grade,
    this.score,
    this.durationMs = 0,
    this.restartCount = 0,
    this.finishReason,
    this.missedDetail = const [],
  });

  factory RecitationRecord.fromJson(Map<String, dynamic> json) => RecitationRecord(
        recitationId: _int(json['recitation_id']),
        textId: _intOrNull(json['text_id']),
        title: _text(json['title']),
        totalChars: _int(json['total_chars']),
        matchedChars: _int(json['matched_chars']),
        coveredRatio: _double(json['covered_ratio']),
        grade: _text(json['grade']),
        score: _doubleOrNull(json['score']),
        durationMs: _int(json['duration_ms']),
        restartCount: _int(json['restart_count']),
        finishReason: _text(json['finish_reason']),
        missedDetail: _mapList(json['missed_detail'], MissedChar.fromJson),
      );

  final int recitationId;
  final int? textId;
  final String? title;
  final int totalChars;
  final int matchedChars;
  final double coveredRatio;
  final String? grade;
  final double? score;
  final int durationMs;
  final int restartCount;
  final String? finishReason;
  final List<MissedChar> missedDetail;
}

/// `GET /api/sessions/{id}` 的完整响应。
///
/// 英语给 [items]（`recitations` 为 null），语文给 [recitations]（`items` 为 null）——
/// 这不是「有的字段没取到」，是两个学科本来就没有对方那种明细。
class SessionDetail {
  const SessionDetail({
    required this.summary,
    this.items = const [],
    this.recitations = const [],
  });

  factory SessionDetail.fromJson(Map<String, dynamic> json) => SessionDetail(
        summary: SessionRecord.fromJson(json),
        items: _mapList(json['items'], SessionItem.fromJson),
        recitations: _mapList(json['recitations'], RecitationRecord.fromJson),
      );

  final SessionRecord summary;
  final List<SessionItem> items;
  final List<RecitationRecord> recitations;
}

// ---------------------------------------------------------------------------
// 列表状态与控制器
// ---------------------------------------------------------------------------

/// 我的记录列表状态。
class SessionRecordsState {
  const SessionRecordsState({
    this.items = const [],
    this.total = 0,
    this.page = 1,
    this.loading = false,
    this.loadingMore = false,
    this.errorText,
  });

  final List<SessionRecord> items;
  final int total;
  final int page;
  final bool loading;
  final bool loadingMore;
  final String? errorText;

  bool get hasMore => items.length < total;

  SessionRecordsState copyWith({
    List<SessionRecord>? items,
    int? total,
    int? page,
    bool? loading,
    bool? loadingMore,
    String? errorText,
    bool clearError = false,
  }) =>
      SessionRecordsState(
        items: items ?? this.items,
        total: total ?? this.total,
        page: page ?? this.page,
        loading: loading ?? this.loading,
        loadingMore: loadingMore ?? this.loadingMore,
        errorText: clearError ? null : (errorText ?? this.errorText),
      );
}

/// 我的记录控制器（F2）。
///
/// 进入即拉第一页；「加载更多」按契约 `page` / `page_size` 续拉并合并。
class SessionRecordsController extends Notifier<SessionRecordsState> {
  static const int pageSize = 20;

  @override
  SessionRecordsState build() {
    // ⚠️ **不能在 `build()` 里同步调 `_load()`**：`_load` 第一件事就是读 `state`，
    // 而此刻 provider 还没初始化完，Riverpod 会抛
    // 「Tried to read the state of an uninitialized provider」。
    // 丢进 microtask，等 `build()` 返回（state 就位）后再发起请求。
    Future.microtask(() => _load(page: 1, replace: true));
    return const SessionRecordsState();
  }

  Future<void> _load({required int page, required bool replace}) async {
    state = state.copyWith(
      loading: replace,
      loadingMore: !replace,
      clearError: true,
    );
    try {
      final data = await ref.read(apiClientProvider).get<Map<String, dynamic>>(
            '/api/sessions',
            query: <String, dynamic>{'page': page, 'page_size': pageSize},
          );
      final payload = data ?? const <String, dynamic>{};
      final fetched = _mapList(payload['items'], SessionRecord.fromJson);
      state = state.copyWith(
        items: replace ? fetched : <SessionRecord>[...state.items, ...fetched],
        total: _int(payload['total']),
        page: page,
        loading: false,
        loadingMore: false,
      );
    } on ApiException catch (e) {
      state = state.copyWith(
        loading: false,
        loadingMore: false,
        errorText: e.displayText,
      );
    }
  }

  Future<void> reload() => _load(page: 1, replace: true);

  Future<void> loadMore() async {
    if (state.loading || state.loadingMore || !state.hasMore) {
      return;
    }
    await _load(page: state.page + 1, replace: false);
  }
}

/// 我的记录 Provider。
final sessionRecordsProvider =
    NotifierProvider<SessionRecordsController, SessionRecordsState>(
  SessionRecordsController.new,
);

/// 单次场次详情。按 `sessionId` 分族，页面直接 watch。
///
/// 服务端可能回业务错误（`ongoing` 场次 → `10003`），
/// 由 `ApiException` 走既有的中文错误展示，这里不吞。
final sessionDetailProvider =
    FutureProvider.family<SessionDetail, int>((ref, sessionId) async {
  final data = await ref.read(apiClientProvider).get<Map<String, dynamic>>(
        '/api/sessions/$sessionId',
      );
  return SessionDetail.fromJson(data ?? const <String, dynamic>{});
});

// ---------------------------------------------------------------------------
// 解析小工具（本文件私有；`_int` 这类在各 library 里各自私有是 Dart 惯例）
// ---------------------------------------------------------------------------

String? _text(Object? raw) {
  final value = raw?.toString().trim();
  if (value == null || value.isEmpty) {
    return null;
  }
  return value;
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

int? _intOrNull(Object? value) {
  if (value == null) {
    return null;
  }
  if (value is int) {
    return value;
  }
  if (value is num) {
    return value.toInt();
  }
  return int.tryParse(value.toString());
}

double _double(Object? value) {
  if (value is double) {
    return value;
  }
  if (value is num) {
    return value.toDouble();
  }
  return double.tryParse(value?.toString() ?? '') ?? 0.0;
}

double? _doubleOrNull(Object? value) {
  if (value == null) {
    return null;
  }
  if (value is double) {
    return value;
  }
  if (value is num) {
    return value.toDouble();
  }
  return double.tryParse(value.toString());
}

List<int> _intList(Object? raw) {
  if (raw is! List) {
    return const [];
  }
  final out = <int>[];
  for (final e in raw) {
    final v = _intOrNull(e);
    if (v != null) {
      out.add(v);
    }
  }
  return out;
}

List<T> _mapList<T>(Object? raw, T Function(Map<String, dynamic>) build) {
  if (raw is! List) {
    return const [];
  }
  final out = <T>[];
  for (final e in raw) {
    if (e is Map<String, dynamic>) {
      out.add(build(e));
    }
  }
  return out;
}
