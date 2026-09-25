/// APP-02 数据模型 —— 严格对齐契约 §8.3 / §8.4 / §8.5 / §8.6 的真实 JSON。
///
/// **字段名一律沿用后端下发的 snake_case 原名**，不做驼峰转换：
/// 契约 §2 与 `backend/app/schemas/*.py` 都明确「JSON 字段名与数据库字段名一致」，
/// 客户端再自己转一道，只会多出一处能写错的地方。
///
/// ⚠️ 本文件字段名是**照着后端实现核过**的（`api/me_learning.py`、
/// `services/mastery_service.py`、`schemas/content.py`），不是照契约示例抄的 ——
/// APP-01 就吃过一次亏（契约 §3.1 的 `/api/ping` 字段与实现对不上）。
library;

import 'package:flutter/foundation.dart';

/// 学科。后端取值固定 `english` / `chinese`。
enum Subject {
  english('english', '英语'),
  chinese('chinese', '语文');

  const Subject(this.wire, this.label);

  /// 与后端交换的值。
  final String wire;

  /// 界面文案。
  final String label;

  /// 未知值返回 `null`。**不抛异常**：后端将来加学科（比如 `math`）
  /// 不该让整个首页白屏。
  static Subject? tryParse(Object? raw) {
    final value = raw?.toString();
    for (final s in Subject.values) {
      if (s.wire == value) {
        return s;
      }
    }
    return null;
  }
}

/// 掌握状态 —— 契约 §7.2，四态，**词条级与单元级共用同一套口径**。
///
/// 判定按优先级顺序（先命中者胜），由服务端 `MasteryService` 唯一裁定，
/// 客户端**只做展示映射，不自己推导**（否则两端口径必然漂移）。
enum MasteryStatus {
  notStarted('not_started', '未练'),
  practicedWithError('practiced_with_error', '练过·有错'),
  practicedOk('practiced_ok', '练过·全对'),
  mastered('mastered', '已掌握');

  const MasteryStatus(this.wire, this.label);

  final String wire;
  final String label;

  /// 未知值降级为 [MasteryStatus.notStarted]（宁可显示"未练"，
  /// 也不能因为多了一个枚举值就崩在首页）。
  static MasteryStatus parse(Object? raw) {
    final value = raw?.toString();
    for (final s in MasteryStatus.values) {
      if (s.wire == value) {
        return s;
      }
    }
    return MasteryStatus.notStarted;
  }
}

/// 首页单元卡（契约 §8.3 的 `items[]`）。
///
/// **英语与语文共用这一个类**，字段集不同（英语有 `word_total`，
/// 语文有 `text_total`），用 [subject] 区分渲染。理由：列表里两者混排，
/// 用两个类会让 `ListView` 的 item 类型变成 `dynamic`，得不偿失。
@immutable
class UnitItem {
  const UnitItem({
    required this.unitId,
    required this.subject,
    required this.name,
    this.wordTotal = 0,
    this.practiced = 0,
    this.mastered = 0,
    this.withError,
    this.accuracy = 0,
    this.lastPracticeAt,
    this.textTotal = 0,
    this.recited = 0,
    this.lastGrade,
    this.status = MasteryStatus.notStarted,
  });

  /// 从契约 §8.3 的条目反序列化。
  ///
  /// `subject` 非法时抛 [ArgumentError] —— 由仓库层逐条兜住并跳过，
  /// 这样"一条脏数据"不会连带毁掉整个列表。
  factory UnitItem.fromJson(Map<String, dynamic> json) {
    final subject = Subject.tryParse(json['subject']);
    if (subject == null) {
      throw ArgumentError('单元 ${json['unit_id']} 的学科无法识别：${json['subject']}');
    }
    return UnitItem(
      unitId: _int(json['unit_id']),
      subject: subject,
      name: json['name']?.toString() ?? '',
      wordTotal: _int(json['word_total']),
      practiced: _int(json['practiced']),
      mastered: _int(json['mastered']),
      // ⚠️ 契约 §8.3 的响应**没有** `with_error`，但 UI 规范 P4 的卡片要显示「✗N」。
      //    后端 `MasteryService.unit_counts()` 其实算了这个值，只是 `my_units`
      //    没透出（见 docs/dev/02 §八 偏离 #44）。这里做成可空：
      //    **字段哪天补下来就自动生效，客户端不需要改代码**；
      //    现在为 null 时 UI 省略「✗N」，绝不用其它字段硬凑一个错的数字。
      withError: json['with_error'] == null ? null : _int(json['with_error']),
      accuracy: _double(json['accuracy']),
      lastPracticeAt: json['last_practice_at']?.toString(),
      textTotal: _int(json['text_total']),
      recited: _int(json['recited']),
      lastGrade: json['last_grade']?.toString(),
      status: MasteryStatus.parse(json['status']),
    );
  }

  final int unitId;
  final Subject subject;
  final String name;

  // ---- 英语 ----
  final int wordTotal;
  final int practiced;
  final int mastered;

  /// 练过且有错的词条数。契约未下发时为 `null`。
  final int? withError;

  /// 正确率（0–1，服务端算好）。
  final double accuracy;

  final String? lastPracticeAt;

  // ---- 语文 ----
  final int textTotal;
  final int recited;

  /// 最近一篇的评级（如「良好」）。
  final String? lastGrade;

  final MasteryStatus status;

  /// 本次进度的分母（英语 = 词条数，语文 = 课文数）。
  int get total => subject == Subject.english ? wordTotal : textTotal;

  /// 本次进度的分子（英语 = 已练词条，语文 = 已背课文）。
  int get done => subject == Subject.english ? practiced : recited;

  /// 进度条比例。分母为 0 时返回 `0.0`（与后端「分母 0 → 0.0」口径一致）。
  double get progressRatio => total <= 0 ? 0.0 : (done / total).clamp(0.0, 1.0);

  /// 数字型进度文案（UI 规范 §7.4：必须是「20/24」这种数字，不是只有进度条）。
  String get progressText => subject == Subject.english
      ? '$practiced/$wordTotal'
      : '$recited/$textTotal 篇';

  /// 是否还没开始练（用于「只练错题」入口的显隐与空状态文案）。
  bool get isNotStarted => status == MasteryStatus.notStarted;

  /// 单元卡上的量词（"24 个词" / "3 篇课文"）。
  String get countLabel => subject == Subject.english ? '$wordTotal 个词' : '$textTotal 篇课文';

  @override
  bool operator ==(Object other) =>
      other is UnitItem && other.unitId == unitId && other.subject == subject && other.name == name;

  @override
  int get hashCode => Object.hash(unitId, subject, name);
}

/// `GET /api/me/units` 的完整响应。
@immutable
class UnitsPage {
  const UnitsPage({
    required this.items,
    this.grade,
    this.semester,
    this.fromCache = false,
    this.cachedAt,
  });

  factory UnitsPage.fromJson(Map<String, dynamic> json) {
    final rawItems = json['items'];
    final items = <UnitItem>[];
    if (rawItems is List) {
      for (final raw in rawItems) {
        if (raw is! Map) {
          continue;
        }
        try {
          items.add(UnitItem.fromJson(Map<String, dynamic>.from(raw)));
        } on ArgumentError {
          // 单条脏数据跳过，不让整个首页挂掉
          continue;
        }
      }
    }
    return UnitsPage(
      items: items,
      grade: json['grade'] == null ? null : _int(json['grade']),
      semester: _semesterCode(json['semester']),
    );
  }

  /// 单元列表（英语在前、语在后，服务端已按 `subject, order_no` 排好）。
  final List<UnitItem> items;

  /// 服务端实际采用的年级（可能来自偏好，也可能是请求参数）。
  final int? grade;

  /// 1 = 上学期，2 = 下学期。
  final int? semester;

  /// 是否来自本地缓存（断网降级，F15）。UI 需要据此提示「当前无法连接服务器」。
  final bool fromCache;

  /// 缓存写入时间（用于提示"这是什么时候的数据"）。
  final DateTime? cachedAt;

  List<UnitItem> bySubject(Subject subject) =>
      items.where((item) => item.subject == subject).toList(growable: false);

  /// 有错题可练的单元数 —— 「只练错题」入口的显隐依据。
  int get unitsWithError =>
      items.where((item) => item.withError != null && item.withError! > 0).length;

  @override
  bool operator ==(Object other) =>
      other is UnitsPage &&
      other.grade == grade &&
      other.semester == semester &&
      other.fromCache == fromCache &&
      listEquals(other.items, items);

  @override
  int get hashCode => Object.hash(grade, semester, fromCache, Object.hashAll(items));
}

/// `GET /api/me/progress` 的今日统计。
@immutable
class TodayStats {
  const TodayStats({this.sessions = 0, this.items = 0, this.correct = 0});

  factory TodayStats.fromJson(Map<String, dynamic>? json) => TodayStats(
        sessions: _int(json?['sessions']),
        items: _int(json?['items']),
        correct: _int(json?['correct']),
      );

  final int sessions;
  final int items;
  final int correct;

  @override
  bool operator ==(Object other) =>
      other is TodayStats &&
      other.sessions == sessions &&
      other.items == items &&
      other.correct == correct;

  @override
  int get hashCode => Object.hash(sessions, items, correct);
}

/// `GET /api/me/progress` —— 首页顶部进度概要（契约 §8.4）。
@immutable
class ProgressOverview {
  const ProgressOverview({
    this.totalWords = 0,
    this.practicedWords = 0,
    this.masteredWords = 0,
    this.totalTexts = 0,
    this.recitedTexts = 0,
    this.streakDays = 0,
    this.accuracy = 0,
    this.today = const TodayStats(),
  });

  factory ProgressOverview.fromJson(Map<String, dynamic> json) => ProgressOverview(
        totalWords: _int(json['total_words']),
        practicedWords: _int(json['practiced_words']),
        masteredWords: _int(json['mastered_words']),
        totalTexts: _int(json['total_texts']),
        recitedTexts: _int(json['recited_texts']),
        streakDays: _int(json['streak_days']),
        accuracy: _double(json['accuracy']),
        today: TodayStats.fromJson(
          json['today'] is Map ? Map<String, dynamic>.from(json['today'] as Map) : null,
        ),
      );

  final int totalWords;
  final int practicedWords;
  final int masteredWords;
  final int totalTexts;
  final int recitedTexts;

  /// 连续练习天数（服务端口径：今天没练但昨天练了仍算连续，见 `streak_days`）。
  final int streakDays;

  final double accuracy;
  final TodayStats today;

  @override
  bool operator ==(Object other) =>
      other is ProgressOverview &&
      other.totalWords == totalWords &&
      other.practicedWords == practicedWords &&
      other.masteredWords == masteredWords &&
      other.totalTexts == totalTexts &&
      other.recitedTexts == recitedTexts &&
      other.streakDays == streakDays &&
      other.accuracy == accuracy &&
      other.today == today;

  @override
  int get hashCode => Object.hash(
        totalWords,
        practicedWords,
        masteredWords,
        totalTexts,
        recitedTexts,
        streakDays,
        accuracy,
        today,
      );
}

/// 词条（契约 §8.5 的 `words[]`，学生视角的裁剪版）。
///
/// ⚠️ **拿不到 `accepted`（可接受答案）** —— 那是判分依据，后端刻意不下发。
/// 客户端**永远不要**尝试自己判分（判定 100% 在服务端）。
@immutable
class WordItem {
  const WordItem({
    required this.id,
    required this.unitId,
    required this.type,
    this.en,
    this.cn,
    this.phonetic,
    this.pos,
    this.exampleEn,
    this.exampleCn,
    this.requireSpell = true,
    this.orderNo = 0,
    this.masteryStatus = MasteryStatus.notStarted,
    this.wrongCount = 0,
  });

  factory WordItem.fromJson(Map<String, dynamic> json) {
    final mastery = json['mastery'] is Map
        ? Map<String, dynamic>.from(json['mastery'] as Map)
        : const <String, dynamic>{};
    return WordItem(
      id: _int(json['id']),
      unitId: _int(json['unit_id']),
      type: json['type']?.toString() ?? 'word',
      // 按 mode 查询时，出题侧字段会**显式为 null**（键仍在，不删键）
      en: json['en']?.toString(),
      cn: json['cn']?.toString(),
      phonetic: json['phonetic']?.toString(),
      pos: json['pos']?.toString(),
      exampleEn: json['example_en']?.toString(),
      exampleCn: json['example_cn']?.toString(),
      requireSpell: _bool(json['require_spell'], fallback: true),
      orderNo: _int(json['order_no']),
      masteryStatus: MasteryStatus.parse(mastery['status']),
      wrongCount: _int(mastery['wrong_count']),
    );
  }

  final int id;
  final int unitId;

  /// `word` / `phrase` / `sentence`。
  final String type;

  final String? en;
  final String? cn;
  final String? phonetic;
  final String? pos;
  final String? exampleEn;
  final String? exampleCn;

  /// 是否需要拼读（`require_spell`）。
  final bool requireSpell;

  final int orderNo;
  final MasteryStatus masteryStatus;
  final int wrongCount;

  /// 词条的展示标题（详情页用；两侧都返回时优先中文）。
  String get displayTitle => cn ?? en ?? '（缺少内容）';

  @override
  bool operator ==(Object other) => other is WordItem && other.id == id;

  @override
  int get hashCode => id.hashCode;
}

/// 课文列表项（契约 §8.6）。**不含正文**。
@immutable
class TextBrief {
  const TextBrief({
    required this.id,
    required this.unitId,
    required this.title,
    this.author,
    this.totalChars = 0,
    this.reciteScopeChars = 0,
    this.paragraphCount = 0,
    this.reciteParagraphCount = 0,
    this.lastGrade,
  });

  factory TextBrief.fromJson(Map<String, dynamic> json) => TextBrief(
        id: _int(json['id']),
        unitId: _int(json['unit_id']),
        title: json['title']?.toString() ?? '',
        author: json['author']?.toString(),
        totalChars: _int(json['total_chars']),
        reciteScopeChars: _int(json['recite_scope_chars']),
        paragraphCount: _int(json['paragraph_count']),
        reciteParagraphCount: _int(json['recite_paragraph_count']),
        lastGrade: json['last_grade']?.toString(),
      );

  final int id;
  final int unitId;
  final String title;
  final String? author;

  /// ⚠️ **名字骗人，别拿去当"全文分母"**。
  ///
  /// 契约 §8.6 的示例是 `total_chars: 1180`（全文）对
  /// `recite_scope_chars: 620`（背诵范围），但**实现不是这样**：
  /// 后端 `texts.total_chars = PinyinService.build_scoped(段落, 背诵范围).length`
  /// —— 它算的是**背诵范围内**的字数，与 [reciteScopeChars] **恒等**。
  /// （接口设计 §8.7 / §9.4 又写着 `total_chars: 620`，等于范围字数，
  /// 所以是**文档自身前后不一致**，实现跟的是 §9.4 那一支。）
  ///
  /// 因此：**要展示、要算背诵进度，一律用 [reciteScopeChars]**。
  /// 这个字段目前没有任何消费方，留着只是为了不丢信息。
  final int totalChars;

  /// 需要背诵的字数（背诵范围内）。**UI 与预估都该用它**。
  final int reciteScopeChars;

  final int paragraphCount;
  final int reciteParagraphCount;

  /// 上次背诵评级（没背过为 `null`）。
  final String? lastGrade;

  bool get hasRecited => lastGrade != null && lastGrade!.isNotEmpty;

  @override
  bool operator ==(Object other) => other is TextBrief && other.id == id;

  @override
  int get hashCode => id.hashCode;
}

// ---------------------------------------------------------------- 内部工具

int _int(Object? value) {
  if (value is int) {
    return value;
  }
  if (value is num) {
    return value.toInt();
  }
  return int.tryParse(value?.toString() ?? '') ?? 0;
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

/// `first` / `second` → `1` / `2`；未知返回 `null`。
int? _semesterCode(Object? raw) => switch (raw?.toString()) {
      'first' => 1,
      'second' => 2,
      _ => null,
    };
