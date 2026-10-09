/// APP-02 练习配置页 —— U7 / U8 / U9 + F7 / F11 / F13。
///
/// U9（"配置页全部用卡片，无下拉框"）在这里是**可执行断言**，
/// 而不是一句写在文档里的要求 —— 下拉框一旦被谁加回来，用例立刻红。
library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/theme/app_theme.dart';
import 'package:student_app/core/widgets/app_button.dart';
import 'package:student_app/features/config/practice_config_page.dart';
import 'package:student_app/features/home/home_controller.dart';
import 'package:student_app/features/home/models.dart';
import 'package:student_app/router/routes/practice.dart';

import 'support/fake_home_repository.dart';

UnitItem englishUnit({int? withError, int total = 24, int mastered = 14}) => UnitItem(
      unitId: 11,
      subject: Subject.english,
      name: 'Unit 1 Hello!',
      wordTotal: total,
      practiced: 20,
      mastered: mastered,
      withError: withError,
      accuracy: 0.88,
      status: MasteryStatus.practicedWithError,
    );

UnitItem chineseUnit() => const UnitItem(
      unitId: 12,
      subject: Subject.chinese,
      name: '第一单元',
      textTotal: 3,
      recited: 1,
      lastGrade: '良好',
      status: MasteryStatus.practicedOk,
    );

List<TextBrief> sampleTexts() => const [
      TextBrief(id: 301, unitId: 12, title: '背影', author: '朱自清', reciteScopeChars: 620),
      TextBrief(id: 302, unitId: 12, title: '春', author: '朱自清', reciteScopeChars: 300),
      TextBrief(id: 303, unitId: 12, title: '济南的冬天', author: '老舍', reciteScopeChars: 400),
    ];

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    SharedPreferences.setMockInitialValues({
      StoreKeys.serverHost: '127.0.0.1',
      StoreKeys.serverPort: 8810,
      StoreKeys.authToken: 'test-token',
    });
    local = await LocalStore.open();
    serverStore = ServerStore(local);
  });

  Widget boot(
    Widget page, {
    FakeHomeRepository? repo,
    JsonAdapter? adapter,
    Map<String, WidgetBuilder>? routes,
  }) {
    return ProviderScope(
      overrides: [
        localStoreProvider.overrideWithValue(local),
        homeRepositoryProvider.overrideWithValue(repo ?? FakeHomeRepository()),
        if (adapter != null)
          apiClientProvider.overrideWithValue(
            clientWith(adapter, serverStore: serverStore, localStore: local),
          ),
      ],
      child: MaterialApp(
        theme: AppTheme.build(palette: AppPalettes.day),
        // 配置页「开始练习」会 `pushNamed('/practice')`。这里**只**注册调用方要用的
        // 那几条路由（用桩页），不去拉 APP-03 的真实页面 —— 本包测试只该验证
        // 「请求发对了 + 卷交给了练习页」这层边界；练习页自身由 APP-03 / QA-01 验。
        routes: routes ?? const <String, WidgetBuilder>{},
        home: AgeScope(profile: AgeProfile.of(AgeBand.junior), child: page),
      ),
    );
  }

  group('U9 全部用卡片，无下拉框', () {
    testWidgets('英语配置页找不到任何下拉控件', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(boot(PracticeConfigPage(unit: englishUnit())));
      await tester.pumpAndSettle();

      expect(
        find.byWidgetPredicate(
          (widget) =>
              widget is DropdownButton ||
              widget is DropdownButtonFormField ||
              widget is DropdownMenu,
        ),
        findsNothing,
        reason: 'UI 规范 P5：所有选项必须用卡片 / 胶囊，绝不用下拉框',
      );
    });

    testWidgets('语文配置页同样没有下拉框', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(
        boot(
          PracticeConfigPage(unit: chineseUnit()),
          repo: FakeHomeRepository(texts: sampleTexts()),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byWidgetPredicate((widget) => widget is DropdownButton),
        findsNothing,
      );
    });
  });

  group('U8 预估随选择实时更新', () {
    testWidgets('默认整单元 24 题', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(boot(PracticeConfigPage(unit: englishUnit())));
      await tester.pumpAndSettle();

      expect(find.textContaining('本次 24 题'), findsOneWidget);
    });

    testWidgets('改成「随机几个」→ 题量跟着变（N=10）', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(boot(PracticeConfigPage(unit: englishUnit())));
      await tester.pumpAndSettle();

      await tester.tap(find.text('随机几个'));
      await tester.pumpAndSettle();

      expect(find.textContaining('本次 10 题'), findsOneWidget);
      // 步进器出现（可以自己调 N）
      expect(find.text('10个'), findsOneWidget);
    });

    testWidgets('改成「只练错题」→ 题量按错题数（后端给了 with_error=3）', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(
        boot(PracticeConfigPage(unit: englishUnit(withError: 3))),
      );
      await tester.pumpAndSettle();

      expect(find.textContaining('本次 24 题'), findsOneWidget);

      await tester.tap(find.text('只练错题'));
      await tester.pumpAndSettle();

      expect(find.textContaining('本次 3 题'), findsOneWidget);
    });

    testWidgets('选「每题限时 15 秒」→ 用时被限时压下来', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(boot(PracticeConfigPage(unit: englishUnit())));
      await tester.pumpAndSettle();

      // 24 题 × 18s = 432s ≈ 7 分钟
      expect(find.textContaining('本次 24 题'), findsOneWidget);

      await tester.tap(find.text('15 秒/题'));
      await tester.pumpAndSettle();

      // 24 题 × 15s = 360s = 6 分钟
      expect(find.textContaining('本次 24 题'), findsOneWidget);
      expect(find.textContaining('约 6 分钟'), findsOneWidget);
    });

    testWidgets('一个题都没有时给出原因，且按钮不可点', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(
        boot(PracticeConfigPage(unit: englishUnit(total: 0, mastered: 0))),
      );
      await tester.pumpAndSettle();

      expect(find.text('这个单元还没有内容'), findsOneWidget);
      final button = tester.widget<AppButton>(
        find.widgetWithText(AppButton, '开始练习'),
      );
      expect(button.onPressed, isNull, reason: '一个题都组不出来时不该能点');
    });
  });

  group('U7 发起练习（F13 的请求体必须与契约 §9.1 一致）', () {
    testWidgets('只练错题 + 随机 10 个 + 每题限时 30 秒', (tester) async {
      useTallScreen(tester);
      final adapter = JsonAdapter((options) {
        if (options.uri.path == '/api/sessions') {
          return okBody({
            'session_id': 9001,
            'unit_id': 11,
            'unit_name': 'Unit 1 Hello!',
            'content_type': 'english',
            'items': [
              {
                'seq': 1,
                'word_id': 88,
                'mode': 'cn2en',
                'prompt': '教室',
                'require_spell': 1,
                'target_len': 9,
              },
            ],
            'time_limit_ms': 30000,
            'started_at': '2026-09-24T09:00:00Z',
          });
        }
        return okBody(const {});
      });

      await tester.pumpWidget(
        boot(
          PracticeConfigPage(unit: englishUnit(withError: 12)),
          adapter: adapter,
          // 组卷成功后要跳到 /practice（APP-03 已落地）——给个桩，避免把
          // APP-03 的整页依赖（录音 / WS）拉进本包的单测。
          routes: {PracticeRoutes.practice: (context) => const Scaffold()},
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('只练错题'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('随机几个'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('30 秒/题'));
      await tester.pumpAndSettle();

      await tester.tap(find.text('开始练习'));
      await tester.pumpAndSettle();

      final request = adapter.received.firstWhere((r) => r.uri.path == '/api/sessions');
      final raw = request.data;
      final body = raw is String
          ? jsonDecode(raw) as Map<String, dynamic>
          : (raw as Map).cast<String, dynamic>();

      expect(body['unit_id'], 11);
      expect(body['content_type'], 'english');

      final strategy = (body['strategy'] as Map).cast<String, dynamic>();
      expect(strategy['scope'], 'wrong');
      expect(strategy['count'], 10);
      expect(strategy['count_mode'], 'random_n');
      expect(strategy['order'], 'sequential');
      expect(strategy['mode'], 'cn2en');
      expect(strategy['time_limit_ms'], 30000);
    });

    // 注：原「开始练习后把组好的卷交给练习页（/practice）」用例随 WP-F 拆除
    // features/english 一并移除（英语练习页已删，/practice 不再注册）；
    // 组卷请求体契约仍由上一条用例钉住。

    testWidgets('服务端报错时显示中文提示（含编号），不显示英文堆栈', (tester) async {
      useTallScreen(tester);
      final adapter = JsonAdapter((options) {
        if (options.uri.path == '/api/sessions') {
          return jsonEncode({
            'code': 10001,
            'message': '参数不合法',
            'data': null,
            'trace_id': 'tr-123',
          });
        }
        return okBody(const {});
      });

      await tester.pumpWidget(
        boot(PracticeConfigPage(unit: englishUnit()), adapter: adapter),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('开始练习'));
      await tester.pumpAndSettle();

      expect(find.textContaining('参数不合法'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });

  group('选项按学科给（不给不生效的假选项）', () {
    testWidgets('英语不出现「选课文」', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(boot(PracticeConfigPage(unit: englishUnit())));
      await tester.pumpAndSettle();

      expect(find.text('选课文'), findsNothing);
      expect(find.text('整个单元'), findsOneWidget);
      expect(find.text('自定义区间'), findsOneWidget);
      expect(find.text('只练没掌握的'), findsOneWidget);
    });

    testWidgets('语文不出现「练多少个 / 出题顺序 / 怎么问」（后端未应用这些字段）', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(
        boot(
          PracticeConfigPage(unit: chineseUnit()),
          repo: FakeHomeRepository(texts: sampleTexts()),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('练多少个'), findsNothing);
      expect(find.text('出题顺序'), findsNothing);
      expect(find.text('怎么问'), findsNothing);
      // 但范围与时间限制要给
      expect(find.text('练哪部分'), findsOneWidget);
      expect(find.text('时间限制'), findsOneWidget);
      expect(find.text('全部'), findsNothing);
    });

    testWidgets('语文选课文：列出课文与背诵字数，默认全选', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(
        boot(
          PracticeConfigPage(unit: chineseUnit()),
          repo: FakeHomeRepository(texts: sampleTexts()),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('选课文'));
      await tester.pumpAndSettle();

      expect(find.text('背影'), findsOneWidget);
      expect(find.text('济南的冬天'), findsOneWidget);
      expect(find.textContaining('背诵 620 字'), findsOneWidget);
      expect(find.text('都不选'), findsOneWidget, reason: '默认全选，按钮显示为「都不选」');
      // 三篇都选中 → 题量 3
      expect(find.textContaining('本次 3 题'), findsOneWidget);
    });

    testWidgets('取消掉课文后题量减少', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(
        boot(
          PracticeConfigPage(unit: chineseUnit()),
          repo: FakeHomeRepository(texts: sampleTexts()),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('选课文'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('春'));
      await tester.pumpAndSettle();

      expect(find.textContaining('本次 2 题'), findsOneWidget);
    });
  });

  group('自定义区间（F8）', () {
    testWidgets('起止都能用加减号调，题量跟着变', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(boot(PracticeConfigPage(unit: englishUnit())));
      await tester.pumpAndSettle();

      await tester.tap(find.text('自定义区间'));
      await tester.pumpAndSettle();

      // 默认 1–24 → 24 题
      expect(find.text('从第'), findsOneWidget);
      expect(find.text('到第'), findsOneWidget);
      expect(find.textContaining('本次 24 题'), findsOneWidget);

      // 把"到第"减 4 次 → 20 → 20 题
      final plusButtons = find.byTooltip('减少');
      await tester.tap(plusButtons.last);
      await tester.pumpAndSettle();
      await tester.tap(plusButtons.last);
      await tester.pumpAndSettle();
      await tester.tap(plusButtons.last);
      await tester.pumpAndSettle();
      await tester.tap(plusButtons.last);
      await tester.pumpAndSettle();

      expect(find.textContaining('本次 20 题'), findsOneWidget);
    });
  });

  group('缺少参数时不崩', () {
    testWidgets('直接路由进来（没带单元）→ 给出明确指引', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(boot(const PracticeConfigPage()));
      await tester.pumpAndSettle();

      expect(find.text('请先在首页选一个单元'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });
}
