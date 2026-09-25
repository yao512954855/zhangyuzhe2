/// A5 语文错题本 —— `GET /api/me/wrong-chars`（接口设计 **§8.10**，2026-09-25 补齐）。
///
/// 这套用例盯着**四件容易假绿的事**：
///
/// 1. **两个端点不能串**：英语错题走 §8.8 `wrong-words`（「词」），语文走
///    §8.10 `wrong-chars`（「字」）。假适配器若按 path 前缀模糊匹配，
///    或页面把两个 provider 弄反，**渲染结果看着都像一组错题**，
///    只有把「请求打到了哪个 path / 哪个方法」钉住才分得清。
/// 2. **「重背这篇」的真实请求体**：必须是 `content_type=chinese` +
///    `scope=lesson` + `scope_detail.text_ids=[id]`，且**绝不能带 `mode`**
///    （语文发 `mode` 会被后端 Pydantic 拦成 `10001`，练习一条都发不出去）。
///    这里断言的是**生产代码 `PracticeConfig.toRequest()` 吐出来的东西**，
///    不是手写的期望字符串。
/// 3. **文案不许夸大**：按钮写「重背这篇」——背诵是按课文逐字走的，
///    后端没有"只背这几个字"这种模式，页面里**不许出现「只练错题」**。
/// 4. **上下文由服务端给**：`context_before` / `context_after` 直接渲染，
///    客户端不自己从正文里截（两份真相迟早对不上）。
library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/features/home/models.dart';
import 'package:student_app/features/mine/wrong_book_page.dart';
import 'package:student_app/features/mine/wrong_char_book.dart';

import 'test_app05_helpers.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    (local, serverStore) = await openTestStore();
  });

  Future<void> boot(
    WidgetTester tester,
    JsonAdapter adapter, {
    Subject subject = Subject.chinese,
    DateTime? now,
    List<NavigatorObserver>? observers,
  }) async {
    useTallScreen(tester);
    await tester.pumpWidget(
      bootApp(
        WrongBookPage(initialSubject: subject, now: now),
        local: local,
        serverStore: serverStore,
        adapter: adapter,
        observers: observers,
      ),
    );
    await tester.pumpAndSettle();
  }

  // -------------------------------------------------------------------------
  // 纯函数：分组与合并
  // -------------------------------------------------------------------------

  group('按课文分组（纯函数，U1 的语文侧）', () {
    WrongChar c(int textId, String ch, {int wrong = 1}) => WrongChar(
          textId: textId,
          textTitle: '课文$textId',
          unitId: 12,
          unitName: '第一单元',
          char: ch,
          wrongCount: wrong,
        );

    test('同一篇课文归一组；组间按组内最高错次降序，组内保持服务端原序', () {
      // 服务端序：错次降序（安2 → 北1 → 京1 → 不1）
      final groups = groupByText([
        c(1, '安', wrong: 2),
        c(1, '北'),
        c(1, '京'),
        c(2, '不'),
      ]);

      expect(groups.map((g) => g.textId).toList(), [1, 2]);
      expect(groups.first.textTitle, '课文1');
      expect(groups.first.items.map((w) => w.char).toList(), ['安', '北', '京'],
          reason: '组内不能重排 —— 服务端已经按错次排好了');
      expect(groups.last.items.single.char, '不');
    });

    test('合并第二页：按课文并入、课文内按字去重', () {
      final merged = mergeTextGroups(
        groupByText([c(1, '安', wrong: 2)]),
        groupByText([c(1, '安', wrong: 2), c(1, '京'), c(3, '晓', wrong: 5)]),
      );

      expect(merged.map((g) => g.textId).toList(), [3, 1], reason: '错次高的课文排前');
      expect(merged[1].items.map((w) => w.char).toList(), ['安', '京'],
          reason: '「安」不能出现两次');
    });

    test('字段缺失 / 类型不对不炸：char 为空、count 为 null 都能解析', () {
      final item = WrongChar.fromJson(const {'text_id': 9, 'char': null});
      expect(item.char, '');
      expect(item.wrongCount, 0);
      expect(item.contextBefore, '');
    });
  });

  // -------------------------------------------------------------------------
  // 页面：语文页签
  // -------------------------------------------------------------------------

  group('语文页签渲染（§8.10）', () {
    testWidgets('按课文分组渲染：课文名 / 漏字 / 错次 / 服务端给的上下文', (tester) async {
      final adapter = defaultAdapter(
        wrongChars: wrongCharsJson([
          wrongCharItemJson(
            textId: 7,
            char: '不',
            textTitle: '背影',
            unitName: '第七单元',
            wrongCount: 2,
            lastWrongAt: '2026-09-24T01:30:00Z',
            firstPos: 4,
            contextBefore: '我与父亲',
            contextAfter: '相见',
          ),
          wrongCharItemJson(
            textId: 7,
            char: '亲',
            textTitle: '背影',
            unitName: '第七单元',
            wrongCount: 1,
            firstPos: 3,
            contextBefore: '我与父',
            contextAfter: '不相见',
          ),
        ], 2),
      );
      await boot(tester, adapter, now: DateTime(2026, 9, 25, 12));

      // 请求确实打到了 §8.10 这个 path（不是英语的 wrong-words）
      final calls = adapter.received.where((r) => r.uri.path == '/api/me/wrong-chars');
      expect(calls, hasLength(1));
      expect(calls.single.method, 'GET');
      expect(calls.single.uri.queryParameters['page'], '1');

      expect(find.text('《背影》'), findsOneWidget);
      expect(find.text('第七单元'), findsOneWidget);
      expect(find.text('2 个易错字'), findsOneWidget);
      expect(find.text('不'), findsOneWidget);
      expect(find.text('漏 2 次 · 昨天'), findsOneWidget, reason: 'UTC 01:30 → 北京时间当天，相对「2026-09-25」是昨天');
      expect(find.text('漏 1 次'), findsOneWidget, reason: '没给 last_wrong_at 时只显示次数');
      expect(find.text('重背这篇'), findsOneWidget);
      // 红线：背诵没有"只背这几个字"的模式，不许出现「只练错题」
      expect(find.textContaining('只练错题'), findsNothing);
    });

    testWidgets('一次都没漏过 → 空态说清楚，不报错也不编造', (tester) async {
      final adapter = defaultAdapter(wrongChars: wrongCharsJson(const [], 0));
      await boot(tester, adapter);

      expect(find.text('还没有漏背的字'), findsOneWidget);
      expect(find.textContaining('漏掉的字会收在这里'), findsOneWidget);
      expect(find.text('重背这篇'), findsNothing);
    });

    testWidgets('「重背这篇」走生产组卷链路：scope=lesson + text_ids，且**不发 mode**', (tester) async {
      final adapter = defaultAdapter(
        wrongChars: wrongCharsJson([
          wrongCharItemJson(
            textId: 7,
            char: '不',
            unitId: 12,
            textTitle: '背影',
            unitName: '第七单元',
          ),
        ], 1),
      );
      await boot(tester, adapter);

      await tester.tap(find.text('重背这篇'));
      await tester.pumpAndSettle();

      final post = adapter.received.firstWhere(
        (r) => r.uri.path == '/api/sessions' && r.method.toUpperCase() == 'POST',
      );
      final raw = post.data;
      final body = raw is String
          ? jsonDecode(raw) as Map<String, dynamic>
          : (raw as Map).cast<String, dynamic>();

      expect(body['unit_id'], 12, reason: '按课文所属单元组卷');
      expect(body['content_type'], 'chinese');

      final strategy = (body['strategy'] as Map).cast<String, dynamic>();
      expect(strategy['scope'], 'lesson');
      expect(strategy['scope_detail'], {'text_ids': [7]});
      expect(strategy.containsKey('mode'), isFalse,
          reason: '语文发 mode 会被后端拦成 10001 —— 这条红线必须钉住');
    });
  });

  // -------------------------------------------------------------------------
  // 两个端点不能串
  // -------------------------------------------------------------------------

  group('两个端点不能串（防回归）', () {
    testWidgets('默认进英语页签 → 只打 §8.8，绝不打 §8.10', (tester) async {
      final adapter = defaultAdapter(
        wrongWords: wrongWordsJson([
          wrongItemJson(wordId: 88, unitId: 11, unitName: 'Unit 1', en: 'classroom', cn: '教室'),
        ], 1),
      );
      await boot(tester, adapter, subject: Subject.english);

      expect(
        adapter.received.where((r) => r.uri.path == '/api/me/wrong-words'),
        hasLength(1),
      );
      expect(
        adapter.received.where((r) => r.uri.path == '/api/me/wrong-chars'),
        isEmpty,
        reason: '英语页签不该去拉语文错字',
      );
      expect(find.text('教室'), findsOneWidget);
      expect(find.text('重背这篇'), findsNothing);
    });

    testWidgets('页内切到语文 → 出现「重背这篇」，英语的「只练错题」按钮消失', (tester) async {
      final adapter = defaultAdapter(
        wrongWords: wrongWordsJson([
          wrongItemJson(wordId: 88, unitId: 11, unitName: 'Unit 1', en: 'classroom', cn: '教室'),
        ], 1),
        wrongChars: wrongCharsJson([wrongCharItemJson(textId: 7, char: '不')], 1),
      );
      await boot(tester, adapter);

      // 进来是语文（initialSubject）
      expect(find.text('重背这篇'), findsOneWidget);

      await tester.tap(find.text('英语'));
      await tester.pumpAndSettle();
      expect(find.text('只练错题'), findsOneWidget);
      expect(find.text('重背这篇'), findsNothing);

      await tester.tap(find.text('语文'));
      await tester.pumpAndSettle();
      expect(find.text('重背这篇'), findsOneWidget);
    });
  });
}
