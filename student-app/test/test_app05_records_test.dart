/// WP-APP-05 「我的记录」场次列表 / 单次详情 —— F2 / U3
/// （契约 **§14.1 缺口 7** → 接口设计 **§9.5**，2026-09-25 补齐）。
///
/// 这套用例盯的是**三件容易假绿的事**：
///
/// 1. **`/api/sessions` 同名不同义**：`GET` = 我的记录列表（§9.5.1），
///    `POST` = 创建场次（§9.1）。假适配器若只按 `path` 匹配（曾经就是这样），
///    会把"创建场次"的响应喂给列表 —— 页面不报错，`items` 里的
///    `{seq,mode,prompt}` 被当成场次摘要悄悄解析，记录永远显示不出来。
///    所以这里除了断言渲染结果，还用 `adapter.received` 钉住**真实方法**。
/// 2. **`ongoing` 场次必须没有详情入口**：服务端对进行中的场次拒绝下发逐题明细
///    （`expected` 就是答案，中途返回等于泄题），点下去必然报错。
/// 3. **空 / 失败时如实说**：不编造场次、不拿 0 分冒充成绩。
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/features/mine/my_records_page.dart';
import 'package:student_app/features/mine/session_detail_page.dart';
import 'package:student_app/features/mine/session_records.dart';

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
    Widget page = const MyRecordsPage(),
    List<NavigatorObserver>? observers,
  }) async {
    useTallScreen(tester);
    await tester.pumpWidget(
      bootApp(
        page,
        local: local,
        serverStore: serverStore,
        adapter: adapter,
        observers: observers,
      ),
    );
    await tester.pumpAndSettle();
  }

  group('F2 场次列表（GET /api/sessions，§9.5.1）', () {
    testWidgets('渲染服务端场次：学科·单元 / 用时 / 项数 / 成绩', (tester) async {
      final adapter = defaultAdapter(
        sessions: sessionsJson(
          [
            sessionRecordJson(
              sessionId: 9001,
              unitName: 'Unit 3 My Family',
              totalItems: 20,
              accuracy: 0.9,
              score: 88,
              durationMs: 96000,
            ),
          ],
          1,
        ),
      );
      await boot(tester, adapter);

      expect(find.text('练习记录'), findsOneWidget);
      expect(find.text('共 1 次'), findsOneWidget);
      expect(find.text('英语练习 · Unit 3 My Family'), findsOneWidget);
      expect(find.text('88'), findsOneWidget); // 成绩取整，不显示 88.0
      expect(find.text('90%'), findsOneWidget);
      expect(find.textContaining('1 分 36 秒'), findsOneWidget); // 96_000ms
      expect(find.textContaining('20 个词'), findsOneWidget);

      // 真·GET 列表请求：路径对、方法对、分页参数对。
      final listCalls = adapter.received
          .where((r) => r.uri.path == '/api/sessions')
          .toList();
      expect(listCalls, hasLength(1));
      expect(listCalls.single.method, 'GET');
      expect(listCalls.single.uri.queryParameters['page'], '1');
      expect(listCalls.single.uri.queryParameters['page_size'], '20');
    });

    testWidgets('语文场次的项数口径是「字」，标题走「语文背诵」', (tester) async {
      final adapter = defaultAdapter(
        sessions: sessionsJson(
          [
            sessionRecordJson(
              sessionId: 9100,
              contentType: 'chinese',
              unitName: '第三单元 古诗',
              totalItems: 120,
              correctItems: 110,
              accuracy: 0.9167,
              score: 86,
            ),
          ],
          1,
        ),
      );
      await boot(tester, adapter);

      expect(find.text('语文背诵 · 第三单元 古诗'), findsOneWidget);
      expect(find.textContaining('120 字'), findsOneWidget);
      expect(find.textContaining('92%'), findsOneWidget); // 0.9167 取整
    });

    testWidgets('多条场次按服务端顺序渲染，行与行之间有分隔', (tester) async {
      final adapter = defaultAdapter(
        sessions: sessionsJson(
          [
            sessionRecordJson(sessionId: 9002, unitName: 'Unit 4', score: 70),
            sessionRecordJson(sessionId: 9001, unitName: 'Unit 3', score: 88),
          ],
          2,
        ),
      );
      await boot(tester, adapter);

      expect(find.text('共 2 次'), findsOneWidget);
      expect(find.byKey(const ValueKey<String>('record-9002')), findsOneWidget);
      expect(find.byKey(const ValueKey<String>('record-9001')), findsOneWidget);
      expect(find.byType(Divider), findsOneWidget);
    });

    testWidgets('进行中的场次：有「进行中」标签，但**没有**可点的详情入口', (tester) async {
      final adapter = defaultAdapter(
        // 契约：ongoing 场次没有成绩、没有结束时间、用时按 0。
        sessions: sessionsJson(
          [
            sessionRecordJson(
              sessionId: 9100,
              unitName: 'Unit 5',
              status: 'ongoing',
              score: null,
              endedAt: null,
              durationMs: 0,
            ),
          ],
          1,
        ),
      );
      final spy = PushSpy();
      await boot(tester, adapter, observers: [spy]);
      // `PushSpy` 也会记下 `home` 那条初始路由，先丢掉再断言。
      spy.pushed.clear();
      spy.pushedNames.clear();

      expect(find.text('进行中'), findsOneWidget);
      final row = find.byKey(const ValueKey<String>('record-9100'));
      expect(row, findsOneWidget);
      // 行的**根 widget 不是 InkWell** —— 压根没有手势识别器。
      // 注意不是"点了没反应"：给它一个必然报错的入口，比不给更糟。
      expect(
        tester.widget(row),
        isNot(isA<InkWell>()),
        reason: 'ongoing 场次不得有详情入口（服务端拒绝下发逐题明细，点下去必然报错）',
      );
      expect(spy.pushed, isEmpty);
    });

    testWidgets('已结束的场次：点进单次详情', (tester) async {
      final adapter = defaultAdapter(
        sessions: sessionsJson([sessionRecordJson(sessionId: 9001)], 1),
        sessionDetail: sessionDetailJson(
          sessionRecordJson(sessionId: 9001),
          items: [sessionItemJson()],
        ),
      );
      await boot(tester, adapter);

      final row = find.byKey(const ValueKey<String>('record-9001'));
      expect(
        tester.widget(row),
        isA<InkWell>(),
        reason: '已结束的场次必须可点进详情',
      );

      await tester.tap(row);
      await tester.pumpAndSettle();

      expect(find.text('这次练习'), findsOneWidget);
      expect(find.text('每个词的情况'), findsOneWidget);
      // 详情请求打的是带 id 的那条路径。
      final detailCalls = adapter.received
          .where((r) => r.uri.path == '/api/sessions/9001')
          .toList();
      expect(detailCalls, hasLength(1));
      expect(detailCalls.single.method, 'GET');
    });

    testWidgets('一条记录都没有：如实引导，不编造场次', (tester) async {
      final adapter = defaultAdapter(sessions: sessionsJson(const [], 0));
      await boot(tester, adapter);

      expect(find.textContaining('还没有练习记录'), findsOneWidget);
      expect(find.byKey(const ValueKey<String>('record-1')), findsNothing);
      expect(find.text('加载更多'), findsNothing);
    });

    testWidgets('列表接口失败：中文错误 + 可重试，不静默吞掉', (tester) async {
      final adapter = JsonAdapter((options) {
        if (options.uri.path == '/api/sessions') {
          return '{"code":10006,"message":"网络开小差了","data":null,"trace_id":"t-9"}';
        }
        return okBody(progressJson());
      });
      await boot(tester, adapter);

      expect(find.textContaining('网络开小差了'), findsOneWidget);
      expect(find.text('再试一次'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('刷新入口：点了会重新拉，新场次立刻出现在列表里', (tester) async {
      // 底部 Tab 是 `IndexedStack`（保活），列表不会自己刷新 —— 所以这个入口是有用的，
      // 不是摆设：第二轮请求必须真的发出、且新数据要真的渲染出来。
      var round = 0;
      final adapter = JsonAdapter((options) {
        if (options.uri.path == '/api/sessions') {
          round += 1;
          if (round == 1) {
            return okBody(sessionsJson([sessionRecordJson(sessionId: 9001)], 1));
          }
          return okBody(
            sessionsJson(
              [
                sessionRecordJson(sessionId: 9002, unitName: 'Unit 9'),
                sessionRecordJson(sessionId: 9001),
              ],
              2,
            ),
          );
        }
        return okBody(progressJson());
      });
      await boot(tester, adapter);

      expect(find.text('共 1 次'), findsOneWidget);
      expect(find.byKey(const ValueKey<String>('record-9002')), findsNothing);

      await tester.tap(find.byKey(const ValueKey<String>('records-refresh')));
      await tester.pumpAndSettle();

      expect(round, 2, reason: '刷新必须真的再发一次请求');
      expect(find.text('共 2 次'), findsOneWidget);
      expect(find.byKey(const ValueKey<String>('record-9002')), findsOneWidget);
    });

    testWidgets('分页：还有下一页时给「加载更多」，点了续拉第 2 页并合并', (tester) async {
      // total=25 > 首页 20 条 → 应当出现「加载更多」；第 2 页给完剩下 5 条后按钮消失。
      final firstPage = List.generate(
        20,
        (i) => sessionRecordJson(sessionId: 100 + i, unitName: 'Unit $i'),
      );
      final secondPage = List.generate(
        5,
        (i) => sessionRecordJson(sessionId: 200 + i, unitName: 'Unit ${20 + i}'),
      );
      final adapter = JsonAdapter((options) {
        if (options.uri.path == '/api/sessions') {
          final page = options.uri.queryParameters['page'] ?? '1';
          if (page == '2') {
            return okBody(sessionsJson(secondPage, 25, page: 2));
          }
          return okBody(sessionsJson(firstPage, 25));
        }
        return okBody(progressJson());
      });
      await boot(tester, adapter);

      expect(find.text('共 25 次'), findsOneWidget);
      expect(find.text('加载更多'), findsOneWidget);
      expect(find.byKey(const ValueKey<String>('record-200')), findsNothing);

      await tester.tap(find.text('加载更多'));
      await tester.pumpAndSettle();

      expect(find.byKey(const ValueKey<String>('record-200')), findsOneWidget);
      // 合并而非替换：第一页那条还在，且凑满 25 条后不再显示「加载更多」。
      expect(find.byKey(const ValueKey<String>('record-100')), findsOneWidget);
      expect(find.text('加载更多'), findsNothing);
    });
  });

  group('F2 单次详情（GET /api/sessions/{id}，§9.5.2）', () {
    testWidgets('英语场次：逐项明细 + 错答回显（你答的是什么 / 应该是什么）', (tester) async {
      final record = sessionRecordJson(
        sessionId: 9001,
        unitName: 'Unit 3 My Family',
        totalItems: 2,
        correctItems: 1,
        accuracy: 0.5,
        score: 50,
        hintCount: 1,
      );
      final adapter = defaultAdapter(
        sessionDetail: sessionDetailJson(
          record,
          items: [
            sessionItemJson(
              wordId: 88,
              en: 'classroom',
              cn: '教室',
              state: 'wrong',
              yourAnswer: 'classrom',
              expected: 'classroom',
              matchType: 'fuzzy',
              hintLevel: 1,
            ),
            sessionItemJson(
              wordId: 89,
              en: 'library',
              cn: '图书馆',
              state: 'correct',
            ),
          ],
        ),
      );
      await boot(
        tester,
        adapter,
        page: SessionDetailPage(
          record: SessionRecord.fromJson(record),
        ),
      );

      expect(find.text('每个词的情况'), findsOneWidget);
      expect(find.text('classroom'), findsOneWidget);
      expect(find.text('教室'), findsOneWidget);
      expect(
        find.textContaining('你答的是「classrom」，应该是「classroom」'),
        findsOneWidget,
      );
      expect(find.textContaining('用了第 1 档提示'), findsOneWidget);
      expect(find.text('library'), findsOneWidget);
      expect(find.text('答对'), findsWidgets); // 既有指标标签，也有这行的状态标签
      expect(find.text('答错'), findsOneWidget);
    });

    testWidgets('语文场次：按篇明细 + **服务端重建**的漏字', (tester) async {
      final record = sessionRecordJson(
        sessionId: 9100,
        contentType: 'chinese',
        unitName: '第三单元 古诗',
        totalItems: 120,
        correctItems: 110,
        accuracy: 0.9167,
        score: 86,
        durationMs: 460000,
        hintCount: 1,
      );
      final adapter = defaultAdapter(
        sessionDetail: sessionDetailJson(
          record,
          recitations: [
            recitationJson(
              recitationId: 301,
              title: '静夜思',
              totalChars: 40,
              matchedChars: 38,
              coveredRatio: 0.95,
              grade: '良好',
              durationMs: 45000,
              missedDetail: [missedCharJson(12, '霜')],
            ),
          ],
        ),
      );
      await boot(
        tester,
        adapter,
        page: SessionDetailPage(
          record: SessionRecord.fromJson(record),
        ),
      );

      expect(find.text('每一篇的情况'), findsOneWidget);
      expect(find.text('静夜思'), findsOneWidget);
      expect(find.textContaining('背出 38/40 个字'), findsOneWidget);
      expect(find.textContaining('良好'), findsOneWidget);
      // 漏字由服务端按 content_plain[start:end] 重建，客户端只渲染。
      expect(find.textContaining('漏了 1 个字：霜'), findsOneWidget);
      expect(find.text('背出比例'), findsOneWidget);
      expect(find.textContaining('110/120 个字'), findsOneWidget);
    });

    testWidgets('硬闯进行中的场次：走服务端错误（10003）中文上屏，而不是空明细', (tester) async {
      final record = sessionRecordJson(
        sessionId: 9100,
        status: 'ongoing',
        score: null,
        endedAt: null,
        durationMs: 0,
      );
      final adapter = JsonAdapter((options) {
        if (options.uri.path == '/api/sessions/9100') {
          return '{"code":10003,"message":"该场次还没结束，暂时看不到逐题明细",'
              '"data":null,"trace_id":"t-3"}';
        }
        return okBody(progressJson());
      });
      await boot(
        tester,
        adapter,
        page: SessionDetailPage(record: SessionRecord.fromJson(record)),
      );

      expect(find.textContaining('该场次还没结束'), findsOneWidget);
      // 关键：**没有**逐题明细（防泄题）——连"这次没有可复盘的词条"都不该出现，
      // 因为那不是"没题"，是"服务端拒绝给"。
      expect(find.text('每个词的情况'), findsNothing);
      expect(tester.takeException(), isNull);
    });
  });
}
