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
import 'package:student_app/core/widgets/app_button.dart';
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

  testWidgets('U1 首页两按钮：听背与错题本均可点（J2 已接线）', (tester) async {
    await tester.pumpWidget(boot(adapter()));
    await tester.pump();

    expect(find.text('听背'), findsOneWidget);
    expect(find.text('错题本'), findsOneWidget);
    expect(find.text('错了的题都在这'), findsOneWidget);

    // J2 销账（2026-10-09 集成接线）：两入口 onTap 均非空
    expect(inkWellOf(tester, '错题本').onTap, isNotNull,
        reason: 'WP-E 已交付，错题本入口必须可点');
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

  testWidgets('U1c 主题入口：AppBar 调色板按钮弹主题面板，选择持久化（D2 修复）',
      (tester) async {
    await tester.pumpWidget(boot(adapter()));
    await tester.pump();

    // 入口在第一屏（AppBar actions），tooltip 即语义标签
    expect(find.byTooltip('主题'), findsOneWidget);
    await tester.tap(find.byTooltip('主题'));
    await tester.pumpAndSettle();

    // 四模式 + 自动暖色开关齐备（弹层组件 APP-01 交付，此前无入口成孤儿）
    expect(find.text('选个看着舒服的'), findsOneWidget);
    for (final label in ['跟随系统', '日间', '纸质护眼', '夜间']) {
      expect(find.text(label), findsOneWidget);
    }
    expect(find.text('晚上自动变暗'), findsOneWidget);

    // 选「夜间」→ 落本地存储（下次打开还用它）
    await tester.tap(find.text('夜间'));
    await tester.pumpAndSettle();
    expect(local.themeId, 'night', reason: '主题选择必须持久化到 LocalStore');
  });

  testWidgets('U2 单元列表数据来自 /api/units；类型多选 checkbox；未选时按钮禁用',
      (tester) async {
    useTallScreen(tester);
    await tester.pumpWidget(boot(adapter()));
    await tester.pump();
    await tester.tap(find.text('听背'));
    await tester.pumpAndSettle();

    // 数据来自 /api/units
    expect(find.text('Starter Unit 1'), findsOneWidget);
    expect(find.text('Unit 1 Hello!'), findsOneWidget);
    expect(find.text('暂无内容'), findsOneWidget);

    // todo11 A2：类型栏多选化（radio → checkbox），默认全不选
    for (final key in ['word', 'phrase', 'sentence']) {
      expect(find.byKey(ValueKey('check-type-$key')), findsOneWidget);
    }

    // F15-2：还没勾单元/类型 → 开始按钮禁用 + 引导提示
    expect(
      tester.widget<AppButton>(
        find.widgetWithText(AppButton, '背').last,
      ).onPressed,
      isNull,
    );
    expect(find.text('先在上面勾选要练的单元'), findsOneWidget);

    // 勾单元 + 勾类型 → 可开始（有内容类型计数才亮）
    await tester.tap(find.text('Starter Unit 1'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('check-type-word')));
    await tester.pumpAndSettle();
    expect(find.text('已选 1 个单元 · 共 5 条'), findsOneWidget);
    expect(
      tester.widget<AppButton>(
        find.widgetWithText(AppButton, '背').last,
      ).onPressed,
      isNotNull,
    );

    // 【听】同一规则（F15-5）：可点，走 mode=listen 多选组卷
    expect(
      tester.widget<AppButton>(
        find.widgetWithText(AppButton, '听').last,
      ).onPressed,
      isNotNull,
    );
  });

  testWidgets('U2b start 返回 20004 时上屏服务端消息', (tester) async {
    useTallScreen(tester);
    // 20004 无可练内容（契约 §4 错误码表）；不带 trace_id → displayText=message
    final bizError = jsonEncode({
      'code': 20004,
      'message': '所选单元里没有可练的内容',
      'data': null,
    });
    await tester.pumpWidget(boot(adapter(startResponse: bizError)));
    await tester.pump();
    await tester.tap(find.text('听背'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Starter Unit 1'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('check-type-word')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(AppButton, '背').last);
    await tester.pumpAndSettle();

    // 进了模式选择页
    expect(find.text('看中文背英文'), findsOneWidget);
    await tester.tap(find.text('看中文背英文'));
    await tester.pumpAndSettle();

    expect(find.text('所选单元里没有可练的内容'), findsOneWidget,
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

    // todo11 A1/A2：先勾 1 个单元 + 1 个类型才能开始
    await tester.tap(find.text('Starter Unit 1'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('check-type-word')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(AppButton, '背').last);
    await tester.pumpAndSettle();
    expect(find.text('Starter Unit 1 · 背诵'), findsOneWidget);

    await tester.tap(find.text('看中文背英文'));
    await tester.pumpAndSettle();

    // 到了答题页（v1.3 按住说话交互的页内断言归 APP-12 U16，这里只验链路可达）
    expect(find.byType(SpellPage), findsOneWidget);
    expect(find.text('苹果'), findsOneWidget);
  });
}
