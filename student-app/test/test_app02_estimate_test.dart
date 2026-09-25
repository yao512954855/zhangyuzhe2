/// APP-02 预估与策略测试 —— F9 / F11 / F12 + F13 的请求体。
///
/// 这里钉的是**最容易被"看起来对"蒙过去**的两块：
/// 1. 预估公式（U8：题量与用时随选择实时变化）；
/// 2. `POST /api/sessions` 的请求体字段名与取值。
///    后端 `StrategyIn` 是 `extra="ignore"` —— key 拼错**不报错**，
///    只会静默按默认值出题，学生在界面上看到的选择全是假的。
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/features/config/practice_estimate.dart';
import 'package:student_app/features/config/practice_strategy.dart';
import 'package:student_app/features/home/models.dart';

UnitItem englishUnit({
  int total = 24,
  int practiced = 20,
  int mastered = 14,
  int? withError,
}) =>
    UnitItem(
      unitId: 11,
      subject: Subject.english,
      name: 'Unit 1 Hello!',
      wordTotal: total,
      practiced: practiced,
      mastered: mastered,
      withError: withError,
      accuracy: 0.88,
      status: MasteryStatus.practicedWithError,
    );

UnitItem chineseUnit({int total = 3, int recited = 1}) => UnitItem(
      unitId: 12,
      subject: Subject.chinese,
      name: '第一单元',
      textTotal: total,
      recited: recited,
      lastGrade: '良好',
    );

PracticeConfig english({
  PracticeScope scope = PracticeScope.unit,
  CountMode countMode = CountMode.all,
  int count = 10,
  OrderMode order = OrderMode.sequential,
  EnglishMode mode = EnglishMode.cn2en,
  TimeLimit limit = const TimeLimit.none(),
  int rangeStart = 1,
  int rangeEnd = 0,
  UnitItem? unit,
}) =>
    PracticeConfig(
      unit: unit ?? englishUnit(),
      scope: scope,
      countMode: countMode,
      count: count,
      order: order,
      mode: mode,
      timeLimit: limit,
      rangeStart: rangeStart,
      rangeEnd: rangeEnd,
    );

void main() {
  group('候选题量（F8 范围）', () {
    test('整个单元 = 全部词条', () {
      expect(english().candidateCount, 24);
    });

    test('只练未掌握 = 总数 − 已掌握', () {
      expect(english(scope: PracticeScope.unmastered).candidateCount, 24 - 14);
    });

    test('只练错题：后端给了 with_error 就按它算', () {
      expect(
        english(scope: PracticeScope.wrong, unit: englishUnit(withError: 3)).candidateCount,
        3,
      );
    });

    test('只练错题：契约没给 with_error 时**保守按整单元算**（宁可估多不估少）', () {
      expect(english(scope: PracticeScope.wrong, unit: englishUnit()).candidateCount, 24);
    });

    test('自定义区间按序号闭区间', () {
      expect(
        english(scope: PracticeScope.range, rangeStart: 5, rangeEnd: 20).candidateCount,
        16,
      );
    });

    test('区间起点晚于终点 → 0（不会算出负数）', () {
      expect(
        english(scope: PracticeScope.range, rangeStart: 20, rangeEnd: 5).candidateCount,
        0,
      );
    });

    test('语文选课文按选中篇数', () {
      final config = PracticeConfig(
        unit: chineseUnit(),
        scope: PracticeScope.lesson,
        selectedTextIds: {301, 302},
      );
      expect(config.candidateCount, 2);
    });
  });

  group('本次题量（F9 数量）', () {
    test('全部 = 候选数', () {
      expect(english().itemCount, 24);
    });

    test('前 N 个 / 随机 N 个都取 N', () {
      expect(english(countMode: CountMode.firstN, count: 10).itemCount, 10);
      expect(english(countMode: CountMode.randomN, count: 10).itemCount, 10);
    });

    test('N 超过候选数时按候选数（不会凭空多出题）', () {
      expect(english(countMode: CountMode.firstN, count: 100).itemCount, 24);
    });
  });

  group('预估用时（F12 / U8）', () {
    test('英语三种模式的单题基准不同', () {
      expect(estimatePractice(english(mode: EnglishMode.cn2en)).seconds, 24 * 18);
      expect(estimatePractice(english(mode: EnglishMode.en2cn)).seconds, 24 * 10);
      expect(estimatePractice(english(mode: EnglishMode.mixed)).seconds, 24 * 14);
    });

    test('题量变了，用时跟着变（U8：实时更新）', () {
      final ten = estimatePractice(english(countMode: CountMode.firstN, count: 10));
      final twenty = estimatePractice(english(countMode: CountMode.randomN, count: 20));
      expect(ten.itemCount, 10);
      expect(twenty.itemCount, 20);
      expect(twenty.seconds, greaterThan(ten.seconds));
    });

    test('单题限时比基准短 → 按限时算', () {
      final config = english(limit: const TimeLimit(TimeLimitKind.perQuestion, 15));
      expect(estimatePractice(config).seconds, 24 * 15);
    });

    test('单题限时比基准长 → **仍按基准算**（限时只是上限，不是目标）', () {
      final config = english(limit: const TimeLimit(TimeLimitKind.perQuestion, 60));
      expect(estimatePractice(config).seconds, 24 * 18);
    });

    test('整组限时 → 用时就是限时本身（与倒计时一致）', () {
      final config = english(limit: const TimeLimit(TimeLimitKind.wholeGroup, 300));
      expect(estimatePractice(config).seconds, 300);
    });

    test('语文按篇算：拿不到课文列表时用默认字数兜底', () {
      final config = PracticeConfig(unit: chineseUnit(), scope: PracticeScope.unit);
      // 每篇 200 字 × 0.6 + 5s 起手 = 125s
      expect(estimatePractice(config).seconds, 3 * 125);
    });

    test('语文拿到课文列表后按真实背诵字数算', () {
      final config = PracticeConfig(unit: chineseUnit(), scope: PracticeScope.unit);
      final texts = [
        TextBrief(id: 301, unitId: 12, title: '背影', reciteScopeChars: 620),
        TextBrief(id: 302, unitId: 12, title: '春', reciteScopeChars: 300),
        TextBrief(id: 303, unitId: 12, title: '济南的冬天', reciteScopeChars: 400),
      ];
      // 平均 440 字 × 0.6 + 5 = 269s/篇
      expect(estimatePractice(config, texts: texts).seconds, 3 * 269);
    });

    test('零题时用时为 0，文案不说"约 0 分钟"这种怪话', () {
      final config = english(scope: PracticeScope.range, rangeStart: 9, rangeEnd: 3);
      final estimate = estimatePractice(config);
      expect(estimate.seconds, 0);
      expect(estimate.summary, '这个范围里没有题目');
    });

    test('预估条文案格式', () {
      final estimate = estimatePractice(english(countMode: CountMode.firstN, count: 20));
      expect(estimate.summary, contains('本次 20 题'));
      expect(estimate.summary, contains('分钟'));
    });
  });

  group('能否开练（F7 的兜底）', () {
    test('正常配置可以开练', () {
      expect(english().canStart, isTrue);
      expect(english().blockReason, isNull);
    });

    test('一个题都没有时不能开练，并说明原因', () {
      final config = english(scope: PracticeScope.range, rangeStart: 9, rangeEnd: 3);
      expect(config.canStart, isFalse);
      expect(config.blockReason, '这个范围里没有题目');
    });

    test('只练错题但没错题 → 提示先练几遍', () {
      final config = english(scope: PracticeScope.wrong, unit: englishUnit(withError: 0));
      expect(config.canStart, isFalse);
      expect(config.blockReason, '这个单元还没有错题，先练几遍吧');
    });

    test('只练未掌握但都掌握了 → 提示已经掌握', () {
      final config = english(scope: PracticeScope.unmastered, unit: englishUnit(mastered: 24));
      expect(config.canStart, isFalse);
      expect(config.blockReason, '这个单元的词都掌握啦');
    });

    test('语文选课文但一篇没选 → 先选课文', () {
      final config = PracticeConfig(
        unit: chineseUnit(),
        scope: PracticeScope.lesson,
        selectedTextIds: const {},
      );
      expect(config.canStart, isFalse);
      expect(config.blockReason, '先选要背的课文');
    });
  });

  group('选项按学科给（**不给不生效的假选项**）', () {
    test('英语不给"选课文"（lesson 会被后端退化成整单元）', () {
      final scopes = english().availableScopes;
      expect(scopes, contains(PracticeScope.unit));
      expect(scopes, contains(PracticeScope.range));
      expect(scopes, contains(PracticeScope.wrong));
      expect(scopes, contains(PracticeScope.unmastered));
      expect(scopes, isNot(contains(PracticeScope.lesson)));
    });

    test('语文只给"整个单元 / 选课文"', () {
      final config = PracticeConfig(unit: chineseUnit());
      expect(config.availableScopes, [PracticeScope.unit, PracticeScope.lesson]);
    });

    test('语文不支持"数量 / 顺序"（后端未应用这两个字段）', () {
      expect(PracticeConfig(unit: chineseUnit()).supportsCountAndOrder, isFalse);
      expect(english().supportsCountAndOrder, isTrue);
    });

    test('语文的时限只有整篇，没有单题限时', () {
      final limits = PracticeConfig(unit: chineseUnit()).availableLimits;
      expect(limits.first.isNone, isTrue);
      expect(
        limits.skip(1).every((l) => l.kind == TimeLimitKind.wholeGroup),
        isTrue,
      );
    });
  });

  group('请求体（F13 —— 字段名必须与契约 §9.1 逐字一致）', () {
    test('英语基本请求', () {
      final request = english(
        scope: PracticeScope.wrong,
        countMode: CountMode.randomN,
        count: 10,
        order: OrderMode.wrongFirst,
      ).toRequest();

      expect(request['unit_id'], 11);
      expect(request['content_type'], 'english');

      final strategy = request['strategy'] as Map<String, dynamic>;
      expect(strategy['scope'], 'wrong');
      expect(strategy['count'], 10);
      expect(strategy['count_mode'], 'random_n');
      expect(strategy['order'], 'wrong_first');
      expect(strategy['mode'], 'cn2en');
      expect(strategy['time_limit_ms'], 0);
      expect(strategy['scope_detail'], isNull);
    });

    test('"全部"时 count 发 0（契约：0 表示全部）', () {
      final strategy = english().toRequest()['strategy'] as Map<String, dynamic>;
      expect(strategy['count'], 0);
      expect(strategy['count_mode'], 'all');
    });

    test('单题限时发毫秒', () {
      final strategy = english(limit: const TimeLimit(TimeLimitKind.perQuestion, 30))
          .toRequest()['strategy'] as Map<String, dynamic>;
      expect(strategy['time_limit_ms'], 30000);
    });

    test('整组限时**不发** time_limit_ms（发了会被当成每题限时）', () {
      final strategy = english(limit: const TimeLimit(TimeLimitKind.wholeGroup, 300))
          .toRequest()['strategy'] as Map<String, dynamic>;
      expect(strategy['time_limit_ms'], 0);
    });

    test('自定义区间带 scope_detail.start/end', () {
      final strategy = english(scope: PracticeScope.range, rangeStart: 5, rangeEnd: 20)
          .toRequest()['strategy'] as Map<String, dynamic>;
      expect(strategy['scope'], 'range');
      expect(strategy['scope_detail'], {'start': 5, 'end': 20});
    });

    test('语文请求：content_type=chinese，且**不发 mode**', () {
      final request = PracticeConfig(unit: chineseUnit(), scope: PracticeScope.unit).toRequest();
      expect(request['content_type'], 'chinese');
      final strategy = request['strategy'] as Map<String, dynamic>;
      // 后端 `PracticeMode` 只接受 cn2en/en2cn/mixed，语文分支不读 mode。
      // 早先这里发过 'recite' → 每次组卷都被拦成 10001（真机烟测抓到的）。
      expect(strategy.containsKey('mode'), isFalse);
      expect(strategy['scope'], 'unit');
      expect(strategy['scope_detail'], isNull);
    });

    test('语文选课文带 text_ids（且排序稳定）', () {
      final strategy = PracticeConfig(
        unit: chineseUnit(),
        scope: PracticeScope.lesson,
        selectedTextIds: {303, 301, 302},
      ).toRequest()['strategy'] as Map<String, dynamic>;
      expect(strategy['scope'], 'lesson');
      expect(strategy['scope_detail'], {
        'text_ids': [301, 302, 303],
      });
    });

    test('区间上限为 0 时按单元总量补齐（不会发出 end: 0）', () {
      final strategy = english(scope: PracticeScope.range, rangeStart: 1, rangeEnd: 0)
          .toRequest()['strategy'] as Map<String, dynamic>;
      expect(strategy['scope_detail'], {'start': 1, 'end': 24});
    });
  });

  group('TimeLimit', () {
    test('文案', () {
      expect(const TimeLimit.none().label, '不限时');
      expect(const TimeLimit(TimeLimitKind.perQuestion, 30).label, '30 秒/题');
      expect(const TimeLimit(TimeLimitKind.wholeGroup, 600).label, '10 分钟');
    });

    test('只有单题限时才转毫秒', () {
      expect(const TimeLimit(TimeLimitKind.perQuestion, 30).timeLimitMs, 30000);
      expect(const TimeLimit(TimeLimitKind.wholeGroup, 300).timeLimitMs, 0);
      expect(const TimeLimit.none().timeLimitMs, 0);
    });
  });
}
