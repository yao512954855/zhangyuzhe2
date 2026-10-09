/// WP-C 首页 / 单元列表验收 —— U1 / U2 / U14（30-学生端 WP-C 验收用例）。
///
/// U1  首页两按钮：布局、置灰逻辑、跳转正确
/// U2  单元列表 + 类型单选：数据来自 /api/units；无内容类型不可选，
///     start 返回 20004 时上屏服务端消息
/// U14 入口可达：首页 → 单元列表 → 模式选择 → 答题页 全链路无孤儿页
library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/providers.dart';
import 'package:student_app/core/router/app_router.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/theme/app_theme.dart';
import 'package:student_app/features/home/home_page.dart';
import 'package:student_app/features/recite/spell_page.dart';
import 'package:student_app/router/routes/home.dart';
import 'package:student_app/router/routes/recite.dart';

import 'support/fake_home_repository.dart';

List<Map<String, dynamic>> sampleUnits() => const [
      {
        'id': 1,
        'name': 'Starter Unit 1',
        'sort': 1,
        'counts': {'word': 5, 'phrase': 2, 'sentence': 0},
      },
      {
        'id': 2,
        'name': 'Unit 1 Hello!',
        'sort': 2,
        'counts': {'word': 0, 'phrase': 0, 'sentence': 0},
      },
    ];

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    SharedPreferences.setMockInitialValues({
      StoreKeys.serverHost: '127.0.0.1',
      StoreKeys.serverPort: 8810,
    });
    local = await LocalStore.open();
    serverStore = ServerStore(local);
  });

  JsonAdapter adapter({String? startResponse}) {
    return JsonAdapter((options) {
      switch (options.uri.path) {
        case '/api/units':
          return okBody(sampleUnits());
        case '/api/practice/start':
          return startResponse ??
              okBody(<String, dynamic>{
                  'session_id': 'ps-1',
                  'items': [
                    {
                      'id': 101,
                      'en': 'apple',
                      'pos': 'n.',
                      'zh': '苹果',
                      'alt_zh': '',
                    },
                  ],
                });
        default:
          return okBody(null);
      }
    });
  }

  Widget boot(JsonAdapter adapter) {
    return ProviderScope(
      overrides: [
        localStoreProvider.overrideWithValue(local),
        apiClientProvider.overrideWithValue(
          clientWith(adapter, serverStore: serverStore, localStore: local),
        ),
      ],
      child: MaterialApp(
        theme: AppTheme.build(palette: AppPalettes.day),
        // AgeScope 挂 builder：所有命名路由页都能取档位（与 app.dart 一致）。
        builder: (context, child) => AgeScope(
          profile: AgeProfile.of(AgeBand.junior),
          child: child!,
        ),
        // home: 直开首页（initialRoute: '/home' 会先把 '/' 也压栈，
        // bootstrap 的探活 Timer 在测试里变成挂起 Timer；
        // 而 home 与 routes['/'] 又互斥 → 拷贝一份去掉 '/'）。
        routes: Map<String, WidgetBuilder>.of(AppRouter.routes)
          ..remove('/'),
        home: const HomePage(),
      ),
    );
  }

  InkWell inkWellOf(WidgetTester tester, String label) {
    final candidates = find.ancestor(
      of: find.text(label),
      matching: find.byType(InkWell),
    );
    return tester.widget<InkWell>(candidates.last);
  }

  testWidgets('U1 首页两按钮：听背可点、错题本置灰带「即将开放」', (tester) async {
    await tester.pumpWidget(boot(adapter()));
    await tester.pump();

    expect(find.text('听背'), findsOneWidget);
    expect(find.text('错题本'), findsOneWidget);
    expect(find.text('即将开放'), findsOneWidget);

    // 置灰逻辑：错题本的 InkWell.onTap 为 null，听背的能点
    expect(inkWellOf(tester, '错题本').onTap, isNull,
        reason: 'WP-E 未交付，错题本入口必须置灰（接力项 J2）');
    expect(inkWellOf(tester, '听背').onTap, isNotNull);
  });

  testWidgets('U1b 首页点【听背】进单元列表', (tester) async {
    useTallScreen(tester);
    await tester.pumpWidget(boot(adapter()));
    await tester.pump();

    await tester.tap(find.text('听背'));
    await tester.pumpAndSettle();

    expect(find.text('Starter Unit 1'), findsOneWidget);
  });

  testWidgets('U2 单元列表数据来自 /api/units；类型单选；空类型不可选', (tester) async {
    useTallScreen(tester);
    await tester.pumpWidget(boot(adapter()));
    await tester.pump();
    await tester.tap(find.text('听背'));
    await tester.pumpAndSettle();

    // 数据来自 /api/units
    expect(find.text('Starter Unit 1'), findsOneWidget);
    expect(find.text('Unit 1 Hello!'), findsOneWidget);
    expect(find.text('暂无内容'), findsOneWidget);

    // 默认选中单词（有内容）→ 底部提示「开始单词背诵」
    expect(find.text('开始单词背诵'), findsOneWidget);

    // 短句 0 条 → 点了不生效（仍停在单词）
    await tester.tap(find.text('短句'));
    await tester.pump();
    expect(find.text('开始单词背诵'), findsOneWidget);

    // 切到词组（渲染为「词组 2」）→ 提示跟随
    await tester.tap(find.textContaining('词组'));
    await tester.pump();
    expect(find.text('开始词组背诵'), findsOneWidget);

    // 【听】已接线（J2' 销账 2026-10-09）：选中单元+类型后可点，走 mode=listen 组卷
    final listenWell = tester.widget<InkWell>(
      find.ancestor(of: find.text('听'), matching: find.byType(InkWell)).last,
    );
    expect(listenWell.onTap, isNotNull);
  });

  testWidgets('U2b start 返回 20004 时上屏服务端消息', (tester) async {
    useTallScreen(tester);
    // 20004 无可练内容（契约 §4 错误码表）；不带 trace_id → displayText=message
    final bizError = jsonEncode({
      'code': 20004,
      'message': '这个单元下没有可练的内容',
      'data': null,
    });
    await tester.pumpWidget(boot(adapter(startResponse: bizError)));
    await tester.pump();
    await tester.tap(find.text('听背'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('背'));
    await tester.pumpAndSettle();

    // 进了模式选择页
    expect(find.text('看中文背英文'), findsOneWidget);
    await tester.tap(find.text('看中文背英文'));
    await tester.pumpAndSettle();

    expect(find.text('这个单元下没有可练的内容'), findsOneWidget,
        reason: '20004 无可练内容：把服务端 message 原样上屏');
  });

  testWidgets('U14 全链路入口可达：首页→单元列表→模式选择→答题页', (tester) async {
    useTallScreen(tester);
    await tester.pumpWidget(boot(adapter()));
    await tester.pump();

    // 路由表完整性（无孤儿页：链路五页全部注册）
    for (final route in [
      HomeRoutes.home,
      HomeRoutes.unitList,
      ReciteRoutes.spellConfig,
      ReciteRoutes.spell,
      ReciteRoutes.settle,
    ]) {
      expect(AppRouter.routes.containsKey(route), isTrue,
          reason: '$route 应已注册');
    }

    await tester.tap(find.text('听背'));
    await tester.pumpAndSettle();
    expect(find.text('Starter Unit 1'), findsOneWidget);

    await tester.tap(find.text('背'));
    await tester.pumpAndSettle();
    expect(find.text('Starter Unit 1 · 背诵'), findsOneWidget);

    await tester.tap(find.text('看中文背英文'));
    await tester.pumpAndSettle();

    // 到了答题页：中文题面 + 开始按钮
    expect(find.text('苹果'), findsOneWidget);
    expect(find.text('开始拼读'), findsOneWidget);
    expect(find.byType(SpellPage), findsOneWidget);
  });
}
