/// APP-11 两级多选 + 类型多选验收（todo11 A1/A2 / 契约 v1.3 §4.1）。
///
/// U15-1 两级列表：展开/收起、勾选联动（一级行勾选 = 全选其下二级）
/// U15-2 跨一级分组勾选 2 个单元 → 底部栏「已选 N 个单元 · 共 X 条」按求和
/// U15-3 所选单元下无所选类型内容 → 开始按钮禁用 + 提示
/// U15-4 start 请求体断言：unit_ids/types 数组、顺序与勾选一致
/// U15-5 只勾 1 个单元 → 数组单元素（契约允许，不发单数字段）
/// + 单数入口兼容：错题本重做等（unitId/type 单数）仍发单数字段
library;

import 'dart:convert';

import 'package:dio/dio.dart' show RequestOptions;
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
import 'package:student_app/features/home/practice_entry.dart';
import 'package:student_app/features/home/unit_list_page.dart';
import 'package:student_app/features/recite/spell_config_page.dart';
import 'package:student_app/router/routes/recite.dart';

import 'support/fake_home_repository.dart';

/// 包一层业务失败响应（`code != 0`，HTTP 仍 200 —— 契约 §4 信封）。
/// 不带 trace_id → displayText = 原始 message（与既有 U2b 同口径）。
String okBodyErr(int code, String message) => jsonEncode({
      'code': code,
      'message': message,
      'data': null,
    });

/// 两级单元树 fixture（契约 §4.1 v1.3：一级 counts 含二级汇总）。
///
/// - 三年级上册(10)：Lesson 1(11) word5/phrase2/sentence1、
///   Lesson 2(12) word3/phrase1/sentence1 → 汇总 word8/phrase3/sentence2
/// - 三年级下册(20)：Unit 1(21) word4/phrase0/sentence0
List<Map<String, dynamic>> sampleTree() => const [
      {
        'id': 10,
        'name': '三年级上册',
        'sort': 1,
        'parent_id': null,
        'counts': {'word': 8, 'phrase': 3, 'sentence': 2},
        'children': [
          {
            'id': 11,
            'name': 'Lesson 1',
            'sort': 1,
            'parent_id': 10,
            'counts': {'word': 5, 'phrase': 2, 'sentence': 1},
            'children': [],
          },
          {
            'id': 12,
            'name': 'Lesson 2',
            'sort': 2,
            'parent_id': 10,
            'counts': {'word': 3, 'phrase': 1, 'sentence': 1},
            'children': [],
          },
        ],
      },
      {
        'id': 20,
        'name': '三年级下册',
        'sort': 2,
        'parent_id': null,
        'counts': {'word': 4, 'phrase': 0, 'sentence': 0},
        'children': [
          {
            'id': 21,
            'name': 'Unit 1',
            'sort': 1,
            'parent_id': 20,
            'counts': {'word': 4, 'phrase': 0, 'sentence': 0},
            'children': [],
          },
        ],
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

  /// 记录收到的请求（断言 start 请求体用）。
  ///
  /// start 默认回 20004 业务错：本包验收只断请求体，
  /// 不需要真的落答题页（spell_page 归 APP-12 独占，不在本包编译图里）。
  ({JsonAdapter adapter, List<RequestOptions> received}) makeAdapter({
    String? startResponse,
  }) {
    final received = <RequestOptions>[];
    final adapter = JsonAdapter((options) {
      received.add(options);
      switch (options.uri.path) {
        case '/api/units':
          return okBody(sampleTree());
        case '/api/practice/start':
          return startResponse ??
              okBodyErr(20004, '所选单元里没有可练的内容');
        default:
          return okBody(null);
      }
    });
    return (adapter: adapter, received: received);
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
        builder: (context, child) => AgeScope(
          profile: AgeProfile.of(AgeBand.junior),
          child: child!,
        ),
        // 只注册本链路页面：答题页（spell_page）属 APP-12 独占，
        // 不 import 不路由，保持本包测试与其编译态解耦。
        routes: <String, WidgetBuilder>{
          ReciteRoutes.spellConfig: (_) => const SpellConfigPage(),
        },
        home: const UnitListPage(),
      ),
    );
  }

  Future<void> pumpList(WidgetTester tester, JsonAdapter adapter) async {
    useTallScreen(tester);
    await tester.pumpWidget(boot(adapter));
    await tester.pumpAndSettle();
  }

  AppButton backButton(WidgetTester tester) =>
      tester.widget<AppButton>(find.widgetWithText(AppButton, '背').last);

  group('U15-1 两级列表：展开/收起 + 勾选联动', () {
    testWidgets('一级默认展开；点行体收起/再展开；一级勾选框全选/全不选二级',
        (tester) async {
      final fake = makeAdapter();
      await pumpList(tester, fake.adapter);

      // 两级都渲染（一级分组 + 二级单元）
      expect(find.text('三年级上册'), findsOneWidget);
      expect(find.text('Lesson 1'), findsOneWidget);
      expect(find.text('Lesson 2'), findsOneWidget);
      expect(find.text('三年级下册'), findsOneWidget);
      expect(find.text('Unit 1'), findsOneWidget);

      // 一级汇总条数：8+3+2=13
      expect(find.text('13 条'), findsOneWidget);

      // 点一级行体 → 收起（二级消失），再点 → 展开
      await tester.tap(find.text('三年级上册'));
      await tester.pumpAndSettle();
      expect(find.text('Lesson 1'), findsNothing);
      expect(find.text('Lesson 2'), findsNothing);

      await tester.tap(find.text('三年级上册'));
      await tester.pumpAndSettle();
      expect(find.text('Lesson 1'), findsOneWidget);

      // 一级行勾选 = 全选其下二级：两个二级都变勾选态
      await tester.tap(find.byKey(const ValueKey('check-group-10')));
      await tester.pumpAndSettle();
      expect(
        tester.widget<InkWell>(
          find.byKey(const ValueKey('check-unit-11')),
        ).onTap,
        isNotNull,
      );
      // 二级行本身进入选中态（整行 Material 高亮 → 用图标断言）
      expect(find.byIcon(Icons.check_box_rounded),
          findsNWidgets(3)); // 一级 1 + 二级 2（底部类型框未选为 outline）

      // 再点一级 → 全不选
      await tester.tap(find.byKey(const ValueKey('check-group-10')));
      await tester.pumpAndSettle();
      expect(find.byIcon(Icons.check_box_rounded), findsNothing);
      expect(find.byIcon(Icons.indeterminate_check_box_rounded), findsNothing);
    });
  });

  group('U15-2 跨分组勾选与底部栏计数', () {
    testWidgets('跨一级分组勾选 2 个单元 → 「已选 2 个单元 · 共 X 条」按求和',
        (tester) async {
      final fake = makeAdapter();
      await pumpList(tester, fake.adapter);

      // 上册 Lesson 1（word5）+ 下册 Unit 1（word4）= 跨分组 2 个单元
      await tester.tap(find.text('Lesson 1'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Unit 1'));
      await tester.pumpAndSettle();

      // 未选类型 → 不显示计数，提示选类型，按钮禁用
      expect(find.text('至少选一种类型（单词 / 词组 / 短句）'), findsOneWidget);
      expect(backButton(tester).onPressed, isNull);

      // 勾「单词」→ 类型条数 = 5 + 4 = 9（按所选单元求和，F15-3）
      await tester.tap(find.byKey(const ValueKey('check-type-word')));
      await tester.pumpAndSettle();
      expect(find.text('单词 9'), findsOneWidget);
      expect(find.text('已选 2 个单元 · 共 9 条'), findsOneWidget);
      expect(backButton(tester).onPressed, isNotNull);
    });
  });

  group('U15-3 无所选类型内容 → 禁用 + 提示', () {
    testWidgets('所选单元下没有所选类型的内容 → 开始按钮禁用并提示', (tester) async {
      final fake = makeAdapter();
      await pumpList(tester, fake.adapter);

      // Unit 1 只有 word(4)，没有 phrase/sentence
      await tester.tap(find.text('Unit 1'));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('check-type-phrase')));
      await tester.pumpAndSettle();

      expect(find.text('所选单元里还没有这些类型的内容'), findsOneWidget);
      expect(backButton(tester).onPressed, isNull);
      expect(
        tester.widget<AppButton>(
          find.widgetWithText(AppButton, '听').last,
        ).onPressed,
        isNull,
        reason: '听写入口同一规则（F15-5）',
      );

      // 补选「单词」→ 合计有内容 → 恢复可开始
      await tester.tap(find.byKey(const ValueKey('check-type-word')));
      await tester.pumpAndSettle();
      expect(backButton(tester).onPressed, isNotNull);
    });
  });

  group('U15-4/U15-5 start 请求体数组化', () {
    testWidgets('U15-4 多选：unit_ids/types 数组、顺序与勾选一致', (tester) async {
      final fake = makeAdapter();
      await pumpList(tester, fake.adapter);

      // 勾选顺序：Lesson 2 → Lesson 1；类型顺序：词组 → 单词
      await tester.tap(find.text('Lesson 2'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Lesson 1'));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('check-type-phrase')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('check-type-word')));
      await tester.pumpAndSettle();

      // 背 → 模式选择页 → 看中文背英文 → 发 start（20004 回包，
      // 服务端消息上屏 = 请求已真正发出且走完 ApiClient 解包路径）
      await tester.tap(find.widgetWithText(AppButton, '背').last);
      await tester.pumpAndSettle();
      expect(find.text('看中文背英文'), findsOneWidget);
      await tester.tap(find.text('看中文背英文'));
      await tester.pumpAndSettle();
      expect(find.text('所选单元里没有可练的内容'), findsOneWidget);

      final startCalls = fake.received
          .where((r) => r.uri.path == '/api/practice/start')
          .toList();
      expect(startCalls, hasLength(1));
      expect(startCalls.single.data, <String, dynamic>{
        'mode': 'spell_zh2en',
        'unit_ids': [12, 11],
        'types': ['phrase', 'word'],
      }, reason: '数组顺序 = 勾选顺序（契约 §4.1：组卷按单元顺序）');
      expect(startCalls.single.data.containsKey('unit_id'), isFalse);
      expect(startCalls.single.data.containsKey('type'), isFalse);
    });

    testWidgets('U15-5 只勾 1 个单元 → 退化为单元素数组，不发单数字段',
        (tester) async {
      final fake = makeAdapter();
      await pumpList(tester, fake.adapter);

      await tester.tap(find.text('Lesson 1'));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('check-type-word')));
      await tester.pumpAndSettle();

      await tester.tap(find.widgetWithText(AppButton, '背').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('看中文背英文'));
      await tester.pumpAndSettle();
      expect(find.text('所选单元里没有可练的内容'), findsOneWidget);

      final startCalls = fake.received
          .where((r) => r.uri.path == '/api/practice/start')
          .toList();
      expect(startCalls, hasLength(1));
      expect(startCalls.single.data, <String, dynamic>{
        'mode': 'spell_zh2en',
        'unit_ids': [11],
        'types': ['word'],
      });
      expect(startCalls.single.data.containsKey('unit_id'), isFalse);
      expect(startCalls.single.data.containsKey('type'), isFalse);
    });

    test('单数入口兼容：错题本重做等（unitId/type 单数）仍发单数字段', () {
      expect(
        spellStartBody(
          const PracticeSelection(
            unitId: 7,
            unitName: 'U7',
            type: 'word',
          ),
          'spell_zh2en',
        ),
        <String, dynamic>{'unit_id': 7, 'type': 'word', 'mode': 'spell_zh2en'},
      );
      expect(
        spellStartBody(
          const PracticeSelection(
            unitIds: [7],
            unitNames: ['U7'],
            types: ['word'],
          ),
          'spell_zh2en',
        ),
        <String, dynamic>{
          'mode': 'spell_zh2en',
          'unit_ids': [7],
          'types': ['word'],
        },
      );
    });
  });
}
