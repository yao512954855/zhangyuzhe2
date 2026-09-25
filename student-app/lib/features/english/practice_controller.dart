/// 英语练习页的业务编排层 —— WP-APP-03（F1 ~ F18）。
///
/// 职责边界（与学生端共通约束一致）：
/// - **所有请求走 `apiClientProvider`**（第 7 条）：本文件是唯一碰
///   `POST /api/sessions/{id}/answers` / `finish` / `appeal` 的地方；
/// - **判分 / 对齐 / 差异计算 100% 在服务端**（第 8 条）：客户端只渲染、只转发
///   `diff`，绝不自己算（契约 §13）；
/// - **语音走统一 `ws_client`**（第 7 条）：但**录音 / 推音频 / 收 verdict** 由页面负责
///   （需要麦克风与二进制流，不适合放进 riverpod 状态），本控制器只消费「判定结果」
///   去推进状态机 —— 等同 recite_controller 的「页面持有 IO、控制器持有数据态」分工。
///
/// ⚠️ 两个**契约缺口 / 偏离**（独立复核必看，交付说明 D-APP03 系列有详述）：
/// D-APP03-01 `Idempotency-Key` 头：F15 / §9.2 要求写操作带此头防重。
///   **已修复**：`api_client.dart`（WP-APP-01）新增了
///   `RequestOptionsOverride.headers`（默认 `null`，对既有调用方零影响），
///   本文件据此把 `buildIdempotencyKey()` 真正接到了提交请求上。
///   注：附加头在校验层被刻意排在 `Authorization` **之前**，所以调用方无法用它绕鉴权。
/// D-APP03-02 英语提示端点缺失 → **2026-09-25 已裁决（B 方案），代码按裁决回撤**：
///   三档提示**划归语文**（需求 §5.7「学科范围」，见 `docs/dev/02-实现约定与复用经验.md` §八 #71）。
///   英语侧只剩**一档**可用（占位框数量，客户端本地可渲染）；「二档首字母 / 三档完整答案」
///   需要服务端下发答案侧信息，但 §9.1 明文「组卷响应**绝不返回答案**」，且 hint 端点只存在于
///   §9.4 的**语文**背诵通道 → **无来源**。故 `kEnglishMaxHintLevel = 1`，
///   **不再摆走不通的二/三档入口**（原先「已升到第 2/3 档」的虚指文案已删除）。
///
/// D-APP03-03 超时口径 —— **复核后确认不是缺口**，原文表述已更正：
///   U11 要求单题限时到点判 `match_type='timeout'`。客户端按限时到点提交
///   `asr_text:''` 并**如实上报 `duration_ms`**；服务端
///   `SessionService._is_time_over()` 会按 `duration_ms >= sessions.time_limit_ms`
///   兜底判超时（`timed_out` 字段只是客户端可选的提前声明，**不是必要条件**），
///   行为 A 下走 `_timeout_verdict()` 直接给 `match_type='timeout'`。
///   已读代码确认，无需客户端额外字段。
library;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_client.dart';
import '../../core/net/api_exception.dart';
import '../../core/net/ws_client.dart';
import '../../core/providers.dart';
import '../config/practice_session.dart';
import '../config/practice_strategy.dart';

/// 英语侧提示档位上限（**单一真源**：控制器与 `HintButton` 都读它）。
///
/// 一档 = 显示占位框数量（本地可渲染）；二/三档需服务端下发答案侧信息，
/// 英语通道（接口设计 §9.1）明文「组卷响应绝不返回答案」且**无 hint 端点**
/// → 2026-09-25 裁决 B：三档提示划归语文，英语一期只做一档。
/// 见需求 §5.7「学科范围」、§八 #71。
const int kEnglishMaxHintLevel = 1;

// ---------------------------------------------------------------------------
// 步骤类型
// ---------------------------------------------------------------------------

/// 一步的题型（决定 WS `task_type` 与是否拼读）。
enum PracticeStepKind {
  /// 说单词（模式一 step1）。WS `task_type=word`。
  word,

  /// 拼读（模式一 step2）。WS `task_type=spell`。
  spell,

  /// 说中文（模式二）。WS `task_type=zh_answer`。
  zhAnswer;

  /// 对应契约 §6.3 的 WS `task_type`。
  String get taskType => switch (this) {
        PracticeStepKind.word => WsTaskType.word,
        PracticeStepKind.spell => WsTaskType.spell,
        PracticeStepKind.zhAnswer => WsTaskType.zhAnswer,
      };

  /// 这一步的口语化标题。
  String get label => switch (this) {
        PracticeStepKind.word => '说单词',
        PracticeStepKind.spell => '拼读',
        PracticeStepKind.zhAnswer => '说中文',
      };
}

// ---------------------------------------------------------------------------
// 判定 / 回显数据模型
// ---------------------------------------------------------------------------

/// `diff` 里的一个单元格（驱动 P7 逐字母回显）。
@immutable
class AnswerDiffCell {
  const AnswerDiffCell({
    required this.pos,
    required this.expected,
    required this.actual,
    required this.state,
  });

  factory AnswerDiffCell.fromJson(Map<String, dynamic> json) {
    return AnswerDiffCell(
      pos: _toInt(json['pos']),
      expected: json['expected']?.toString() ?? '',
      actual: json['actual']?.toString() ?? '',
      state: json['state']?.toString() ?? 'match',
    );
  }

  /// 位置（从 0 起）。
  final int pos;

  /// 标准答案的该位（字母）。
  final String expected;

  /// 学生读出的该位。
  final String actual;

  /// `match` / `wrong` / `missing` / `extra`（契约 §10.4）。
  final String state;

  bool get isWrong => state == 'wrong';
  bool get isMissing => state == 'missing';
  bool get isExtra => state == 'extra';
}

/// 一次判定结果（语音来自 WS `verdict`，键盘来自 HTTP 响应）。
@immutable
class AnswerVerdict {
  const AnswerVerdict({
    required this.isCorrect,
    required this.matchType,
    this.asrText,
    this.expected,
    this.answerId,
    this.canAppeal = false,
    this.diff = const <AnswerDiffCell>[],
    this.confidence,
    this.empty = false,
  });

  /// 识别为空（`error.code=50003`）：不计错、可重录。
  const AnswerVerdict.empty()
      : isCorrect = false,
        matchType = '',
        asrText = null,
        expected = null,
        answerId = null,
        canAppeal = false,
        diff = const <AnswerDiffCell>[],
        confidence = null,
        empty = true;

  factory AnswerVerdict.fromJson(Map<String, dynamic> json) {
    final rawDiff = json['diff'];
    final diff = rawDiff is List
        ? rawDiff
            .whereType<Map>()
            .map((e) => AnswerDiffCell.fromJson(Map<String, dynamic>.from(e)))
            .toList(growable: false)
        : const <AnswerDiffCell>[];
    final answerId = _toInt(json['answer_id']);
    return AnswerVerdict(
      isCorrect: json['is_correct'] == true,
      matchType: json['match_type']?.toString() ?? 'wrong',
      asrText: json['asr_text']?.toString(),
      expected: json['expected']?.toString(),
      answerId: answerId == 0 ? null : answerId,
      canAppeal: json['can_appeal'] == true,
      diff: diff,
      confidence: _toDouble(json['confidence']),
    );
  }

  final bool isCorrect;
  final String matchType;
  final String? asrText;
  final String? expected;
  final int? answerId;
  final bool canAppeal;
  final List<AnswerDiffCell> diff;
  final double? confidence;

  /// 识别为空（不计错）。
  final bool empty;
}

/// 本地记下的「一步提交 / 判定」记录（voice 与 keyboard 两条通道都会产生）。
///
/// 语音通道由 WS `verdict` 填充（服务端已在 WS `end` 时落库，见 §10.3）；
/// 键盘通道由 HTTP `POST /api/sessions/{id}/answers` 落库（§9.2）。
/// 本地这份镜像用于驱动翻页与题内进度展示，等于服务端持久化那一份。
@immutable
class AnswerRecord {
  const AnswerRecord({
    required this.seq,
    required this.step,
    required this.stepKind,
    required this.mode,
    required this.wordId,
    required this.isCorrect,
    required this.matchType,
    required this.usedKeyboard,
    required this.retryCount,
    required this.hintLevel,
    required this.usedHintCount,
    required this.durationMs,
    required this.firstVoiceMs,
    this.asrText,
    this.answerId,
    this.diff = const <AnswerDiffCell>[],
    this.canAppeal = false,
    this.appealed = false,
    this.empty = false,
  });

  final int seq;
  final int step;
  final PracticeStepKind stepKind;
  final String mode;
  final int? wordId;
  final bool isCorrect;
  final String matchType;
  final bool usedKeyboard;
  final int retryCount;
  final int hintLevel;
  final int usedHintCount;
  final int durationMs;
  final int firstVoiceMs;
  final String? asrText;
  final int? answerId;
  final List<AnswerDiffCell> diff;
  final bool canAppeal;
  final bool appealed;
  final bool empty;
}

// ---------------------------------------------------------------------------
// 结算数据模型（§9.3 响应）
// ---------------------------------------------------------------------------

/// 结算里的一条错题（§9.3 `wrong_items`）。
@immutable
class WrongItem {
  const WrongItem({
    required this.wordId,
    required this.en,
    required this.cn,
    required this.yourAnswer,
    required this.matchType,
  });

  factory WrongItem.fromJson(Map<String, dynamic> json) {
    return WrongItem(
      wordId: _toInt(json['word_id']),
      en: json['en']?.toString() ?? '',
      cn: json['cn']?.toString() ?? '',
      yourAnswer: json['your_answer']?.toString() ?? '',
      matchType: json['match_type']?.toString() ?? 'wrong',
    );
  }

  final int wordId;
  final String en;
  final String cn;
  final String yourAnswer;
  final String matchType;
}

/// `POST /api/sessions/{id}/finish` 的响应（服务端重算，客户端直接渲染）。
@immutable
class PracticeSettle {
  const PracticeSettle({
    required this.sessionId,
    required this.totalItems,
    required this.correctItems,
    required this.accuracy,
    required this.score,
    required this.hintCount,
    required this.avgDurationMs,
    required this.durationMs,
    required this.wrongItems,
    required this.improved,
  });

  factory PracticeSettle.fromJson(Map<String, dynamic> json) {
    final rawWrong = json['wrong_items'];
    final wrong = rawWrong is List
        ? rawWrong
            .whereType<Map>()
            .map((e) => WrongItem.fromJson(Map<String, dynamic>.from(e)))
            .toList(growable: false)
        : const <WrongItem>[];
    return PracticeSettle(
      sessionId: _toInt(json['session_id']),
      totalItems: _toInt(json['total_items']),
      correctItems: _toInt(json['correct_items']),
      accuracy: _toDouble(json['accuracy']),
      score: _toDouble(json['score']),
      hintCount: _toInt(json['hint_count']),
      avgDurationMs: _toInt(json['avg_duration_ms']),
      durationMs: _toInt(json['duration_ms']),
      wrongItems: wrong,
      improved: json['improved'] == true,
    );
  }

  final int sessionId;
  final int totalItems;
  final int correctItems;
  final double accuracy;
  final double score;
  final int hintCount;
  final int avgDurationMs;
  final int durationMs;
  final List<WrongItem> wrongItems;
  final bool improved;

  /// 评级（口语化，不用「差/不及格」等打击性词）。
  String get grade => switch (score) {
        >= 90 => '优秀',
        >= 80 => '良好',
        >= 60 => '加油',
        _ => '继续练习',
      };
}

// ---------------------------------------------------------------------------
// 解析辅助
// ---------------------------------------------------------------------------

int _toInt(Object? v) {
  if (v is int) return v;
  if (v is num) return v.toInt();
  return int.tryParse(v?.toString() ?? '') ?? 0;
}

double _toDouble(Object? v) {
  if (v is num) return v.toDouble();
  return double.tryParse(v?.toString() ?? '') ?? 0.0;
}

// ---------------------------------------------------------------------------
// 仓库：唯一碰英语答题 / 结算 / 申诉接口的地方
// ---------------------------------------------------------------------------

/// 英语练习的对外仓库接口（抽象，便于组件测试用假实现覆盖）。
abstract class PracticeRepository {
  /// 键盘兜底提交（落库 + 服务端判分）。语音通道由 WS 负责，不走这里。
  ///
  /// 字段名逐个对着 §9.2 写：`seq` / `word_id` / `mode` / `step` / `asr_text` /
  /// `answer_text` / `duration_ms` / `first_voice_ms` / `retry_count` /
  /// `used_keyboard` / `hint_level` / `used_hint_count` / `asr_confidence`。
  Future<AnswerVerdict> submitAnswer({
    required int sessionId,
    required int seq,
    required int? wordId,
    required String mode,
    required int step,
    required String asrText,
    required int durationMs,
    required int firstVoiceMs,
    required bool usedKeyboard,
    required int retryCount,
    required int hintLevel,
    required int usedHintCount,
    double? confidence,
    /// F15 / §9.2 的 `Idempotency-Key`。`null` 表示不带该头（仅测试用）。
    String? idempotencyKey,
  });

  /// 结束练习（reason: `completed` / `abandoned` / `timeout`，§9.3）。
  Future<PracticeSettle> finish({required int sessionId, required String reason});

  /// 学生申诉（§8.9）；不改判分，只置 `appealed=1`。
  Future<void> appeal({required int answerId, String? reason});
}

/// 走真实 `apiClient` 的仓库实现。
class HttpPracticeRepository implements PracticeRepository {
  HttpPracticeRepository({required this.api});

  final ApiClient api;

  @override
  Future<AnswerVerdict> submitAnswer({
    required int sessionId,
    required int seq,
    required int? wordId,
    required String mode,
    required int step,
    required String asrText,
    required int durationMs,
    required int firstVoiceMs,
    required bool usedKeyboard,
    required int retryCount,
    required int hintLevel,
    required int usedHintCount,
    double? confidence,
    String? idempotencyKey,
  }) async {
    final data = await api.post<Map<String, dynamic>>(
      '/api/sessions/$sessionId/answers',
      body: {
        'seq': seq,
        'word_id': wordId,
        'mode': mode,
        'step': step,
        'asr_text': asrText,
        'answer_text': null,
        'duration_ms': durationMs,
        'first_voice_ms': firstVoiceMs,
        'retry_count': retryCount,
        'used_keyboard': usedKeyboard,
        'hint_level': hintLevel,
        'used_hint_count': usedHintCount,
        'asr_confidence': confidence,
      },
      // F15 / §9.2：写操作必须带幂等键，弱网重试才不会重复计分。
      // 键由控制器生成（见 `PracticeController.buildIdempotencyKey`），
      // 经 `RequestOptionsOverride.headers` 附加（WP-APP-01 已支持自定义头）。
      override: idempotencyKey == null
          ? null
          : RequestOptionsOverride(headers: {'Idempotency-Key': idempotencyKey}),
    );
    return AnswerVerdict.fromJson(data ?? const <String, dynamic>{});
  }

  @override
  Future<PracticeSettle> finish({required int sessionId, required String reason}) async {
    final data = await api.post<Map<String, dynamic>>(
      '/api/sessions/$sessionId/finish',
      body: {'reason': reason},
    );
    return PracticeSettle.fromJson(data ?? const <String, dynamic>{});
  }

  @override
  Future<void> appeal({required int answerId, String? reason}) async {
    await api.post<Map<String, dynamic>>(
      '/api/answers/$answerId/appeal',
      body: reason == null ? const <String, dynamic>{} : {'reason': reason},
    );
  }
}

final practiceRepositoryProvider = Provider<PracticeRepository>((ref) {
  return HttpPracticeRepository(api: ref.watch(apiClientProvider));
});

// ---------------------------------------------------------------------------
// 控制器状态机
// ---------------------------------------------------------------------------

/// 控制器阶段。
enum PracticePhase {
  /// 等待学生开口 / 打字。
  asking,

  /// 已出判定，展示回显，等「继续 / 再试一次」（F6 不自动消失）。
  result,

  /// 整场结束（已结算）。
  finished,

  /// 出错（网络等，非答题失败）。
  error;

  bool get isBusy => this == finished;
}

/// 控制器对外暴露的不可变状态。
@immutable
class PracticeState {
  const PracticeState({
    this.session = const PracticeSession(sessionId: 0, items: []),
    this.itemIndex = 0,
    this.step = 1,
    this.stepKind = PracticeStepKind.word,
    this.phase = PracticePhase.asking,
    this.lastVerdict,
    this.lastRecord,
    this.failuresForStep = 0,
    this.hintLevel = 0,
    this.usedHintCount = 0,
    this.keyboardActive = false,
    this.settle,
    this.errorMessage,
    this.timedOut = false,
    this.records = const <AnswerRecord>[],
    this.appealed = false,
  });

  final PracticeSession session;
  final int itemIndex;
  final int step;
  final PracticeStepKind stepKind;
  final PracticePhase phase;

  final AnswerVerdict? lastVerdict;
  final AnswerRecord? lastRecord;

  /// 当前步已失败的语音次数（0 = 还没失败过）。
  final int failuresForStep;

  /// 当前已升到的提示档（0 = 未用提示）。
  final int hintLevel;

  /// 当前步已用的提示总次数。
  final int usedHintCount;

  /// 是否处于键盘兜底输入态（F7 / F8 / U7 / U13）。
  final bool keyboardActive;

  final PracticeSettle? settle;
  final String? errorMessage;

  /// 当前步因限时到点被判错（U11）。
  final bool timedOut;

  /// 本场已记录的所有「步」（voice + keyboard 都会记）。
  final List<AnswerRecord> records;

  /// 最近一次判定的申诉是否已提交。
  final bool appealed;

  PracticeState copyWith({
    PracticeSession? session,
    int? itemIndex,
    int? step,
    PracticeStepKind? stepKind,
    PracticePhase? phase,
    AnswerVerdict? lastVerdict,
    bool clearVerdict = false,
    AnswerRecord? lastRecord,
    int? failuresForStep,
    int? hintLevel,
    int? usedHintCount,
    bool? keyboardActive,
    PracticeSettle? settle,
    String? errorMessage,
    bool clearError = false,
    bool? timedOut,
    List<AnswerRecord>? records,
    bool? appealed,
  }) {
    return PracticeState(
      session: session ?? this.session,
      itemIndex: itemIndex ?? this.itemIndex,
      step: step ?? this.step,
      stepKind: stepKind ?? this.stepKind,
      phase: phase ?? this.phase,
      lastVerdict: clearVerdict ? null : (lastVerdict ?? this.lastVerdict),
      lastRecord: lastRecord ?? this.lastRecord,
      failuresForStep: failuresForStep ?? this.failuresForStep,
      hintLevel: hintLevel ?? this.hintLevel,
      usedHintCount: usedHintCount ?? this.usedHintCount,
      keyboardActive: keyboardActive ?? this.keyboardActive,
      settle: settle ?? this.settle,
      errorMessage: clearError ? errorMessage : (errorMessage ?? this.errorMessage),
      timedOut: timedOut ?? this.timedOut,
      records: records ?? this.records,
      appealed: appealed ?? this.appealed,
    );
  }
}

/// 英语练习状态机。
///
/// 页面负责：WS 连接 / 录音 / 推音频 / 解析 `verdict` / 计时；并在关键节点调用
/// 这里的方法推进状态。本控制器只持有「数据态」与「流程决策」，不持有
/// 麦克风 / 计时器 / WS —— 这些是页面生命周期对象，不该进 riverpod 状态。
class PracticeController extends Notifier<PracticeState> {
  PracticeRepository get _repo => ref.read(practiceRepositoryProvider);

  /// 幂等键计数器（会话内自增，保证唯一）。D-APP03-01：仅生成，传输待接入。
  int _idemCounter = 0;

  @override
  PracticeState build() => const PracticeState();

  // ----------------------------------------------------------- 会话初始化

  /// 用组卷响应启动一场练习（页面在 initState 调一次）。
  void start(PracticeSession session) {
    if (state.session.sessionId != 0) {
      return; // 已启动，避免重建重复初始化
    }
    if (session.isEmpty) {
      state = state.copyWith(
        session: session,
        phase: PracticePhase.error,
        errorMessage: '这次练习一道题都没有，先回去选点内容吧',
      );
      return;
    }
    final first = session.items.first;
    state = state.copyWith(
      session: session,
      itemIndex: 0,
      step: 1,
      stepKind: _steps(first).first,
      phase: PracticePhase.asking,
      failuresForStep: 0,
      hintLevel: 0,
      usedHintCount: 0,
      keyboardActive: false,
      clearVerdict: true,
      records: const <AnswerRecord>[],
    );
  }

  // ----------------------------------------------------------- 题目 / 步骤推导

  PracticeItem? get currentItem {
    final items = state.session.items;
    if (itemIndexOutOfRange) return null;
    return items[state.itemIndex];
  }

  bool get itemIndexOutOfRange =>
      state.itemIndex < 0 || state.itemIndex >= state.session.items.length;

  /// 该题的练习模式（cn2en / en2cn；mixed 由服务端按题分配 mode）。
  EnglishMode _itemMode(PracticeItem item) {
    if (item.mode == 'en2cn') return EnglishMode.en2cn;
    return EnglishMode.cn2en;
  }

  /// 该题的步骤序列：模式一 = [word, spell]，模式二 = [zhAnswer]。
  List<PracticeStepKind> _steps(PracticeItem item) {
    if (_itemMode(item) == EnglishMode.en2cn) {
      return const [PracticeStepKind.zhAnswer];
    }
    return item.requireSpell
        ? const [PracticeStepKind.word, PracticeStepKind.spell]
        : const [PracticeStepKind.word];
  }

  /// 当前步骤序列（随 item 变化）。
  List<PracticeStepKind> get _currentSteps {
    final item = currentItem;
    if (item == null) return const [PracticeStepKind.word];
    return _steps(item);
  }

  bool get isLastStep => state.step >= _currentSteps.length;

  /// 题面（服务端已按模式给好，客户端不泄露答案）。
  String? get promptText => currentItem?.prompt;

  /// 模式口语化标签（F18 模式二 / mixed 需让学生明白要做什么）。
  String get modeLabel {
    final item = currentItem;
    if (item == null) return '';
    return switch (_itemMode(item)) {
      EnglishMode.en2cn => '看英文，说中文',
      EnglishMode.cn2en => '看中文，说英文',
      EnglishMode.mixed => '看中文，说英文',
    };
  }

  /// 当前步 WS `task_type`（页面 `sendStart` 用）。
  String get currentTaskType => state.stepKind.taskType;

  int? get currentWordId => currentItem?.wordId;

  /// 题序文案（如「第 3 / 20 题」）。
  String get progressLabel {
    final total = state.session.items.length;
    return '第 ${state.itemIndex + 1} / $total 题';
  }

  // ----------------------------------------------------------- 语音判定结果

  /// 语音通道：WS `verdict` 到达时由页面调用。
  void submitVoiceVerdict(
    AnswerVerdict verdict, {
    required int durationMs,
    required int firstVoiceMs,
  }) {
    if (verdict.empty) {
      // U8：识别为空，不计错，可重录，停留原步。
      state = state.copyWith(
        phase: PracticePhase.asking,
        keyboardActive: false,
        errorMessage: '没听清，请靠近麦克风再说一次',
      );
      return;
    }
    _consumeVerdict(verdict, durationMs: durationMs, firstVoiceMs: firstVoiceMs, usedKeyboard: false);
  }

  /// 键盘通道：落库后由页面调用。
  Future<void> submitKeyboard({
    required String text,
    required int durationMs,
    required int firstVoiceMs,
  }) async {
    final item = currentItem;
    if (item == null) return;
    final mode = _itemMode(item).wire;
    state = state.copyWith(phase: PracticePhase.asking, errorMessage: null);
    try {
      final verdict = await _repo.submitAnswer(
        sessionId: state.session.sessionId,
        seq: item.seq,
        wordId: item.wordId,
        mode: mode,
        step: state.step,
        asrText: text,
        durationMs: durationMs,
        firstVoiceMs: firstVoiceMs,
        usedKeyboard: true,
        retryCount: state.failuresForStep,
        hintLevel: state.hintLevel,
        usedHintCount: state.usedHintCount,
        idempotencyKey: buildIdempotencyKey(),
      );
      _consumeVerdict(verdict, durationMs: durationMs, firstVoiceMs: firstVoiceMs, usedKeyboard: true);
    } on ApiException catch (e) {
      state = state.copyWith(phase: PracticePhase.error, errorMessage: e.displayText);
    }
  }

  /// 统一消费一次判定（voice / keyboard 共用）。
  void _consumeVerdict(
    AnswerVerdict verdict, {
    required int durationMs,
    required int firstVoiceMs,
    required bool usedKeyboard,
  }) {
    final item = currentItem;
    if (item == null) return;
    final mode = _itemMode(item).wire;

    final record = AnswerRecord(
      seq: item.seq,
      step: state.step,
      stepKind: state.stepKind,
      mode: mode,
      wordId: item.wordId,
      isCorrect: verdict.isCorrect,
      matchType: verdict.matchType,
      usedKeyboard: usedKeyboard,
      retryCount: state.failuresForStep,
      hintLevel: state.hintLevel,
      usedHintCount: state.usedHintCount,
      durationMs: durationMs,
      firstVoiceMs: firstVoiceMs,
      asrText: verdict.asrText,
      answerId: verdict.answerId,
      diff: verdict.diff,
      canAppeal: verdict.canAppeal,
      empty: false,
    );

    final records = [...state.records, record];

    if (verdict.isCorrect) {
      // 正确：停留回显，等「继续」。
      state = state.copyWith(
        lastVerdict: verdict,
        lastRecord: record,
        records: records,
        phase: PracticePhase.result,
        keyboardActive: false,
        timedOut: false,
        errorMessage: null,
        appealed: false,
      );
      return;
    }

    // 错误：累计失败次数（用于「第 3 次才弹键盘」）。
    final failures = state.failuresForStep + 1;
    if (failures >= 3) {
      // U6 / U17：第 3 次失败 → 自动切键盘（前 2 次不弹键盘）。
      state = state.copyWith(
        lastVerdict: verdict,
        lastRecord: record,
        records: records,
        failuresForStep: failures,
        phase: PracticePhase.asking,
        keyboardActive: true,
        timedOut: false,
        errorMessage: '还是没听清，换成打字吧',
      );
    } else {
      // 前 2 次：停留回显提示「没听清，再说一次」，可语音重试（不弹键盘引导）。
      state = state.copyWith(
        lastVerdict: verdict,
        lastRecord: record,
        records: records,
        failuresForStep: failures,
        phase: PracticePhase.result,
        keyboardActive: false,
        timedOut: false,
        errorMessage: '没听清，再说一次',
      );
    }
  }

  // ----------------------------------------------------------- 重试 / 键盘切换

  /// 学生点「再试一次」（结果页，错误且未到第 3 次）：回到原步语音重试。
  void retryVoice() {
    state = state.copyWith(
      phase: PracticePhase.asking,
      keyboardActive: false,
      timedOut: false,
      errorMessage: null,
      clearVerdict: true,
    );
  }

  /// 学生点「改用键盘」（任意时刻，U7）：立即进键盘态。
  void useKeyboard() {
    state = state.copyWith(
      phase: PracticePhase.asking,
      keyboardActive: true,
      errorMessage: null,
    );
  }

  /// WS 错误帧：`action=keyboard` → 强制键盘（U13 断网降级）；
  /// 其余（`retry` / `abort`）→ 提示后停留原步。
  void onWsError({required String action, String? uiHint}) {
    if (action == 'keyboard') {
      state = state.copyWith(
        phase: PracticePhase.asking,
        keyboardActive: true,
        errorMessage: uiHint ?? '网络不太顺，换成打字继续吧',
      );
    } else {
      state = state.copyWith(
        phase: PracticePhase.asking,
        keyboardActive: state.keyboardActive,
        errorMessage: uiHint ?? '没听清，再说一次',
      );
    }
  }

  // ----------------------------------------------------------- 提示（F12）

  /// 升一档提示；每次计入 `usedHintCount`，提交答案时随答案上报。
  ///
  /// ⚠️ 英语侧**封顶 `kEnglishMaxHintLevel`（= 1）**，不是 3：二档（首字母）/
  /// 三档（完整答案）需服务端下发答案，英语通道没有这个来源（§八 #71，
  /// 2026-09-25 裁决 B）。封顶后按钮置灰，**不再虚升档位**——计数落库与
  /// 扣分口径（需求 §6.4）只可能用到一档。
  void escalateHint() {
    if (state.hintLevel >= kEnglishMaxHintLevel) return;
    state = state.copyWith(
      hintLevel: state.hintLevel + 1,
      usedHintCount: state.usedHintCount + 1,
    );
  }

  /// 一档提示内容：占位框数量（本地已知）。
  int get placeholderCount => currentItem?.targetLen ?? 0;

  // ----------------------------------------------------------- 计时到点（U11）

  /// 单题限时到点：判错并展示，等「继续」翻页（不弹模态）。
  void timeoutCurrentStep() {
    final item = currentItem;
    if (item == null) return;
    final mode = _itemMode(item).wire;
    final record = AnswerRecord(
      seq: item.seq,
      step: state.step,
      stepKind: state.stepKind,
      mode: mode,
      wordId: item.wordId,
      isCorrect: false,
      matchType: 'timeout',
      usedKeyboard: false,
      retryCount: state.failuresForStep,
      hintLevel: state.hintLevel,
      usedHintCount: state.usedHintCount,
      durationMs: state.session.timeLimitMs,
      firstVoiceMs: state.session.timeLimitMs,
      asrText: '',
      diff: const <AnswerDiffCell>[],
      empty: false,
    );
    state = state.copyWith(
      lastRecord: record,
      records: [...state.records, record],
      phase: PracticePhase.result,
      timedOut: true,
      keyboardActive: false,
      errorMessage: '时间到啦，这题先过',
    );
  }

  // ----------------------------------------------------------- 翻页 / 结算

  /// 结果页点「继续」：推进到下一步 / 下一题 / 结算。
  void next() {
    if (state.phase != PracticePhase.result) return;
    final item = currentItem;
    if (item == null) {
      _finish(reason: 'completed');
      return;
    }
    if (!isLastStep) {
      // 进入下一步（如 word → spell）。
      state = state.copyWith(
        step: state.step + 1,
        stepKind: _currentSteps[state.step], // step 已 +1，索引 = 旧 step
        phase: PracticePhase.asking,
        failuresForStep: 0,
        hintLevel: 0,
        usedHintCount: 0,
        keyboardActive: false,
        timedOut: false,
        errorMessage: null,
        clearVerdict: true,
      );
      return;
    }
    if (state.itemIndex < state.session.items.length - 1) {
      final nextItem = state.session.items[state.itemIndex + 1];
      state = state.copyWith(
        itemIndex: state.itemIndex + 1,
        step: 1,
        stepKind: _steps(nextItem).first,
        phase: PracticePhase.asking,
        failuresForStep: 0,
        hintLevel: 0,
        usedHintCount: 0,
        keyboardActive: false,
        timedOut: false,
        errorMessage: null,
        clearVerdict: true,
      );
    } else {
      _finish(reason: 'completed');
    }
  }

  /// 中途退出：标记 `abandoned`，已答数据不丢（U15）。
  Future<PracticeSettle?> abandon() async {
    if (state.phase == PracticePhase.finished) return state.settle;
    return _finish(reason: 'abandoned');
  }

  Future<PracticeSettle?> _finish({required String reason}) async {
    if (state.session.sessionId == 0) return null;
    try {
      final settle = await _repo.finish(sessionId: state.session.sessionId, reason: reason);
      state = state.copyWith(phase: PracticePhase.finished, settle: settle);
      return settle;
    } on ApiException catch (e) {
      state = state.copyWith(phase: PracticePhase.error, errorMessage: e.displayText);
      return null;
    }
  }

  /// 申诉（U9 / F11）：不改判分，只记一次。
  Future<void> appeal() async {
    final record = state.lastRecord;
    if (record == null || !record.canAppeal || record.appealed) return;
    try {
      await _repo.appeal(answerId: record.answerId!, reason: '我读对了');
      state = state.copyWith(
        appealed: true,
        lastRecord: AnswerRecord(
          seq: record.seq,
          step: record.step,
          stepKind: record.stepKind,
          mode: record.mode,
          wordId: record.wordId,
          isCorrect: record.isCorrect,
          matchType: record.matchType,
          usedKeyboard: record.usedKeyboard,
          retryCount: record.retryCount,
          hintLevel: record.hintLevel,
          usedHintCount: record.usedHintCount,
          durationMs: record.durationMs,
          firstVoiceMs: record.firstVoiceMs,
          asrText: record.asrText,
          answerId: record.answerId,
          diff: record.diff,
          canAppeal: record.canAppeal,
          appealed: true,
        ),
      );
    } on ApiException catch (e) {
      state = state.copyWith(errorMessage: e.displayText);
    }
  }

  /// 最近一次判定是否可申诉。
  bool get canAppeal =>
      state.lastRecord?.canAppeal == true && (state.lastRecord?.appealed ?? false) == false;

  /// 本场已答对步数（voice + keyboard 都算）。
  int get correctCount => state.records.where((r) => r.isCorrect).length;

  /// 生成幂等键（D-APP03-01：仅生成，待传输层接入）。
  String buildIdempotencyKey() {
    _idemCounter += 1;
    final ts = DateTime.now().microsecondsSinceEpoch;
    return 'app03-${state.session.sessionId}-$_idemCounter-$ts';
  }
}

final practiceControllerProvider =
    NotifierProvider.autoDispose<PracticeController, PracticeState>(PracticeController.new);
