/// APP-02 模型层测试 —— 字段名与容错。
///
/// 重点不在"能不能解析"，而在**后端给脏数据/缺字段时会不会崩**：
/// 这些模型跑在首页首帧上，一崩就是白屏（U10 明确要求不崩）。
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/features/home/models.dart';
import 'package:student_app/features/home/time_format.dart';

void main() {
  group('UnitItem —— 英语', () {
    test('契约 §8.3 的标准英语条目', () {
      final unit = UnitItem.fromJson({
        'unit_id': 11,
        'subject': 'english',
        'name': 'Unit 1 Hello!',
        'word_total': 24,
        'practiced': 20,
        'mastered': 14,
        'accuracy': 0.88,
        'last_practice_at': '2026-09-23T09:01:00Z',
        'status': 'practiced_with_error',
      });

      expect(unit.unitId, 11);
      expect(unit.subject, Subject.english);
      expect(unit.name, 'Unit 1 Hello!');
      expect(unit.wordTotal, 24);
      expect(unit.practiced, 20);
      expect(unit.mastered, 14);
      expect(unit.accuracy, closeTo(0.88, 1e-9));
      expect(unit.status, MasteryStatus.practicedWithError);
      expect(unit.progressText, '20/24');
      expect(unit.progressRatio, closeTo(20 / 24, 1e-9));
      expect(unit.countLabel, '24 个词');
    });

    test('契约未下发 with_error 时为 null（**不拿别的字段硬凑**）', () {
      final unit = UnitItem.fromJson({
        'unit_id': 11,
        'subject': 'english',
        'name': 'Unit 1',
        'word_total': 24,
        'practiced': 20,
        'mastered': 14,
      });
      expect(unit.withError, isNull);
    });

    test('后端哪天补上 with_error 就自动生效，客户端不需要改代码', () {
      final unit = UnitItem.fromJson({
        'unit_id': 11,
        'subject': 'english',
        'name': 'Unit 1',
        'word_total': 24,
        'practiced': 20,
        'mastered': 14,
        'with_error': 3,
      });
      expect(unit.withError, 3);
    });

    test('分母为 0 时进度比例是 0.0（与后端"分母 0 → 0.0"口径一致）', () {
      final unit = UnitItem.fromJson({
        'unit_id': 1,
        'subject': 'english',
        'name': '空单元',
        'word_total': 0,
        'practiced': 0,
      });
      expect(unit.progressRatio, 0.0);
      expect(unit.progressText, '0/0');
    });
  });

  group('UnitItem —— 语文', () {
    test('语文条目用 text_total / recited，不是 word_total', () {
      final unit = UnitItem.fromJson({
        'unit_id': 12,
        'subject': 'chinese',
        'name': '第一单元',
        'text_total': 3,
        'recited': 1,
        'last_grade': '良好',
        'status': 'practiced_ok',
      });

      expect(unit.subject, Subject.chinese);
      expect(unit.textTotal, 3);
      expect(unit.recited, 1);
      expect(unit.lastGrade, '良好');
      // U11：语文卡要显示"篇"，不是"词"
      expect(unit.progressText, '1/3 篇');
      expect(unit.countLabel, '3 篇课文');
      expect(unit.total, 3);
      expect(unit.done, 1);
    });
  });

  group('UnitsPage', () {
    test('未知学科的单条脏数据被跳过，其余照常解析', () {
      final page = UnitsPage.fromJson({
        'grade': 3,
        'semester': 'first',
        'items': [
          {'unit_id': 1, 'subject': 'math', 'name': '数学一单元'},
          {'unit_id': 11, 'subject': 'english', 'name': 'Unit 1', 'word_total': 24},
          '这不是对象',
        ],
      });

      expect(page.items.length, 1);
      expect(page.items.single.unitId, 11);
      expect(page.grade, 3);
      expect(page.semester, 1);
      expect(page.fromCache, isFalse);
    });

    test('bySubject 过滤出对应学科', () {
      final page = UnitsPage.fromJson({
        'items': [
          {'unit_id': 1, 'subject': 'english', 'name': 'E1'},
          {'unit_id': 2, 'subject': 'chinese', 'name': 'C1'},
          {'unit_id': 3, 'subject': 'english', 'name': 'E2'},
        ],
      });
      expect(page.bySubject(Subject.english).length, 2);
      expect(page.bySubject(Subject.chinese).length, 1);
    });

    test('grade/semester 为 null（还没选年级）时不报错，列表为空', () {
      final page = UnitsPage.fromJson({'grade': null, 'semester': null, 'items': []});
      expect(page.grade, isNull);
      expect(page.semester, isNull);
      expect(page.items, isEmpty);
    });

    test('unitsWithError 只统计真的带 with_error 且大于 0 的单元', () {
      final page = UnitsPage.fromJson({
        'items': [
          {'unit_id': 1, 'subject': 'english', 'name': 'E1', 'with_error': 3},
          {'unit_id': 2, 'subject': 'english', 'name': 'E2'},
          {'unit_id': 3, 'subject': 'english', 'name': 'E3', 'with_error': 0},
        ],
      });
      expect(page.unitsWithError, 1);
    });
  });

  group('MasteryStatus', () {
    test('四态都能解析', () {
      expect(MasteryStatus.parse('not_started'), MasteryStatus.notStarted);
      expect(MasteryStatus.parse('practiced_with_error'), MasteryStatus.practicedWithError);
      expect(MasteryStatus.parse('practiced_ok'), MasteryStatus.practicedOk);
      expect(MasteryStatus.parse('mastered'), MasteryStatus.mastered);
    });

    test('未知值降级为 notStarted（不崩）', () {
      expect(MasteryStatus.parse('brand_new'), MasteryStatus.notStarted);
      expect(MasteryStatus.parse(null), MasteryStatus.notStarted);
    });

    test('四种状态的 wire 值与文案都不同', () {
      final wires = MasteryStatus.values.map((s) => s.wire).toSet();
      final labels = MasteryStatus.values.map((s) => s.label).toSet();
      expect(wires.length, 4);
      expect(labels.length, 4);
    });
  });

  group('WordItem', () {
    test('cn2en 模式下 en 显式为 null（键仍在，不是缺字段）', () {
      final word = WordItem.fromJson({
        'id': 88,
        'unit_id': 11,
        'type': 'word',
        'en': null,
        'cn': '教室',
        'require_spell': 1,
        'order_no': 1,
        'mastery': {'status': 'practiced_with_error', 'streak': 0, 'wrong_count': 2},
      });

      expect(word.en, isNull);
      expect(word.cn, '教室');
      expect(word.requireSpell, isTrue);
      expect(word.masteryStatus, MasteryStatus.practicedWithError);
      expect(word.wrongCount, 2);
      // 出题侧缺失时显示中文，不显示"null"
      expect(word.displayTitle, '教室');
    });

    test('require_spell 的 0/1 与布尔都能接住', () {
      final zero = WordItem.fromJson({'id': 1, 'unit_id': 1, 'require_spell': 0});
      final boolFalse = WordItem.fromJson({'id': 2, 'unit_id': 1, 'require_spell': false});
      expect(zero.requireSpell, isFalse);
      expect(boolFalse.requireSpell, isFalse);
    });

    test('缺少 mastery 时按 not_started 处理', () {
      final word = WordItem.fromJson({'id': 1, 'unit_id': 1, 'cn': '书'});
      expect(word.masteryStatus, MasteryStatus.notStarted);
      expect(word.wrongCount, 0);
    });
  });

  group('TextBrief', () {
    test('契约 §8.6 的课文条目', () {
      final text = TextBrief.fromJson({
        'id': 301,
        'unit_id': 12,
        'title': '背影',
        'author': '朱自清',
        'total_chars': 1180,
        'recite_scope_chars': 620,
        'paragraph_count': 7,
        'recite_paragraph_count': 4,
        'last_grade': '良好',
      });

      expect(text.title, '背影');
      expect(text.author, '朱自清');
      expect(text.reciteScopeChars, 620);
      expect(text.hasRecited, isTrue);
    });

    test('没背过时 last_grade 为空 → hasRecited 为 false', () {
      final text = TextBrief.fromJson({'id': 1, 'unit_id': 1, 'title': '春'});
      expect(text.hasRecited, isFalse);
    });
  });

  group('ProgressOverview', () {
    test('契约 §8.4 的进度总览', () {
      final progress = ProgressOverview.fromJson({
        'total_words': 240,
        'practiced_words': 180,
        'mastered_words': 96,
        'total_texts': 18,
        'recited_texts': 7,
        'streak_days': 5,
        'accuracy': 0.86,
        'today': {'sessions': 2, 'items': 40, 'correct': 35},
      });

      expect(progress.practicedWords, 180);
      expect(progress.masteredWords, 96);
      expect(progress.streakDays, 5);
      expect(progress.today.sessions, 2);
      expect(progress.today.correct, 35);
    });

    test('缺 today 时用零值兜底', () {
      final progress = ProgressOverview.fromJson(const {});
      expect(progress.today.items, 0);
      expect(progress.streakDays, 0);
    });
  });

  group('时间展示', () {
    // 用"本地时间 → UTC"构造输入，这样断言与运行机器的时区无关
    String isoOfLocal(int y, int m, int d, int h, int min) =>
        DateTime(y, m, d, h, min).toUtc().toIso8601String();

    test('今天 / 昨天 / 同年更早 / 跨年', () {
      final now = DateTime(2026, 9, 24, 10, 0);
      expect(formatLastPractice(isoOfLocal(2026, 9, 24, 9, 1), now: now), '今天 09:01');
      expect(formatLastPractice(isoOfLocal(2026, 9, 23, 9, 1), now: now), '昨天 09:01');
      expect(formatLastPractice(isoOfLocal(2026, 9, 20, 9, 1), now: now), '9月20日 09:01');
      expect(
        formatLastPractice(isoOfLocal(2025, 9, 20, 9, 1), now: now),
        '2025年9月20日 09:01',
      );
    });

    test('解析不了返回 null（调用方据此不显示这一行）', () {
      expect(formatLastPractice(null), isNull);
      expect(formatLastPractice(''), isNull);
      expect(formatLastPractice('不是时间'), isNull);
    });

    test('缓存时间用相对说法', () {
      final now = DateTime(2026, 9, 24, 10, 0);
      // 边界钉清楚：< 60 秒 = 「刚刚」，满 60 秒才报「1 分钟前」。
      // （9:59 整正好卡在边界上，不适合当用例，见下）
      expect(
        formatCachedAt(
          DateTime(2026, 9, 24, 9, 59, 30).toUtc().toIso8601String(),
          now: now,
        ),
        '刚刚',
      );
      expect(formatCachedAt(isoOfLocal(2026, 9, 24, 9, 59), now: now), '1 分钟前');
      expect(formatCachedAt(isoOfLocal(2026, 9, 24, 9, 55), now: now), '5 分钟前');
      expect(formatCachedAt(isoOfLocal(2026, 9, 24, 7, 0), now: now), '3 小时前');
    });

    test('学期文案与月份预选', () {
      expect(semesterLabel(1), '上学期');
      expect(semesterLabel(2), '下学期');
      expect(semesterLabel(null), '未选学期');

      // 8 月 – 次年 1 月 = 上学期（9 月开学）
      expect(suggestSemester(DateTime(2026, 9, 1)), 1);
      expect(suggestSemester(DateTime(2026, 12, 1)), 1);
      expect(suggestSemester(DateTime(2026, 1, 15)), 1);
      // 2 – 7 月 = 下学期
      expect(suggestSemester(DateTime(2026, 2, 1)), 2);
      expect(suggestSemester(DateTime(2026, 7, 1)), 2);
    });

    test('年级学期标签', () {
      expect(gradeSemesterLabel(3, 1), '3 年级 · 上学期');
      expect(gradeSemesterLabel(null, null), '还没选择年级');
    });
  });
}
