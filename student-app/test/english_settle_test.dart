/// 英语结算页 —— 「再练一次」**真重开**（2026-09-29 姚工拍板）+ 转场保护。
///
/// 覆盖：
///  - 按**原配置**重发组卷请求 → 直进练习页，并把**新场次**交给它
///    （不再只 `popUntil` 回首页）；
///  - 拿不到原配置时按钮**如实降级**（不假装能重开）；
///  - 重开是异步的 → 进行中连点只组卷一次（防重复 push 撞导航锁）；
///  - 组卷失败 → 留在结算页 + 按钮恢复可点（不锁死）；
///  - `SettlePage.practiceRoute` 与 `PracticeRoutes.practice` 必须一致
///    （结算页为避开循环依赖不 import 路由文件，靠这条断言钉住）。
///
/// 注：控制器是 `autoDispose`，跳转后会被回收、由练习页 `initState` 重新
/// `start()` —— 所以这里断言的是「**新场次真的交给了练习页**」，
/// 而不是回读 provider 状态（那样读到的是刚新建的空实例）。
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
import 'package:student_app/features/config/practice_session.dart';
import 'package:student_app/features/english/practice_controller.dart';
import 'package:student_app/features/english/settle_page.dart';
import 'package:student_app/router/routes/practice.dart';

import 'support/fake_home_repository.dart';

PracticeSettle sampleSettle() => PracticeSettle(
      sessionId: 99,
      totalItems: 5,
      correctItems: 4,
      accuracy: 0.8,
      score: 88,
      hintCount: 1,
      avgDurationMs: 1200,
      durationMs: 6000,
      wrongItems: const <WrongItem>[],
      improved: true,
    );

/// 组卷请求体（**原配置**）—— 重开时必须原样重发，不能重新拼。
Map<String, dynamic> sampleRequest() => <String, dynamic>{
      'unit_id': 11,
      'content_type': 'english',
      'scope': 'unit',
      'count_mode': 'all',
      'mode': 'cn2en',
    };

String sessionBody({int sessionId = 123}) => okBody({
      'session_id': sessionId,
      'unit_id': 11,
      'unit_name': 'Unit 1 Hello!',
      'content_type': 'english',
      'items': [
        {'seq': 1, 'word_id': 101, 'mode': 'cn2en', 'prompt': '苹果', 'target_len': 5},
        {'seq': 2, 'word_id': 102, 'mode': 'cn2en', 'prompt': '香蕉', 'target_len': 6},
      ],
      'time_limit_ms': 0,
    });

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

  /// 结算页 + 一个桩练习页（记录它收到的场次 id，证明「新场次交给了它」）。
  Widget boot({
    required JsonAdapter adapter,
    SettleArguments? args,
    List<int>? pushedSessions,
  }) {
    return ProviderScope(
      overrides: [
        localStoreProvider.overrideWithValue(local),
        apiClientProvider.overrideWithValue(
          clientWith(adapter, serverStore: serverStore, localStore: local),
        ),
      ],
      child: MaterialApp(
        theme: AppTheme.build(palette: AppPalettes.day),
        routes: {
          SettlePage.practiceRoute: (context) {
            final a = ModalRoute.of(context)?.settings.arguments;
            pushedSessions?.add(a is PracticeSession ? a.sessionId : -1);
            return const Scaffold(body: Text('练习页'));
          },
        },
        home: AgeScope(
          profile: AgeProfile.of(AgeBand.junior),
          child: SettlePage(
            args: args ?? SettleArguments(settle: sampleSettle()),
          ),
        ),
      ),
    );
  }

  test('结算页的练习页路由常量与路由表一致', () {
    expect(SettlePage.practiceRoute, PracticeRoutes.practice);
  });

  testWidgets('「再练一次」按原配置重开，直进练习页', (tester) async {
    useTallScreen(tester);
    final adapter = JsonAdapter((_) => sessionBody());
    final pushed = <int>[];
    await tester.pumpWidget(boot(
      adapter: adapter,
      args: SettleArguments(settle: sampleSettle(), replayRequest: sampleRequest()),
      pushedSessions: pushed,
    ));
    await tester.pumpAndSettle();

    expect(find.text('再练一次'), findsOneWidget);
    await tester.tap(find.text('再练一次'));
    await tester.pumpAndSettle();

    final posts = adapter.received
        .where((o) => o.uri.path.endsWith('/api/sessions'))
        .toList();
    expect(posts, hasLength(1), reason: '重开必须重新组卷');
    expect(posts.single.data, sampleRequest(),
        reason: '请求体必须是原配置原样重发');
    expect(pushed, [123],
        reason: '真重开 = 直进练习页，并把**新场次**交给它（不回首页）');
  });

  testWidgets('重开交出去的是新场次，不是上一场', (tester) async {
    useTallScreen(tester);
    // 结算页的场次是 99，重开组出来的是 777 —— 练习页必须收到 777。
    final adapter = JsonAdapter((_) => sessionBody(sessionId: 777));
    final pushed = <int>[];
    await tester.pumpWidget(boot(
      adapter: adapter,
      args: SettleArguments(settle: sampleSettle(), replayRequest: sampleRequest()),
      pushedSessions: pushed,
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.text('再练一次'));
    await tester.pumpAndSettle();

    expect(pushed, [777], reason: '不能把上一场的场次再交一遍');
  });

  testWidgets('拿不到原配置 → 不显示「再练一次」，只给回首页', (tester) async {
    useTallScreen(tester);
    final adapter = JsonAdapter((_) => okBody(null));
    await tester.pumpWidget(boot(adapter: adapter));
    await tester.pumpAndSettle();

    expect(find.text('再练一次'), findsNothing,
        reason: '没有原配置就不该假装能重开');
    expect(find.text('回到首页'), findsOneWidget);
  });

  testWidgets('重开进行中连点 → 只组卷一次', (tester) async {
    useTallScreen(tester);
    var calls = 0;
    final adapter = JsonAdapter((_) {
      calls += 1;
      return sessionBody();
    });
    await tester.pumpWidget(boot(
      adapter: adapter,
      args: SettleArguments(settle: sampleSettle(), replayRequest: sampleRequest()),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.text('再练一次'));
    // 不 settle，紧接着再点一次（此时按钮应已进入 loading/禁用）。
    await tester.tap(find.text('再练一次'), warnIfMissed: false);
    await tester.pumpAndSettle();

    expect(calls, 1, reason: '连点不能重复组卷');
  });

  testWidgets('重开失败（业务码）→ 留在结算页且按钮恢复可点', (tester) async {
    useTallScreen(tester);
    final adapter = JsonAdapter((_) => jsonEncode({
          'code': 10001,
          'message': '参数不对',
          'data': null,
          'trace_id': 'test-trace',
        }));
    final pushed = <int>[];
    await tester.pumpWidget(boot(
      adapter: adapter,
      args: SettleArguments(settle: sampleSettle(), replayRequest: sampleRequest()),
      pushedSessions: pushed,
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.text('再练一次'));
    await tester.pumpAndSettle();

    expect(pushed, isEmpty, reason: '组卷失败不进练习页');
    expect(find.text('再练一次'), findsOneWidget, reason: '按钮不能锁死');
  });

  /// 2026-10-01 真机 bug 回归：结算页「回到首页」点了没反应
  /// （旧 `_routeAnimating` 闸在 pushReplacement 路由上卡死 → 静默 return）。
  ///
  /// 复刻真实栈形 [首页, 结算页]：桩首页带一个可点按钮，点它 push 结算页；
  /// 结算页点「回到首页」→ 必须真的回到桩首页，且首页按钮**仍可点**。
  testWidgets('「回到首页」真的回到首页，且首页仍可点', (tester) async {
    useTallScreen(tester);
    final adapter = JsonAdapter((_) => okBody(null));
    var homeTaps = 0;
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          localStoreProvider.overrideWithValue(local),
          apiClientProvider.overrideWithValue(
            clientWith(adapter, serverStore: serverStore, localStore: local),
          ),
        ],
        child: MaterialApp(
          theme: AppTheme.build(palette: AppPalettes.day),
          routes: {
            '/settle': (context) => AgeScope(
                  profile: AgeProfile.of(AgeBand.junior),
                  child: SettlePage(args: SettleArguments(settle: sampleSettle())),
                ),
          },
          home: Scaffold(
            body: Builder(
              builder: (context) => Center(
                child: TextButton(
                  onPressed: () {
                    homeTaps += 1;
                    Navigator.of(context).pushNamed('/settle');
                  },
                  child: const Text('HOME_MARK'),
                ),
              ),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('HOME_MARK'));
    await tester.pumpAndSettle();
    expect(find.text('回到首页'), findsOneWidget);

    await tester.tap(find.text('回到首页'));
    await tester.pumpAndSettle();
    expect(find.text('HOME_MARK'), findsOneWidget, reason: '必须真的回到首页');

    await tester.tap(find.text('HOME_MARK'));
    await tester.pumpAndSettle();
    expect(homeTaps, 2, reason: '回到首页后首页必须仍然可点（bug #1）');
  });
}
