/// APP-13 错题本改造验收 —— U17-4 / U17-5 / U17-6（52-二轮学生端工作包规格）。
///
/// - U17-4 三类 tab 切换：请求 mode 参数正确、列表各自独立；
/// - U17-5 已掌握：mastered_at 非空显示「已掌握」标记；切 status=mastered 只见已掌握；
/// - U17-6 批量删除：勾选 3 条 → POST /api/wrongbook/batch-resolve →
///   resolved 计数=3、列表刷新；单条长按删除保留；
/// - mode/status 过滤在假 adapter 里真实生效（走真 ApiClient 解包路径）。
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
import 'package:student_app/features/wrongbook/wrongbook_page.dart';

import 'support/fake_home_repository.dart';

/// 样例错题：三类 mode 齐全，happy 已掌握（mastered_at 非空）。
List<Map<String, dynamic>> seedRows() => [
      {
        'item_id': 101,
        'en': 'apple',
        'pos': 'n.',
        'zh': '苹果',
        'mode': 'spell_zh2en',
        'wrong_count': 2,
        'last_wrong_at': '2026-10-14T12:00:00Z',
        'unit_id': 1,
        'unit_name': 'Starter Unit 1',
        'type': 'word',
        'mastered_at': '',
      },
      {
        'item_id': 102,
        'en': 'cat',
        'pos': 'n.',
        'zh': '猫',
        'mode': 'spell_zh2en',
        'wrong_count': 1,
        'last_wrong_at': '2026-10-14T12:00:00Z',
        'unit_id': 1,
        'unit_name': 'Starter Unit 1',
        'type': 'word',
        'mastered_at': '',
      },
      {
        'item_id': 103,
        'en': 'dog',
        'pos': 'n.',
        'zh': '狗',
        'mode': 'spell_zh2en',
        'wrong_count': 1,
        'last_wrong_at': '2026-10-14T12:00:00Z',
        'unit_id': 1,
        'unit_name': 'Starter Unit 1',
        'type': 'word',
        'mastered_at': '',
      },
      {
        'item_id': 104,
        'en': 'go shopping',
        'pos': '',
        'zh': '去购物',
        'mode': 'listen',
        'wrong_count': 1,
        'last_wrong_at': '2026-10-13T02:00:00Z',
        'unit_id': 1,
        'unit_name': 'Starter Unit 1',
        'type': 'phrase',
        'mastered_at': '',
      },
      {
        'item_id': 105,
        'en': 'happy',
        'pos': 'adj.',
        'zh': '高兴的',
        'mode': 'spell_en2zh',
        'wrong_count': 3,
        'last_wrong_at': '2026-10-12T12:00:00Z',
        'unit_id': 2,
        'unit_name': 'Unit 1 Hello!',
        'type': 'word',
        'mastered_at': '2026-10-15T01:00:00Z',
      },
      {
        'item_id': 106,
        'en': 'sad',
        'pos': 'adj.',
        'zh': '难过的',
        'mode': 'spell_en2zh',
        'wrong_count': 1,
        'last_wrong_at': '2026-10-12T12:00:00Z',
        'unit_id': 2,
        'unit_name': 'Unit 1 Hello!',
        'type': 'word',
        'mastered_at': '',
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

  /// 可变的错题库（batch-resolve 会真的从中移除，验证列表刷新）。
  ///
  /// mode/status 过滤**真实生效**（契约 §4.1）：
  /// - mode：listen | spell_zh2en | spell_en2zh；
  /// - status：unmastered（默认，mastered_at 空）| mastered | all。
  List<Map<String, dynamic>> makeStore() => seedRows();

  JsonAdapter adapter(List<Map<String, dynamic>> store) {
    return JsonAdapter((options) {
      final path = options.uri.path;
      if (path == '/api/wrongbook') {
        final mode = options.uri.queryParameters['mode'] ?? '';
        final status = options.uri.queryParameters['status'] ?? 'unmastered';
        final page = int.parse(options.uri.queryParameters['page'] ?? '1');
        final size =
            int.parse(options.uri.queryParameters['page_size'] ?? '20');
        final filtered = store.where((row) {
          if (row['mode'] != mode) {
            return false;
          }
          final mastered = (row['mastered_at'] as String? ?? '').isNotEmpty;
          return switch (status) {
            'mastered' => mastered,
            'all' => true,
            _ => !mastered,
          };
        }).toList();
        final slice = filtered.skip((page - 1) * size).take(size).toList();
        return okBody({
          'items': slice,
          'total': filtered.length,
          'page': page,
          'page_size': size,
        });
      }
      if (path == '/api/wrongbook/batch-resolve') {
        final raw = options.data;
        final body = raw is String
            ? jsonDecode(raw) as Map<String, dynamic>
            : Map<String, dynamic>.from(raw as Map);
        final entries = (body['entries'] as List)
            .map((e) => Map<String, dynamic>.from(e as Map))
            .toList();
        var resolved = 0;
        for (final entry in entries) {
          final before = store.length;
          store.removeWhere((row) =>
              row['item_id'] == entry['item_id'] &&
              row['mode'] == entry['mode']);
          if (store.length < before) {
            resolved++;
          }
        }
        return okBody({'resolved': resolved});
      }
      if (path.startsWith('/api/wrongbook/')) {
        // DELETE /api/wrongbook/{item_id}/{mode}
        final parts = path.split('/');
        final itemId = int.parse(parts[3]);
        final mode = parts[4];
        final before = store.length;
        store.removeWhere(
            (row) => row['item_id'] == itemId && row['mode'] == mode);
        return okBody({'resolved': before > store.length ? 1 : 0});
      }
      if (path == '/api/practice/start') {
        return okBody(<String, dynamic>{
          'session_id': 'ps-1',
          'items': const <Map<String, dynamic>>[],
        });
      }
      return okBody(null);
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
        routes: Map<String, WidgetBuilder>.of(AppRouter.routes)..remove('/'),
        home: const WrongbookPage(),
      ),
    );
  }

  /// 最近一次 wrongbook 列表请求的 query。
  Map<String, String> lastListQuery(JsonAdapter fake) {
    final calls = fake.received
        .where((r) => r.uri.path == '/api/wrongbook')
        .toList();
    return calls.last.uri.queryParameters;
  }

  testWidgets('U17-4 三类 tab：请求 mode 参数正确、列表各自独立', (tester) async {
    useTallScreen(tester);
    final store = makeStore();
    final fake = adapter(store);
    await tester.pumpWidget(boot(fake));
    await tester.pumpAndSettle();

    // 默认 tab=听写：只看得到 listen 错题
    expect(lastListQuery(fake)['mode'], 'listen');
    expect(lastListQuery(fake)['status'], 'unmastered');
    expect(find.text('go shopping'), findsOneWidget);
    expect(find.text('apple'), findsNothing);
    expect(find.text('Starter Unit 1 · 1 题'), findsOneWidget);

    // 切「看中背英」：mode=spell_zh2en，列表独立
    await tester.tap(find.text('看中背英'));
    await tester.pumpAndSettle();
    expect(lastListQuery(fake)['mode'], 'spell_zh2en');
    expect(find.text('apple'), findsOneWidget);
    expect(find.text('go shopping'), findsNothing);
    expect(find.text('Starter Unit 1 · 3 题'), findsOneWidget);

    // 切「看英背中」：mode=spell_en2zh（默认 unmastered：已掌握的 happy 被滤掉）
    await tester.tap(find.text('看英背中'));
    await tester.pumpAndSettle();
    expect(lastListQuery(fake)['mode'], 'spell_en2zh');
    expect(find.text('sad'), findsOneWidget);
    expect(find.text('happy'), findsNothing);
    expect(find.text('Unit 1 Hello! · 1 题'), findsOneWidget);

    // 三次列表请求 mode 全部正确
    final modes = fake.received
        .where((r) => r.uri.path == '/api/wrongbook')
        .map((r) => r.uri.queryParameters['mode'])
        .toList();
    expect(modes, ['listen', 'spell_zh2en', 'spell_en2zh']);
  });

  testWidgets('U17-5 已掌握：mastered_at 非空显示标记；切 mastered 只见已掌握',
      (tester) async {
    useTallScreen(tester);
    final store = makeStore();
    final fake = adapter(store);
    await tester.pumpWidget(boot(fake));
    await tester.pumpAndSettle();

    // 默认 unmastered：看英背中 tab 里 happy（已掌握）被过滤，只见 sad
    await tester.tap(find.text('看英背中'));
    await tester.pumpAndSettle();
    expect(find.text('sad'), findsOneWidget);
    expect(find.text('happy'), findsNothing);

    // 切「已掌握」：只见 happy，且带「已掌握」标记（徽标 + 筛选段各一处）
    await tester.tap(find.text('已掌握'));
    await tester.pumpAndSettle();
    expect(lastListQuery(fake)['status'], 'mastered');
    expect(find.text('happy'), findsOneWidget);
    expect(find.text('sad'), findsNothing);
    expect(find.text('已掌握'), findsNWidgets(2),
        reason: '「已掌握」小标记 + 筛选器段标签');

    // 切「全部」：sad 与 happy 都在，happy 仍带标记
    await tester.tap(find.text('全部'));
    await tester.pumpAndSettle();
    expect(lastListQuery(fake)['status'], 'all');
    expect(find.text('sad'), findsOneWidget);
    expect(find.text('happy'), findsOneWidget);
    expect(find.text('已掌握'), findsNWidgets(2));
  });

  testWidgets('U17-6 批量删除：勾选 3 条 → batch-resolve → resolved=3、列表刷新',
      (tester) async {
    useTallScreen(tester);
    final store = makeStore();
    final fake = adapter(store);
    await tester.pumpWidget(boot(fake));
    await tester.pumpAndSettle();

    // 看中背英 tab 有 3 条（apple / cat / dog）
    await tester.tap(find.text('看中背英'));
    await tester.pumpAndSettle();
    expect(find.text('apple'), findsOneWidget);
    expect(find.text('cat'), findsOneWidget);
    expect(find.text('dog'), findsOneWidget);

    // 进多选模式，勾选 3 条
    await tester.tap(find.text('批量删除'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('apple'));
    await tester.tap(find.text('cat'));
    await tester.tap(find.text('dog'));
    await tester.pumpAndSettle();
    expect(find.text('移出所选（3）'), findsOneWidget);

    // 确认 → POST /api/wrongbook/batch-resolve
    await tester.tap(find.text('移出所选（3）'));
    await tester.pumpAndSettle();
    expect(find.text('把选中的 3 条错题一次移出？移出后它们不会再出现在这里。'),
        findsOneWidget);
    await tester.tap(find.text('移出'));
    await tester.pumpAndSettle();

    final batchCalls = fake.received
        .where((r) => r.uri.path == '/api/wrongbook/batch-resolve')
        .toList();
    expect(batchCalls, hasLength(1));
    final raw = batchCalls.single.data;
    final body = raw is String
        ? jsonDecode(raw) as Map<String, dynamic>
        : Map<String, dynamic>.from(raw as Map);
    final entries = (body['entries'] as List)
        .map((e) => Map<String, dynamic>.from(e as Map))
        .toList();
    expect(entries, hasLength(3));
    expect(
      entries.map((e) => '${e['item_id']}:${e['mode']}').toSet(),
      {'101:spell_zh2en', '102:spell_zh2en', '103:spell_zh2en'},
    );

    // resolved 计数上屏 + 列表刷新（该 tab 清空 → 空态）
    expect(find.text('已移出 3 条错题'), findsOneWidget);
    expect(find.text('这个分类还没有错题'), findsOneWidget);
    expect(find.text('apple'), findsNothing);

    // 其他 tab 不受影响：听写 tab 的错题仍在
    await tester.tap(find.text('听写'));
    await tester.pumpAndSettle();
    expect(find.text('go shopping'), findsOneWidget);
  });

  testWidgets('U17-6b 单条长按删除保留：确认弹窗 → DELETE → 列表消失', (tester) async {
    useTallScreen(tester);
    final store = makeStore();
    final fake = adapter(store);
    await tester.pumpWidget(boot(fake));
    await tester.pumpAndSettle();

    // 默认听写 tab：长按 go shopping 卡片
    await tester.longPress(find.text('go shopping'));
    await tester.pumpAndSettle();
    expect(find.text('移出错题本'), findsOneWidget);
    await tester.tap(find.text('移出'));
    await tester.pumpAndSettle();

    final deletePaths = fake.received
        .where((r) => r.method == 'DELETE')
        .map((r) => r.uri.path)
        .toList();
    expect(deletePaths, ['/api/wrongbook/104/listen']);
    expect(find.text('go shopping'), findsNothing);
  });
}
