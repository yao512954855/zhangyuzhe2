/// 练习策略 —— 契约 §9.1 的 `strategy` 对象（**字段名与取值照抄契约，不自创**）。
///
/// 五个维度对应 UI 规范 P5 的五组卡片：范围 / 数量 / 顺序 / 模式 / 限时。
///
/// ⚠️ 两处**规格与实现不符**的地方，本文件按"真实能生效"的选项来建模，
/// 而不是照规格字面堆选项（否则学生会选了一个**看起来有用、实际被静默忽略**的选项）：
///
/// 1. 范围选项里的「指定课 / 指定段落」：
///    `backend/app/services/session_service.py` 对英语的 `scope=lesson` /
///    `paragraph` **直接退化为整单元**（词条没有"课/段"层级）；
///    语文则由 `scope_detail.text_ids` 按课文筛选。所以这里：
///    **英语不给课/段选项，语文给"选课文"**（见 [PracticeScope.lesson] 的注释）。
///    详见 `docs/dev/02-实现约定与复用经验.md` §八 偏离 #42。
///
/// 2. 限时：契约只有 `time_limit_ms`，且后端 `_is_timed_out()` 是**按单题**判的，
///    没有"整组限时"字段。整组限时只能由客户端本地计时（到点自动结算，
///    `POST /api/sessions/{id}/finish` 的 `reason` 支持 `timeout`）。
///    详见 偏离 #43。
library;

import 'package:flutter/foundation.dart';

import '../home/models.dart';

/// 练哪部分（契约 §9.1 `scope`）。
enum PracticeScope {
  /// 整个单元（默认）。
  unit('unit', '整个单元'),

  /// 选课文 —— **仅语文**。wire 用 `lesson`：它字面意思就是"选一课"，
  /// 而实际筛选靠 `scope_detail.text_ids`（后端 `_create_chinese_session` 的实现）。
  lesson('lesson', '选课文'),

  /// 自定义区间（按词条序号，从 1 起闭区间）。
  range('range', '自定义区间'),

  /// 只练错题（英语）。
  wrong('wrong', '只练错题'),

  /// 只练还没掌握的（英语）。
  unmastered('unmastered', '只练没掌握的');

  const PracticeScope(this.wire, this.label);

  final String wire;
  final String label;
}

/// 练多少个（契约 §9.1 `count_mode`）。
enum CountMode {
  all('all', '全部'),
  firstN('first_n', '前几个'),
  randomN('random_n', '随机几个');

  const CountMode(this.wire, this.label);

  final String wire;
  final String label;
}

/// 出题顺序（契约 §9.1 `order`）。**默认顺序**（需求 §5.3）。
enum OrderMode {
  sequential('sequential', '顺序'),
  random('random', '随机'),
  wrongFirst('wrong_first', '错题优先'),
  reverse('reverse', '倒序');

  const OrderMode(this.wire, this.label);

  final String wire;
  final String label;
}

/// 怎么问（契约 §9.1 `mode`，**英语专有**）。
enum EnglishMode {
  cn2en('cn2en', '看中文说英文'),
  en2cn('en2cn', '看英文说中文'),
  mixed('mixed', '两种混合');

  const EnglishMode(this.wire, this.label);

  final String wire;
  final String label;
}

/// 限时方式（UI 规范 P5 的三选一）。
///
/// 契约只有一个 `time_limit_ms`，所以这里把"整组限时"区分出来，
/// 由客户端本地计时实现（见文件头说明 2）。
enum TimeLimitKind {
  none('不限时'),
  perQuestion('每题限时'),
  wholeGroup('整组限时');

  const TimeLimitKind(this.label);

  final String label;
}

/// 一档时限（方式 + 秒数）。
@immutable
class TimeLimit {
  const TimeLimit(this.kind, this.seconds);

  const TimeLimit.none() : this(TimeLimitKind.none, 0);

  final TimeLimitKind kind;

  /// 秒数；[TimeLimitKind.none] 时为 0。
  final int seconds;

  bool get isNone => kind == TimeLimitKind.none;

  /// 发给服务端的 `time_limit_ms`。
  ///
  /// **只有"每题限时"才发** —— 整组限时发下去会被后端当成单题限时，
  /// 学生每道题都被掐 5 分钟，那是灾难。
  int get timeLimitMs =>
      kind == TimeLimitKind.perQuestion ? seconds * 1000 : 0;

  /// 界面上显示为「30 秒」/「10 分钟」。
  String get label {
    switch (kind) {
      case TimeLimitKind.none:
        return '不限时';
      case TimeLimitKind.perQuestion:
        return '$seconds 秒/题';
      case TimeLimitKind.wholeGroup:
        return '${seconds ~/ 60} 分钟';
    }
  }

  @override
  bool operator ==(Object other) =>
      other is TimeLimit && other.kind == kind && other.seconds == seconds;

  @override
  int get hashCode => Object.hash(kind, seconds);
}

/// 英语的时限档位：单题 15 / 30 / 60 秒，整组 5 / 10 / 15 分钟（F11）。
const kEnglishQuestionLimits = <TimeLimit>[
  TimeLimit(TimeLimitKind.perQuestion, 15),
  TimeLimit(TimeLimitKind.perQuestion, 30),
  TimeLimit(TimeLimitKind.perQuestion, 60),
  TimeLimit(TimeLimitKind.wholeGroup, 5 * 60),
  TimeLimit(TimeLimitKind.wholeGroup, 10 * 60),
  TimeLimit(TimeLimitKind.wholeGroup, 15 * 60),
];

/// 语文的时限档位：整篇 3 / 5 / 10 分钟（F11）。
///
/// 语文**没有单题限时** —— 背诵是"一段连续语音"，
/// 中途掐表只会把整段判成空结果，没有任何教学意义。
const kChineseLimits = <TimeLimit>[
  TimeLimit(TimeLimitKind.wholeGroup, 3 * 60),
  TimeLimit(TimeLimitKind.wholeGroup, 5 * 60),
  TimeLimit(TimeLimitKind.wholeGroup, 10 * 60),
];

/// 一次练习的完整配置。
@immutable
class PracticeConfig {
  const PracticeConfig({
    required this.unit,
    this.scope = PracticeScope.unit,
    this.selectedTextIds = const <int>{},
    this.rangeStart = 1,
    this.rangeEnd = 0,
    this.countMode = CountMode.all,
    this.count = 10,
    this.order = OrderMode.sequential,
    this.mode = EnglishMode.cn2en,
    this.timeLimit = const TimeLimit.none(),
  });

  final UnitItem unit;
  final PracticeScope scope;

  /// 语文「选课文」选中的课文 id（[PracticeScope.lesson] 时有效）。
  final Set<int> selectedTextIds;

  /// 自定义区间（按序号，1 起闭区间）。
  final int rangeStart;
  final int rangeEnd;

  final CountMode countMode;

  /// 「前几个 / 随机几个」里的 N。
  final int count;

  final OrderMode order;
  final EnglishMode mode;
  final TimeLimit timeLimit;

  bool get isEnglish => unit.subject == Subject.english;

  /// 该学科真实可用的范围选项。
  ///
  /// **英语不给「选课文 / 自定义区间之外」的伪选项**：
  /// `lesson` / `paragraph` 在英语侧会被后端静默退化成整单元，
  /// 给了就是骗学生（见文件头说明 1）。
  List<PracticeScope> get availableScopes => isEnglish
      ? const [
          PracticeScope.unit,
          PracticeScope.range,
          PracticeScope.wrong,
          PracticeScope.unmastered,
        ]
      : const [
          PracticeScope.unit,
          PracticeScope.lesson,
        ];

  /// 该学科真实可用的时限档位。
  List<TimeLimit> get availableLimits {
    final base = isEnglish ? kEnglishQuestionLimits : kChineseLimits;
    return [const TimeLimit.none(), ...base];
  }

  /// 该学科是否支持「练多少个」与「出题顺序」。
  ///
  /// **语文不支持**：后端 `_create_chinese_session()` 只用 `scope` / `scope_detail`
  /// 筛课文，`count` / `count_mode` / `order` 被原样存进快照但**没有应用**
  /// （英语侧是走 `_apply_count` / `_apply_order` 的）。
  /// 所以语文配置页不渲染这两组选项 —— 给学生一个选了不生效的开关，
  /// 比不给更糟：他会以为"随机"坏了。
  /// 详见 `docs/dev/02-实现约定与复用经验.md` §八 偏离 #45。
  bool get supportsCountAndOrder => isEnglish;

  /// 候选题目数（还没应用"练多少个"）。
  ///
  /// 用到 `withError` 的地方**在字段缺失时保守取整单元数**，
  /// 宁可把预估说大一点，也不要说"约 1 分钟"结果练了十分钟。
  int get candidateCount {
    switch (scope) {
      case PracticeScope.unit:
        return unit.total;
      case PracticeScope.lesson:
        // 选课文时按选中的篇数；没选就当作整单元（UI 会拦住"没选就开练"）
        return selectedTextIds.isEmpty ? unit.total : selectedTextIds.length;
      case PracticeScope.range:
        final end = rangeEnd <= 0 ? unit.total : rangeEnd;
        final start = rangeStart < 1 ? 1 : rangeStart;
        return end < start ? 0 : (end - start + 1);
      case PracticeScope.wrong:
        return unit.withError ?? unit.total;
      case PracticeScope.unmastered:
        final unmastered = unit.total - unit.mastered;
        return unmastered < 0 ? 0 : unmastered;
    }
  }

  /// 本次实际题量（应用 `count_mode` 之后）。
  int get itemCount => switch (countMode) {
        CountMode.all => candidateCount,
        CountMode.firstN || CountMode.randomN =>
          count > candidateCount ? candidateCount : count,
      };

  /// 是否配置完整、可以发起练习。
  ///
  /// 只拦**真的练不了**的情况：一个题都没有、语文选课文却一篇没选。
  /// 其余一律放行（学生想怎么练就怎么练，不给他设置无谓的门槛）。
  bool get canStart {
    if (itemCount <= 0) {
      return false;
    }
    if (scope == PracticeScope.lesson && selectedTextIds.isEmpty) {
      return false;
    }
    return true;
  }

  /// 不能开始的原因（按钮下方的说明文案）。
  String? get blockReason {
    if (scope == PracticeScope.lesson && selectedTextIds.isEmpty) {
      return '先选要背的课文';
    }
    if (itemCount <= 0) {
      switch (scope) {
        case PracticeScope.wrong:
          return '这个单元还没有错题，先练几遍吧';
        case PracticeScope.unmastered:
          return '这个单元的词都掌握啦';
        case PracticeScope.range:
          return '这个范围里没有题目';
        default:
          return '这个单元还没有内容';
      }
    }
    return null;
  }

  PracticeConfig copyWith({
    PracticeScope? scope,
    Set<int>? selectedTextIds,
    int? rangeStart,
    int? rangeEnd,
    CountMode? countMode,
    int? count,
    OrderMode? order,
    EnglishMode? mode,
    TimeLimit? timeLimit,
  }) =>
      PracticeConfig(
        unit: unit,
        scope: scope ?? this.scope,
        selectedTextIds: selectedTextIds ?? this.selectedTextIds,
        rangeStart: rangeStart ?? this.rangeStart,
        rangeEnd: rangeEnd ?? this.rangeEnd,
        countMode: countMode ?? this.countMode,
        count: count ?? this.count,
        order: order ?? this.order,
        mode: mode ?? this.mode,
        timeLimit: timeLimit ?? this.timeLimit,
      );

  /// 转成契约 §9.1 的请求体。
  ///
  /// **字段名逐个对着契约写**，key 拼错后端会当成默认值静默处理
  /// （`StrategyIn` 是 `extra="ignore"`，多余字段不报错）——
  /// 这是最容易"看起来正常但其实全按默认出题"的坑。
  Map<String, dynamic> toRequest() {
    final detail = <String, dynamic>{};
    if (scope == PracticeScope.range) {
      detail['start'] = rangeStart;
      detail['end'] = rangeEnd <= 0 ? unit.total : rangeEnd;
    }
    if (scope == PracticeScope.lesson) {
      detail['text_ids'] = selectedTextIds.toList()..sort();
    }

    return {
      'unit_id': unit.unitId,
      'content_type': unit.subject.wire,
      'strategy': {
        'scope': scope.wire,
        'scope_detail': detail.isEmpty ? null : detail,
        'count': countMode == CountMode.all ? 0 : count,
        'count_mode': countMode.wire,
        'order': order.wire,
        // ⚠️ **语文不发 `mode`**。
        //
        // 契约 §9.1 里 `mode` 本标注为「英语专有」；后端的 `PracticeMode`
        // 只接受 `cn2en / en2cn / mixed`，而语文分支
        // （`SessionService._create_chinese_session`）**根本不读这个字段**，
        // 自己写死 `mode="recite"`。
        //
        // 早先这里为了"求稳"给语文塞了 `'recite'`，结果被 Pydantic 拦成
        // `10001 参数不正确：strategy.mode` —— **语文练习从 App 一条都发不出去**。
        // 是 WP-APP-02 的真机烟测（`smoke_app02.py`）抓到的：
        // 假适配器永远不会校验请求体，这类"字段值不在枚举里"的错误单测看不见。
        if (isEnglish) 'mode': mode.wire,
        // 只有"每题限时"才发；整组限时归客户端本地计时（见 TimeLimit 注释）
        'time_limit_ms': timeLimit.timeLimitMs,
      },
    };
  }

  @override
  bool operator ==(Object other) =>
      other is PracticeConfig &&
      other.unit == unit &&
      other.scope == scope &&
      setEquals(other.selectedTextIds, selectedTextIds) &&
      other.rangeStart == rangeStart &&
      other.rangeEnd == rangeEnd &&
      other.countMode == countMode &&
      other.count == count &&
      other.order == order &&
      other.mode == mode &&
      other.timeLimit == timeLimit;

  @override
  int get hashCode => Object.hash(
        unit,
        scope,
        Object.hashAllUnordered(selectedTextIds),
        rangeStart,
        rangeEnd,
        countMode,
        count,
        order,
        mode,
        timeLimit,
      );
}
