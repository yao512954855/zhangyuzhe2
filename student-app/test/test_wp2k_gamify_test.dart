/// WP-2K 游戏化激励页 —— `GamifyPage`（契约 §15.2）。
///
/// 钉住四件容易假绿的事：
/// 1. **请求打到正确的 path 与方法**：`GET /api/me/gamify`，且 method=GET。
/// 2. **契约字段原样渲染**：`points` / `streak_days` / 徽章 `name` 与
///    `earned` 状态（已点亮 / 未解锁）都出现在界面上，徽章墙展示全部目录项。
/// 3. **空态不编造**：后端回 `points=0`、空徽章列表 → 明确提示，不报错也不假装。
/// 4. **错误态可重试**：后端回业务失败码 → 显示中文错误，`onRetry` 能重新拉取。
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/features/mine/gamify_page.dart';

import 'test_app05_helpers.dart';

/// 构造 §15.2 的契约响应体 `{points, streak_days, badges:[...]}`。
Map<String, dynamic> gamifyJson({
  int points = 0,
  int streakDays = 0,
  required List<Map<String, dynamic>> badges,
}) =>
    {
      'points': points,
      'streak_days': streakDays,
      'badges': badges,
    };

/// 构造一枚徽章（契约 `{code, name, earned, earned_at?}`）。
Map<String, dynamic> badgeJson({
  required String code,
  required String name,
  required bool earned,
  String? earnedAt,
}) =>
    {
      'code': code,
      'name': name,
      'earned': earned,
      if (earnedAt != null) 'earned_at': earnedAt,
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    (local, serverStore) = await openTestStore();
  });

  /// 只认 gamify 端点的 adapter（其余 path 回默认空体，避免误命中）。
  JsonAdapter gamifyAdapter({
    required Map<String, dynamic> body,
    Map<String, dynamic>? other,
    ApiException? failWith,
    int failTimes = 0,
  }) {
    var calls = 0;
    return JsonAdapter((options) {
      final path = options.uri.path;
      final method = options.method.toUpperCase();
      if (path == '/api/me/gamify' && method == 'GET') {
        calls++;
        if (calls <= failTimes && failWith != null) {
          return '{"code":${failWith.code},"message":"${failWith.message}",'
              '"data":null,"trace_id":"t"}';
        }
        return okBody(body);
      }
      return okBody(other ?? const {});
    });
  }

  Future<void> boot(WidgetTester tester, JsonAdapter adapter) async {
    useTallScreen(tester);
    await tester.pumpWidget(
      bootApp(
        const GamifyPage(),
        local: local,
        serverStore: serverStore,
        adapter: adapter,
      ),
    );
    await tester.pumpAndSettle();
  }

  // -----------------------------------------------------------------------
  // 核心：渲染契约字段
  // -----------------------------------------------------------------------

  group('成就页渲染（§15.2）', () {
    testWidgets('points/streak_days 与徽章 name/earned 都上屏', (tester) async {
      final badges = [
        badgeJson(code: 'first_practice', name: '初次练习', earned: true, earnedAt: '2026-09-24T09:00:00Z'),
        badgeJson(code: 'ten_mastered', name: '小有成就', earned: false),
        badgeJson(code: 'streak_7', name: '持之以恒', earned: true, earnedAt: '2026-09-25T09:00:00Z'),
      ];
      final adapter = gamifyAdapter(
        body: gamifyJson(points: 285, streakDays: 3, badges: badges),
      );
      await boot(tester, adapter);

      // 请求确实打到了正确端点、方法、path
      final calls = adapter.received
          .where((r) => r.uri.path == '/api/me/gamify' && r.method == 'GET');
      expect(calls, hasLength(1));

      // 顶部 AppBar 标题
      expect(find.text('我的成就'), findsOneWidget);

      // 积分与连续天数（契约字段原样呈现）
      expect(find.text('285'), findsWidgets);
      expect(find.text('3'), findsWidgets);
      expect(find.text('积分'), findsOneWidget);
      expect(find.text('连续练习(天)'), findsOneWidget);

      // 徽章 name 全部展示（已点亮 + 未解锁都在墙上）
      expect(find.text('初次练习'), findsOneWidget);
      expect(find.text('小有成就'), findsOneWidget);
      expect(find.text('持之以恒'), findsOneWidget);

      // earned 状态：已点亮 vs 未解锁
      expect(find.text('已点亮'), findsNWidgets(2));
      expect(find.text('未解锁'), findsOneWidget);
    });

    testWidgets('多枚未解锁：数量与状态都对得上', (tester) async {
      final badges = [
        badgeJson(code: 'first_practice', name: '初次练习', earned: false),
        badgeJson(code: 'ten_mastered', name: '小有成就', earned: false),
        badgeJson(code: 'streak_7', name: '持之以恒', earned: false),
      ];
      final adapter = gamifyAdapter(
        body: gamifyJson(points: 0, streakDays: 0, badges: badges),
      );
      await boot(tester, adapter);

      expect(find.text('未解锁'), findsNWidgets(3));
      expect(find.text('已点亮'), findsNothing);
    });
  });

  // -----------------------------------------------------------------------
  // 空态 / 错误态
  // -----------------------------------------------------------------------

  group('空态与错误态', () {
    testWidgets('积分 0、空徽章列表 → 明确提示，不报错也不编造', (tester) async {
      final adapter = gamifyAdapter(
        body: gamifyJson(points: 0, streakDays: 0, badges: const []),
      );
      await boot(tester, adapter);

      // 积分 0 如实显示（不编造正数）
      expect(find.text('0'), findsWidgets);
      expect(find.text('还没有徽章，去练习赢取第一枚吧！'), findsOneWidget);
      expect(find.text('已点亮'), findsNothing);
    });

    testWidgets('后端回业务失败码 → 中文错误；点重试后拉到数据', (tester) async {
      final badges = [
        badgeJson(code: 'first_practice', name: '初次练习', earned: true, earnedAt: '2026-09-24T09:00:00Z'),
      ];
      final adapter = gamifyAdapter(
        body: gamifyJson(points: 10, streakDays: 1, badges: badges),
        failWith: ApiException.business(10002, '登录已过期，请重新登录'),
        failTimes: 1,
      );
      await boot(tester, adapter);

      // 首屏：错误态（displayText 含文案）；ErrorView 必有重试按钮
      expect(find.text('再试一次'), findsOneWidget);
      expect(find.textContaining('登录已过期'), findsOneWidget);
      expect(find.text('初次练习'), findsNothing);

      final callsBeforeRetry = adapter.received
          .where((r) => r.uri.path == '/api/me/gamify')
          .length;
      expect(callsBeforeRetry, greaterThanOrEqualTo(1));

      // 点重试（ErrorView 的 onRetry，按钮文案默认「再试一次」）
      await tester.tap(find.text('再试一次'));
      await tester.pumpAndSettle();

      // 重试成功：徽章出现
      expect(find.text('初次练习'), findsOneWidget);
      expect(find.text('已点亮'), findsOneWidget);

      final callsAfterRetry = adapter.received
          .where((r) => r.uri.path == '/api/me/gamify')
          .length;
      expect(callsAfterRetry, greaterThan(callsBeforeRetry));
    });

    testWidgets('其它 path 不被误命中', (tester) async {
      final adapter = gamifyAdapter(
        body: gamifyJson(points: 0, streakDays: 0, badges: const []),
      );
      await boot(tester, adapter);

      final hitGamify = adapter.received
          .any((r) => r.uri.path == '/api/me/gamify');
      final hitOthers = adapter.received
          .any((r) => r.uri.path != '/api/me/gamify');
      expect(hitGamify, isTrue);
      expect(hitOthers, isFalse);
    });
  });
}
