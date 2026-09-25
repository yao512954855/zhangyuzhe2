/// 语文背诵页的业务编排层 —— WP-APP-04（F1~F16）。
///
/// 职责边界（与学生端共通约束一致）：
/// - **所有请求走 `apiClientProvider`**（第 7 条）：本文件是唯一碰背诵 6 个接口的地方；
/// - **对齐 / 判分 100% 在服务端**：客户端只渲染、只转发累计文本，绝不自己算
///   （契约 §13 禁止客户端自对齐；`/settle` 客户端上报的 `matched_chars` 服务端丢弃）；
/// - **语音走统一 `ws_client`**（第 7 条）：录音、推音频、收 `partial` 由页面负责，
///   本控制器只消费「累计识别文本」去推进进度（等同契约 `/api/recitations/{id}/feed`
///   这条「第二条通道」，也正好是可单测、可真机烟测的落点）。
///
/// 数据来源说明（与 F1「进入不显示正文」一致）：
/// 创建响应**只有计数与元信息**（`total_chars` / `char_count_per_paragraph` / `title`），
/// 不含正文、不含拼音。因此逐字点亮只能按「位置」渲染占位框，
/// 无法在客户端区分标点格（标点由服务端在比对层忽略）—— 这是契约层面的已知缺口，
/// 见交付说明偏离项 D-APP04-01。
library;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/audio/pcm_recorder.dart';
import '../../core/net/api_client.dart';
import '../../core/net/api_exception.dart';
import '../../core/providers.dart';

// ---------------------------------------------------------------------------
// 解析辅助（与 practice_session.dart 同风格，防御性转换）
// ---------------------------------------------------------------------------

int _toInt(Object? v) {
  if (v is int) return v;
  if (v is num) return v.toInt();
  return int.tryParse(v?.toString() ?? '') ?? 0;
}

List<int> _toIntList(Object? v) {
  if (v is! List) return const <int>[];
  final out = <int>[];
  for (final e in v) {
    final n = _toInt(e);
    if (n >= 0) out.add(n);
  }
  return out;
}

Map<int, int> _toHintCount(Object? v) {
  final map = <int, int>{};
  if (v is! Map) return map;
  v.forEach((key, value) {
    final level = _toInt(key);
    if (level >= 1 && level <= 3) {
      map[level] = _toInt(value);
    }
  });
  return map;
}

double _toDouble(Object? v) {
  if (v is num) return v.toDouble();
  return double.tryParse(v?.toString() ?? '') ?? 0.0;
}

// ---------------------------------------------------------------------------
// 数据模型
// ---------------------------------------------------------------------------

/// `POST /api/recitations` 的响应（**不含正文 / 拼音**，F1 明文）。
@immutable
class ReciteCreateResponse {
  const ReciteCreateResponse({
    required this.recitationId,
    required this.sessionId,
    required this.textId,
    required this.title,
    required this.scope,
    required this.totalChars,
    required this.charCountPerParagraph,
  });

  factory ReciteCreateResponse.fromJson(Map<String, dynamic> json) {
    return ReciteCreateResponse(
      recitationId: _toInt(json['recitation_id']),
      sessionId: _toInt(json['session_id']),
      textId: _toInt(json['text_id']),
      title: json['title']?.toString() ?? '',
      scope: _toIntList(json['scope']),
      totalChars: _toInt(json['total_chars']),
      charCountPerParagraph: _toIntList(json['char_count_per_paragraph']),
    );
  }

  final int recitationId;
  final int sessionId;
  final int textId;
  final String title;

  /// 背诵范围（段落下标）。
  final List<int> scope;

  /// 背诵范围内的总字数（逐字点亮按这个计数渲染占位框）。
  final int totalChars;

  /// 每段字数（UI 可据此分段换行）。
  final List<int> charCountPerParagraph;
}

/// `/feed` 的完整响应：进度快照 + 服务端附带的「建议提示」与噪声标记。
@immutable
class ReciteFeedResult {
  const ReciteFeedResult({
    required this.progress,
    this.hintSuggestion = 0,
    this.isNoise = false,
    this.newlyMatched = 0,
    this.newlyMatchedPositions = const <int>[],
  });

  final ReciteProgress progress;

  /// 服务端建议给几档提示（0 = 此刻无需）；**只建议不消耗**。
  final int hintSuggestion;

  /// 这一段是否被判定为噪声（完全背错 / 非中文）。
  final bool isNoise;

  /// 本轮新点亮的**字数**。
  final int newlyMatched;

  /// 本轮新点亮的位置（服务端 `newly_matched` 原样给的就是位置数组）。
  final List<int> newlyMatchedPositions;
}

/// 进度快照（`/feed` / `/restart` 响应的主体部分）。
@immutable
class ReciteProgress {
  const ReciteProgress({
    required this.pos,
    required this.totalChars,
    required this.matchedChars,
    required this.remaining,
    required this.hintCount,
    required this.restartCount,
    required this.missStreak,
    required this.finished,
  });

  factory ReciteProgress.fromSnapshot(Map<String, dynamic> json) {
    return ReciteProgress(
      pos: _toInt(json['pos']),
      totalChars: _toInt(json['total_chars']),
      matchedChars: _toInt(json['matched_chars']),
      remaining: _toInt(json['remaining']),
      hintCount: _toHintCount(json['hint_count']),
      restartCount: _toInt(json['restart_count']),
      missStreak: _toInt(json['miss_streak']),
      finished: json['finished'] == true,
    );
  }

  /// 认领边界（已背到的位置）。
  final int pos;

  final int totalChars;

  /// 实际点亮字数（可能小于 `pos`：中间有漏字）。
  final int matchedChars;

  final int remaining;

  /// 三档提示次数 `{1:.., 2:.., 3:..}`。
  final Map<int, int> hintCount;

  final int restartCount;
  final int missStreak;
  final bool finished;

  /// 三档提示总次数（扣分项口径，契约 §9.4 / scoring.py）。
  int get hintTotal =>
      (hintCount[1] ?? 0) + (hintCount[2] ?? 0) + (hintCount[3] ?? 0);
}

/// 一次提示的返回（`/hint` 响应）。
@immutable
class ReciteHint {
  const ReciteHint({
    required this.level,
    required this.content,
    required this.pos,
    required this.hintCount,
  });

  /// [level] 为 0 表示服务端判定「此刻不该提示」（返回 `level: null`）。
  factory ReciteHint.fromJson(Map<String, dynamic> json) {
    final rawLevel = json['level'];
    return ReciteHint(
      level: rawLevel == null ? 0 : _toInt(rawLevel),
      content: json['content']?.toString(),
      pos: _toInt(json['pos']),
      hintCount: _toHintCount(json['hint_count']),
    );
  }

  final int level;
  final String? content;
  final int pos;
  final Map<int, int> hintCount;
}

/// 漏字回看的一项（`/settle` 的 `missed_detail`）。
@immutable
class ReciteMissedChar {
  const ReciteMissedChar({required this.pos, this.char});

  factory ReciteMissedChar.fromJson(Map<String, dynamic> json) {
    return ReciteMissedChar(
      pos: _toInt(json['pos']),
      char: json['char']?.toString(),
    );
  }

  final int pos;

  /// 漏掉的字（服务端据 `target_chars` 回填；可能为 null）。
  final String? char;
}

/// 结算结果（`/settle` 响应，服务端重算）。
@immutable
class ReciteSettle {
  const ReciteSettle({
    required this.recitationId,
    required this.totalChars,
    required this.matchedChars,
    required this.missedCount,
    required this.missedDetail,
    required this.coveredRatio,
    required this.score,
    required this.grade,
    required this.hintCount,
    required this.replay,
  });

  factory ReciteSettle.fromJson(Map<String, dynamic> json) {
    final detailRaw = json['missed_detail'];
    final detail = detailRaw is List
        ? detailRaw
            .whereType<Map>()
            .map((e) => ReciteMissedChar.fromJson(Map<String, dynamic>.from(e)))
            .toList(growable: false)
        : const <ReciteMissedChar>[];
    return ReciteSettle(
      recitationId: _toInt(json['recitation_id']),
      totalChars: _toInt(json['total_chars']),
      matchedChars: _toInt(json['matched_chars']),
      missedCount: _toInt(json['missed_count']),
      missedDetail: detail,
      coveredRatio: _toDouble(json['covered_ratio']),
      score: _toDouble(json['score']),
      grade: json['grade']?.toString() ?? '',
      hintCount: _toHintCount(json['hint_count']),
      replay: json['replay'] == true,
    );
  }

  final int recitationId;
  final int totalChars;
  final int matchedChars;
  final int missedCount;
  final List<ReciteMissedChar> missedDetail;
  final double coveredRatio;
  final double score;
  final String grade;
  final Map<int, int> hintCount;
  final bool replay;

  int get hintTotal =>
      (hintCount[1] ?? 0) + (hintCount[2] ?? 0) + (hintCount[3] ?? 0);
}

/// 提示触发方（契约 `trigger_by`）。
enum HintTrigger {
  manual,
  auto;

  String get wire => switch (this) {
        HintTrigger.manual => 'manual',
        HintTrigger.auto => 'auto',
      };
}

/// 结束原因（契约 `finish_reason`）。
enum FinishReason {
  completed,
  manual,
  timeout;

  String get wire => switch (this) {
        FinishReason.completed => 'completed',
        FinishReason.manual => 'manual',
        FinishReason.timeout => 'timeout',
      };
}

/// 断线补报的单条事件（F15）。
///
/// 服务端 `recitation_events` **一期只写不读**，且本端只补报「服务端引擎没看到的
/// 客户端侧事件」（如 WS 断开期间产生的提示 / 重开），不重复服务端已记录的
/// matched / missed —— 因此即便重复上报也不会影响评分（契约 recite.py F2）。
@immutable
class ReciteEvent {
  const ReciteEvent({
    required this.eventType,
    this.pos = 0,
    this.char,
    this.hintLevel,
    this.triggerBy,
    this.elapsedMs,
  });

  factory ReciteEvent.hint({
    required int pos,
    String? char,
    required int hintLevel,
    required HintTrigger triggerBy,
  }) =>
      ReciteEvent(
        eventType: 'hint',
        pos: pos,
        char: char,
        hintLevel: hintLevel,
        triggerBy: triggerBy,
      );

  factory ReciteEvent.restart({required int pos}) =>
      ReciteEvent(eventType: 'restart', pos: pos);

  final String eventType;
  final int pos;
  final String? char;
  final int? hintLevel;
  final HintTrigger? triggerBy;
  final int? elapsedMs;

  Map<String, dynamic> toJson() => {
        'event_type': eventType,
        'pos': pos,
        if (char != null) 'char': char,
        if (hintLevel != null) 'hint_level': hintLevel,
        if (triggerBy != null) 'trigger_by': triggerBy!.wire,
        if (elapsedMs != null) 'elapsed_ms': elapsedMs,
      };
}

/// 控制器阶段，驱动页面整体状态切换。
enum RecitePhase {
  initial,
  creating,
  ready,
  reciting,
  settling,
  settled,
  failed;

  bool get isBusy =>
      this == RecitePhase.creating || this == RecitePhase.settling;
}

/// 控制器对外暴露的不可变状态。
@immutable
class ReciteState {
  const ReciteState({
    this.phase = RecitePhase.initial,
    this.create,
    this.progress,
    this.hint,
    this.suggestedHintLevel = 0,
    this.settle,
    this.errorMessage,
  });

  final RecitePhase phase;
  final ReciteCreateResponse? create;
  final ReciteProgress? progress;

  /// 最近一次下发的提示（页面据此渲染提示条）。
  final ReciteHint? hint;

  /// `/feed` 返回的「建议给提示」档位（0 = 无需）；仅提示不消耗。
  final int suggestedHintLevel;

  final ReciteSettle? settle;

  /// 上一次失败的中文提示（可为 null）。
  final String? errorMessage;

  ReciteState copyWith({
    RecitePhase? phase,
    ReciteCreateResponse? create,
    ReciteProgress? progress,
    ReciteHint? hint,
    bool clearHint = false,
    int? suggestedHintLevel,
    ReciteSettle? settle,
    String? errorMessage,
    bool clearError = false,
  }) {
    return ReciteState(
      phase: phase ?? this.phase,
      create: create ?? this.create,
      progress: progress ?? this.progress,
      hint: clearHint ? hint : (hint ?? this.hint),
      suggestedHintLevel: suggestedHintLevel ?? this.suggestedHintLevel,
      settle: settle ?? this.settle,
      errorMessage: clearError ? errorMessage : (errorMessage ?? this.errorMessage),
    );
  }
}

// ---------------------------------------------------------------------------
// 仓库：唯一碰背诵接口的地方
// ---------------------------------------------------------------------------

class ReciteRepository {
  ReciteRepository({required this.api});

  final ApiClient api;

  /// F1：开始一次背诵。
  Future<ReciteCreateResponse> create({
    required int sessionId,
    required int textId,
    required List<int> scope,
  }) async {
    final data = await api.post<Map<String, dynamic>>(
      '/api/recitations',
      body: {
        'session_id': sessionId,
        'text_id': textId,
        'scope': scope,
      },
    );
    return ReciteCreateResponse.fromJson(data ?? const <String, dynamic>{});
  }

  /// 喂入累计识别文本，返回推进后的进度 + 建议提示。
  Future<ReciteFeedResult> feed(
    int recitationId, {
    required String partialText,
    int silenceMs = 0,
  }) async {
    final data = await api.post<Map<String, dynamic>>(
      '/api/recitations/$recitationId/feed',
      body: {
        'partial_text': partialText,
        'silence_ms': silenceMs,
      },
    );
    final json = data ?? const <String, dynamic>{};
    // ⚠️ `newly_matched` 服务端下发的是**位置数组**（BE-05 `FeedResult.newly_matched: list[int]`，
    //    例：feed("我与父亲") → [0,1,2,3]），不是数字。
    //    早先这里写 `_toInt(json['newly_matched'])` —— 对数组恒得 0，
    //    假适配器的组件测试永远绿，只有真机联调（`smoke_app04_live_test.dart`）才发现。
    final rawNewly = json['newly_matched'];
    final positions = _toIntList(rawNewly);
    return ReciteFeedResult(
      progress: ReciteProgress.fromSnapshot(json),
      hintSuggestion: _toInt(json['hint_suggestion']),
      isNoise: json['is_noise'] == true,
      // 兼容两种形状：数组（现契约）取长度，数字（若服务端将来改成计数）原样用。
      newlyMatched: rawNewly is List ? positions.length : _toInt(rawNewly),
      newlyMatchedPositions: positions,
    );
  }

  /// 取一次提示。
  ///
  /// [level] 为 null 时由服务端按静音 / 连续漏字自动判定（返回 [ReciteHint.level]==0
  /// 表示「此刻不该提示」）。
  Future<ReciteHint> requestHint(
    int recitationId, {
    int? level,
    HintTrigger triggerBy = HintTrigger.auto,
    int silenceMs = 0,
  }) async {
    final data = await api.post<Map<String, dynamic>>(
      '/api/recitations/$recitationId/hint',
      body: {
        'level': level,
        'trigger_by': triggerBy.wire,
        'silence_ms': silenceMs,
      },
    );
    return ReciteHint.fromJson(data ?? const <String, dynamic>{});
  }

  /// 重新开始本段（F9）。
  Future<ReciteProgress> restart(int recitationId) async {
    final data = await api.post<Map<String, dynamic>>(
      '/api/recitations/$recitationId/restart',
    );
    return ReciteProgress.fromSnapshot(data ?? const <String, dynamic>{});
  }

  /// F15：断线补报事件流。返回服务端接受的条数（>=0）。
  Future<int> reportEvents(int recitationId, List<ReciteEvent> events) async {
    if (events.isEmpty) return 0;
    final data = await api.post<Map<String, dynamic>>(
      '/api/recitations/$recitationId/events',
      body: {
        'events': events.map((e) => e.toJson()).toList(growable: false),
      },
    );
    final accepted = data?['accepted'];
    return accepted is num ? accepted.toInt() : events.length;
  }

  /// F12/F13：结算（服务端重算，客户端数据仅参考）。
  Future<ReciteSettle> settle(
    int recitationId, {
    required String transcript,
    required int restartCount,
    required int durationMs,
    int? firstVoiceMs,
    required FinishReason finishReason,
  }) async {
    final data = await api.post<Map<String, dynamic>>(
      '/api/recitations/$recitationId/settle',
      body: {
        'transcript': transcript,
        'restart_count': restartCount,
        'duration_ms': durationMs,
        'first_voice_ms': firstVoiceMs,
        'finish_reason': finishReason.wire,
      },
    );
    return ReciteSettle.fromJson(data ?? const <String, dynamic>{});
  }
}

// ---------------------------------------------------------------------------
// 控制器：背诵状态机
// ---------------------------------------------------------------------------

/// 仓库 Provider（依赖 `apiClientProvider`，测试时 override 即可）。
final reciteRepositoryProvider = Provider<ReciteRepository>((ref) {
  return ReciteRepository(api: ref.watch(apiClientProvider));
});

/// 录音器 Provider（页面从它取，测试时 override 成假实现即可，
/// 避免组件测试真的去申请麦克风 / 拉起系统录音）。
final pcmRecorderProvider = Provider<PcmRecorder>((ref) {
  return PcmRecorder();
});

/// 背诵状态机。
///
/// 页面负责录音 / WS / 计时，并在关键节点调用这里的方法推进状态。
/// 本控制器只持有「数据态」（创建信息、进度、当前提示、结算），
/// 不持有录音机 / 计时器 / WS —— 这些是页面生命周期对象，不该进 riverpod 状态。
class ReciteController extends Notifier<ReciteState> {
  ReciteRepository get _repo => ref.read(reciteRepositoryProvider);

  /// 客户端侧攒下的、待在结算时补报的事件（F15）。
  final List<ReciteEvent> _pendingEvents = <ReciteEvent>[];

  @override
  ReciteState build() => const ReciteState();

  /// F1：创建背诵，进入「准备开始」态。
  Future<ReciteCreateResponse> start({
    required int sessionId,
    required int textId,
    required List<int> scope,
  }) async {
    state = state.copyWith(phase: RecitePhase.creating, clearError: true);
    try {
      final create = await _repo.create(
        sessionId: sessionId,
        textId: textId,
        scope: scope,
      );
      _pendingEvents.clear();
      state = state.copyWith(
        phase: RecitePhase.ready,
        create: create,
        progress: ReciteProgress(
          pos: 0,
          totalChars: create.totalChars,
          matchedChars: 0,
          remaining: create.totalChars,
          hintCount: const <int, int>{},
          restartCount: 0,
          missStreak: 0,
          finished: false,
        ),
      );
      return create;
    } on ApiException catch (e) {
      _fail(e.displayText);
      rethrow;
    }
  }

  /// F5/F6：把累计识别文本喂给引擎，刷新进度。
  ///
  /// [silenceMs] 透传静音时长，供服务端在响应里附「建议提示档位」。
  Future<void> feed(String partialText, {int silenceMs = 0}) async {
    final create = state.create;
    if (create == null) return;
    try {
      final result = await _repo.feed(
        create.recitationId,
        partialText: partialText,
        silenceMs: silenceMs,
      );
      state = state.copyWith(
        phase: RecitePhase.reciting,
        progress: result.progress,
        suggestedHintLevel: result.hintSuggestion,
        clearError: true,
      );
    } on ApiException catch (e) {
      // feed 失败**不应阻断录音**：保留进度，只把错误记到 errorMessage，
      // 由页面以内联提示呈现（不切到 failed 全屏，否则会话直接被打断）。
      _note(e.displayText);
    }
  }

  /// F7：取一次提示（手动「我卡住了」或自动静音降档）。
  ///
  /// [level] 为 null → 服务端自动判定。返回是否真的拿到了提示（服务端可能判定
  /// 「此刻不该提示」，即 [ReciteHint.level]==0）。
  Future<bool> requestHint({
    int? level,
    required HintTrigger triggerBy,
    int silenceMs = 0,
  }) async {
    final create = state.create;
    if (create == null) return false;
    try {
      final hint = await _repo.requestHint(
        create.recitationId,
        level: level,
        triggerBy: triggerBy,
        silenceMs: silenceMs,
      );
      if (hint.level <= 0) {
        // 服务端判定此刻不该提示：不更新提示态，也不报错
        return false;
      }
      if (triggerBy == HintTrigger.manual) {
        _pendingEvents.add(
          ReciteEvent.hint(
            pos: hint.pos,
            char: hint.content,
            hintLevel: hint.level,
            triggerBy: triggerBy,
          ),
        );
      }
      state = state.copyWith(
        hint: hint,
        progress: state.progress?.copyWithHintCount(hint.hintCount),
        clearError: true,
      );
      return true;
    } on ApiException catch (e) {
      // 提示取不到：只记到 errorMessage，不把整页切成 failed（卡住时还能继续背）。
      _note(e.displayText);
      return false;
    }
  }

  /// F9：重新开始本段（二次确认由页面负责）。
  Future<void> restart() async {
    final create = state.create;
    if (create == null) return;
    try {
      final progress = await _repo.restart(create.recitationId);
      _pendingEvents.add(ReciteEvent.restart(pos: progress.pos));
      state = state.copyWith(
        hint: const ReciteHint(level: 0, content: null, pos: 0, hintCount: <int, int>{}),
        progress: progress,
        clearHint: true,
        clearError: true,
      );
    } on ApiException catch (e) {
      _fail(e.displayText);
    }
  }

  /// F15：结算前把客户端侧事件补报上去（服务端只写不读，不影响评分）。
  Future<void> flushEvents() async {
    final create = state.create;
    if (create == null || _pendingEvents.isEmpty) return;
    final pending = List<ReciteEvent>.from(_pendingEvents);
    _pendingEvents.clear();
    try {
      await _repo.reportEvents(create.recitationId, pending);
    } on ApiException {
      // 事件补报失败不阻断结算：服务端已用 transcript 重算，事件只作二期回放素材
    }
  }

  /// F12/F13：结算。先补报事件，再请求服务端重算。
  Future<ReciteSettle> settle({
    required String transcript,
    required int durationMs,
    int? firstVoiceMs,
    required FinishReason finishReason,
  }) async {
    final create = state.create;
    if (create == null) {
      throw StateError('尚未开始背诵，无法结算');
    }
    state = state.copyWith(phase: RecitePhase.settling, clearError: true);
    try {
      await flushEvents();
      final result = await _repo.settle(
        create.recitationId,
        transcript: transcript,
        restartCount: state.progress?.restartCount ?? 0,
        durationMs: durationMs,
        firstVoiceMs: firstVoiceMs,
        finishReason: finishReason,
      );
      state = state.copyWith(phase: RecitePhase.settled, settle: result);
      return result;
    } on ApiException catch (e) {
      _fail(e.displayText);
      rethrow;
    }
  }

  /// 手动清除当前提示（例如学生继续背诵后，提示条应消失）。
  void clearHint() {
    if (state.hint != null) {
      state = state.copyWith(clearHint: true);
    }
  }

  void _fail(String message) {
    state = state.copyWith(phase: RecitePhase.failed, errorMessage: message);
  }

  /// 记一条错误提示但**不改变阶段**（用于 feed / hint 这类"失败不应阻断流程"的环节）。
  void _note(String message) {
    state = state.copyWith(errorMessage: message);
  }

  /// 重置（离开页面时）。
  void reset() {
    _pendingEvents.clear();
    state = const ReciteState();
  }
}

final reciteControllerProvider =
    NotifierProvider<ReciteController, ReciteState>(ReciteController.new);

// 给 ReciteProgress 加一个便捷拷贝（仅更新 hintCount）。
extension _ReciteProgressHint on ReciteProgress {
  ReciteProgress copyWithHintCount(Map<int, int> hintCount) =>
      ReciteProgress(
        pos: pos,
        totalChars: totalChars,
        matchedChars: matchedChars,
        remaining: remaining,
        hintCount: hintCount,
        restartCount: restartCount,
        missStreak: missStreak,
        finished: finished,
      );
}
