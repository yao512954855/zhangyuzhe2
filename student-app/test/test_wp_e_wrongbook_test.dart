/// WP-E 错题本验收 —— U1 / U2 / U3 / U4 / U6（30-学生端 WP-E 验收用例）。
///
/// U1 列表分组与徽标：三种 mode 徽标正确，错误次数递增显示
/// U2 分页：翻页无重复无遗漏
/// U3 手动移出：确认弹窗 → DELETE → 列表消失
/// U4 重做入口按 mode 分发：listen 进听写、spell 进背诵对应模式
/// U6 空错题本空态：无崩溃、文案友好
/// （U5 判对移出 = C/D 链路职责 + 真后端验证，见包报告；U7 = analyze/test。）
library;

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
import 'package:student_app/features/dictation/dictation_settings_page.dart';
import 'package:student_app/features/recite/spell_page.dart';
import 'package:student_app/features/wrongbook/wrongbook_models.dart';
import 'package:student_app/features/wrongbook/wrongbook_page.dart';

import 'support/fake_home_repository.dart';

/// 三条样例错题：两个单元、三种 mode、错误次数 2/1/3（递增展示）。
List<Map<String, dynamic>> sampleRows() => [
      {
        'item_id': 101,
        'en': 'apple',
        'pos': 'n.',
        'zh': '苹果',
        'mode': 'spell_zh2en',
        'wrong_count': 2,
        'last_wrong_at': '2026-10-09T12:00:00Z',
        'unit_id': 1,
        'unit_name': 'Starter Unit 1',
        'type': 'word',
      },
      {
        'item_id': 102,
        'en': 'go shopping',
        'pos': '',
        'zh': '去购物',
        'mode': 'listen',
        'wrong_count': 1,
        'last_wrong_at': '2026-10-08T02:00:00Z',
        'unit_id': 1,
        'unit_name': 'Starter Unit 1',
        'type': 'phrase',
      },
      {
        'item_id': 103,
        'en': 'happy',
        'pos': 'adj.',
        'zh': '高兴的',
        'mode': 'spell_en2zh',
        'wrong_count': 3,
        'last_wrong_at': '2026-10-07T12:00:00Z',
        'unit_id': 2,
        'unit_name': 'Unit 1 Hello!',
        'type': 'word',
      },
    ];

/// `GET /api/wrongbook` 的假分页响应（走真 ApiClient 解包路径）。
String wrongbookBody(List<Map<String, dynamic>> rows, int page, int pageSize) {
  final start = (page - 1) * pageSize;
  final slice = rows.skip(start).take(pageSize).toList();
  return okBody({
    'items': slice,
    'total': rows.length,
    'page': page,
    'page_size': pageSize,
  });
}

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

  JsonAdapter adapter({
    List<Map<String, dynamic>> rows = const [],
    String? startResponse,
  }) {
    return JsonAdapter((options) {
      switch (options.uri.path) {
        case '/api/wrongbook':
          final page = int.parse(options.uri.queryParameters['page'] ?? '1');
          final size =
              int.parse(options.uri.queryParameters['page_size'] ?? '20');
          return wrongbookBody(rows, page, size);
        case '/api/practice/start':
          return startResponse ??
              okBody(<String, dynamic>{
                'session_id': 'ps-1',
                'items': [
                  for (final row in rows)
                    {
                      'id': row['item_id'],
                      'en': row['en'],
                      'pos': row['pos'],
                      'zh': row['zh'],
                      'alt_zh': '',
                    },
                ],
              });
        default:
          return okBody(null);
      }
    });
  }

  Widget boot(JsonAdapter fake) {
    return ProviderScope(
      overrides: [
        localStoreProvider.overrideWithValue(local),
        apiClientProvider.overrideWithValue(
          clientWith(fake, serverStore: serverStore, localStore: local),
        ),
      ],
      child: MaterialApp(
        theme: AppTheme.build(palette: AppPalettes.day),
        builder: (context, child) => AgeScope(
          profile: AgeProfile.of(AgeBand.junior),
          child: child!,
        ),
        // wrongbook 路由注册是接力项 J1（app_router.dart 归 WP-C）；
        // 测试里把两边的路由表拼起来模拟接线后的效果。
        routes: Map<String, WidgetBuilder>.of(AppRouter.routes)..remove('/'),
        home: const WrongbookPage(),
      ),
    );
  }

  testWidgets('U6 空错题本：空态文案友好，无崩溃', (tester) async {
    await tester.pumpWidget(boot(adapter()));
    await tester.pumpAndSettle();

    expect(find.text('错题本是空的'), findsOneWidget);
    expect(find.text('先去【听背】练一练，做错的题会自动收进来。'), findsOneWidget);
    // 空态不渲染底部操作栏（没有可重做的题）
    expect(find.textContaining('重做全部'), findsNothing);
  });

  testWidgets('U1 列表按单元分组，三种 mode 徽标与错误次数', (tester) async {
    useTallScreen(tester);
    await tester.pumpWidget(boot(adapter(rows: sampleRows())));
    await tester.pumpAndSettle();

    // 分组头：后端 group_by=unit 已按单元排序 → 连续两个组
    expect(find.text('Starter Unit 1 · 2 题'), findsOneWidget);
    expect(find.text('Unit 1 Hello! · 1 题'), findsOneWidget);

    // 题卡：英文 + 词性中文 + mode 徽标（§7 枚举文案）
    expect(find.text('apple'), findsOneWidget);
    expect(find.text('n. 苹果'), findsOneWidget);
    expect(find.text('go shopping'), findsOneWidget);
    expect(find.text('去购物'), findsOneWidget);
    expect(find.text('happy'), findsOneWidget);
    expect(find.text('adj. 高兴的'), findsOneWidget);
    expect(find.text('听写'), findsOneWidget);
    expect(find.text('看中文背英文'), findsOneWidget);
    expect(find.text('看英文背中文'), findsOneWidget);

    // 错误次数 + 最后错误时间（相对天）
    expect(find.textContaining('错 2 次'), findsOneWidget);
    expect(find.textContaining('错 1 次'), findsOneWidget);
    expect(find.textContaining('错 3 次'), findsOneWidget);
  });

  testWidgets('U2 分页：翻页无重复无遗漏', (tester) async {
    useTallScreen(tester);
    // 页面自己按 page_size=20 分页 → 造 25 条，跨 2 页
    final rows = [
      for (var i = 1; i <= 25; i++)
        {
          'item_id': i,
          'en': 'word$i',
          'pos': '',
          'zh': '释义$i',
          'mode': 'spell_zh2en',
          'wrong_count': i,
          'last_wrong_at': '2026-10-09T12:00:00Z',
          'unit_id': 1,
          'unit_name': 'Unit A',
          'type': 'word',
        },
    ];
    final fake = adapter(rows: rows);
    await tester.pumpWidget(boot(fake));
    await tester.pumpAndSettle();

    // 25 条全部到齐、只出现一次（无重复无遗漏）
    expect(find.text('Unit A · 25 题'), findsOneWidget);
    for (var i = 1; i <= 25; i++) {
      expect(find.text('word$i'), findsOneWidget);
    }

    // 恰好翻了两页，不多不少
    final wrongbookCalls = fake.received
        .where((r) => r.uri.path == '/api/wrongbook')
        .map((r) => r.uri.queryParameters['page'])
        .toList();
    expect(wrongbookCalls, ['1', '2']);
  });

  testWidgets('U3 手动移出：确认弹窗 → DELETE → 列表消失', (tester) async {
    useTallScreen(tester);
    final fake = adapter(rows: sampleRows());
    await tester.pumpWidget(boot(fake));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('移出错题本').first);
    await tester.pumpAndSettle();

    // 确认弹窗出现
    expect(find.text('移出错题本'), findsOneWidget);
    expect(find.textContaining('apple'), findsWidgets);
    await tester.tap(find.text('先留着'));
    await tester.pumpAndSettle();
    // 取消：还在列表里，没有发 DELETE
    expect(find.text('apple'), findsOneWidget);
    expect(fake.received.any((r) => r.uri.path.startsWith('/api/wrongbook/101')),
        isFalse);

    // 再点 → 确认移出
    await tester.tap(find.byTooltip('移出错题本').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('移出'));
    await tester.pumpAndSettle();

    // DELETE /api/wrongbook/{item_id}/{mode}（契约 §4.1）
    final deletePaths = fake.received
        .where((r) => r.method == 'DELETE')
        .map((r) => r.uri.path)
        .toList();
    expect(deletePaths, ['/api/wrongbook/101/spell_zh2en']);
    // 列表里消失
    expect(find.text('apple'), findsNothing);
    expect(find.text('Starter Unit 1 · 1 题'), findsOneWidget);
  });

  testWidgets('U4a 重做分发：只有 listen 一组时直达听写设置页', (tester) async {
    useTallScreen(tester);
    final rows = sampleRows().where((r) => r['mode'] == 'listen').toList();
    final fake = adapter(rows: rows);
    await tester.pumpWidget(boot(fake));
    await tester.pumpAndSettle();

    await tester.tap(find.textContaining('重做全部'));
    await tester.pumpAndSettle();

    // 进了听写设置页（WP-D 页面），单元名与错题题单透传
    expect(find.byType(DictationSettingsPage), findsOneWidget);
    expect(find.text('听写 · Starter Unit 1'), findsOneWidget);
    // listen 组不组卷：没发 practice/start
    expect(fake.received.any((r) => r.uri.path == '/api/practice/start'), isFalse);
  });

  testWidgets('U4b 重做分发：多组先选组，spell 组走 practice/start 进答题页',
      (tester) async {
    useTallScreen(tester);
    // 只留两条 spell 错题（不同单元 → 两个组）
    final rows = sampleRows()
        .where((r) => r['mode'] != 'listen')
        .toList();
    final fake = adapter(rows: rows);
    await tester.pumpWidget(boot(fake));
    await tester.pumpAndSettle();

    await tester.tap(find.textContaining('重做全部'));
    await tester.pumpAndSettle();

    // 选组面板：两组都列出
    expect(find.text('这些错题分了几种练法，先重做哪一组？'), findsOneWidget);
    expect(find.text('看中文背英文（1 题）'), findsOneWidget);
    expect(find.text('看英文背中文（1 题）'), findsOneWidget);

    await tester.tap(find.text('看中文背英文（1 题）'));
    await tester.pumpAndSettle();

    // start 请求体 {unit_id,type,mode} 与契约一致，且 mode=spell_zh2en
    final startCalls = fake.received
        .where((r) => r.uri.path == '/api/practice/start')
        .toList();
    expect(startCalls, hasLength(1));
    expect(startCalls.single.uri.path, '/api/practice/start');

    // 进了答题页，且只重做该组的错题（苹果在，高兴的不在）
    expect(find.byType(SpellPage), findsOneWidget);
    expect(find.text('苹果'), findsOneWidget);
    expect(find.text('高兴的'), findsNothing);
  });

  test('groupForRedo：listen 合并一组，spell 按 (mode,unit,type) 细分', () {
    final items = [
      for (final row in sampleRows()) WrongItem.fromJson(row),
      // 同单元同 type 的第二条 spell_en2zh：应与 happy 并进同一组
      const WrongItem(
        itemId: 104,
        en: 'sad',
        pos: 'adj.',
        zh: '难过的',
        mode: 'spell_en2zh',
        wrongCount: 1,
        lastWrongAt: '2026-10-07T12:00:00Z',
        unitId: 2,
        unitName: 'Unit 1 Hello!',
        type: 'word',
      ),
    ];

    final groups = groupForRedo(items);
    expect(groups, hasLength(3));

    expect(groups[0].mode, 'listen');
    expect(groups[0].count, 1);
    expect(groups[0].unitName, 'Starter Unit 1');

    expect(groups[1].mode, 'spell_zh2en');
    expect(groups[1].unitId, 1);
    expect(groups[1].type, 'word');

    expect(groups[2].mode, 'spell_en2zh');
    expect(groups[2].count, 2);
    expect(groups[2].items.map((e) => e.itemId), [103, 104]);
  });
}
