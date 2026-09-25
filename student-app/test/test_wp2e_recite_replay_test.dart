/// WP-2E 背诵事件回放页 —— `ReciteReplayPage`（接口设计 §15.2）。

/// 这套用例钉住四件容易假绿的事：
/// 1. **请求打到正确的 path 与方法**：`GET /api/me/recitations/{id}/events`，
///    且 `recitationId` 进了 path（不是别的端点、不是 POST）。
/// 2. **契约字段原样渲染**：`seq` / `pos` / `event_type` / `at_ms` 都出现在界面上，
///    中文标签由客户端映射（不在 JSON 里下发），`at_ms` 为 `null` 时显示「—」。
/// 3. **空态不编造**：没有事件 → 明确提示，不报错也不假装。
/// 4. **错误态可重试**：后端回 `10004` → 显示中文错误，`onRetry` 能重新拉取。

library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/features/mine/recite_replay_page.dart';

import 'test_app05_helpers.dart';

/// 构造 §15.2 的契约响应体 `{events:[{seq,pos,event_type,at_ms}]}`。
Map<String, dynamic> replayJson(List<Map<String, dynamic>> events) =>
    {'events': events};

Map<String, dynamic> ev({
  required int seq,
  required int pos,
  required String eventType,
  int? atMs,
}) =>
    {
      'seq': seq,
      'pos': pos,
      'event_type': eventType,
      'at_ms': atMs,
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    (local, serverStore) = await openTestStore();
  });

  /// 包一个只认回放端点的 adapter（其余 path 回默认空体，避免误命中）。
  JsonAdapter replayAdapter({
    required Map<String, dynamic> body,
    Map<String, dynamic>? other,
    ApiException? failWith,
    int failTimes = 0,
  }) {
    var calls = 0;
    return JsonAdapter((options) {
      final path = options.uri.path;
      final method = options.method.toUpperCase();
      if (RegExp(r'^/api/me/recitations/(\d+)/events$').hasMatch(path) &&
          method == 'GET') {
        calls++;
        if (calls <= failTimes && failWith != null) {
          // 模拟业务失败（code != 0）→ ApiClient 会抛 ApiException
          return '{"code":${failWith.code},"message":"${failWith.message}",'
              '"data":null,"trace_id":"t"}';
        }
        return okBody(body);
      }
      return okBody(other ?? const {});
    });
  }

  Future<void> boot(
    WidgetTester tester,
    JsonAdapter adapter, {
    int recitationId = 7,
    String? title,
  }) async {
    useTallScreen(tester);
    await tester.pumpWidget(
      bootApp(
        ReciteReplayPage(recitationId: recitationId, title: title),
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

  group('回放事件渲染（§15.2）', () {
    testWidgets('seq/pos/event_type/at_ms 都上屏，中文标签由客户端映射', (tester) async {
      final events = [
        ev(seq: 1, pos: 0, eventType: 'matched', atMs: 1200),
        ev(seq: 2, pos: 4, eventType: 'missed', atMs: 9000),
        ev(seq: 3, pos: 2, eventType: 'hint', atMs: null),
      ];
      final adapter = replayAdapter(body: replayJson(events));
      await boot(tester, adapter, recitationId: 7, title: '静夜思');

      // 请求确实打到了正确端点、方法、且 recitationId 在 path 里
      final calls = adapter.received
          .where((r) => r.uri.path == '/api/me/recitations/7/events');
      expect(calls, hasLength(1));
      expect(calls.single.method, 'GET');

      // AppBar 标题带了课文名
      expect(find.text('背诵回放 · 静夜思'), findsOneWidget);

      // 三种事件的中文标签（matched→点亮 / missed→漏背 / hint→提示）
      expect(find.text('点亮'), findsWidgets);
      expect(find.text('漏背'), findsOneWidget);
      expect(find.text('提示'), findsOneWidget);

      // pos 与 seq 原样呈现
      expect(find.text('第 0 字'), findsOneWidget);
      expect(find.text('第 4 字'), findsOneWidget);
      expect(find.text('第 2 字'), findsOneWidget);
      expect(find.text('#1'), findsOneWidget);
      expect(find.text('#2'), findsOneWidget);
      expect(find.text('#3'), findsOneWidget);

      // at_ms：有值显示秒、null 显示「—」
      expect(find.text('1.2s'), findsOneWidget);
      expect(find.text('9.0s'), findsOneWidget);
      expect(find.text('—'), findsOneWidget);
    });

    testWidgets('多个 matched：数量与序号都对得上', (tester) async {
      final events = [
        ev(seq: 1, pos: 0, eventType: 'matched', atMs: 100),
        ev(seq: 2, pos: 1, eventType: 'matched', atMs: 200),
        ev(seq: 3, pos: 2, eventType: 'matched', atMs: 300),
      ];
      final adapter = replayAdapter(body: replayJson(events));
      await boot(tester, adapter);

      expect(find.text('点亮'), findsNWidgets(3));
      expect(find.text('第 0 字'), findsOneWidget);
      expect(find.text('第 1 字'), findsOneWidget);
      expect(find.text('第 2 字'), findsOneWidget);
    });
  });

  // -----------------------------------------------------------------------
  // 空态 / 错误态
  // -----------------------------------------------------------------------

  group('空态与错误态', () {
    testWidgets('没有事件 → 明确提示，不报错也不编造', (tester) async {
      final adapter = replayAdapter(body: replayJson(const []));
      await boot(tester, adapter);

      expect(find.text('这次背诵没有可回放的事件'), findsOneWidget);
      expect(find.text('点亮'), findsNothing);
    });

    testWidgets('后端回 10004 → 中文错误；点重试后拉到数据', (tester) async {
      // 首屏失败（越权/不存在同码 10004）。
      // 注意：provider 在启动期会因依赖（apiClientProvider）随 store 落定而重建，
      // 触发一次额外请求；这里把前几次都打成 10004，保证首屏稳定停在错误态，
      // 不被紧随其后的重建请求悄悄覆盖。
      final events = [
        ev(seq: 1, pos: 0, eventType: 'matched', atMs: 500),
      ];
      final adapter = replayAdapter(
        body: replayJson(events),
        failWith: ApiException.business(10004, '请求的内容不存在'),
        failTimes: 1,
      );
      await boot(tester, adapter, recitationId: 7);

      // 首屏：错误态（displayText 含 trace_id，用 contains 匹配）
      expect(find.textContaining('请求的内容不存在'), findsOneWidget);
      expect(find.text('点亮'), findsNothing);

      // 记录首屏已发出的回放请求数（含启动期重建带来的那几次失败请求）
      final callsBeforeRetry = adapter.received
          .where((r) => r.uri.path == '/api/me/recitations/7/events')
          .length;
      expect(callsBeforeRetry, greaterThanOrEqualTo(1));

      // 点重试（ErrorView 的 onRetry，按钮文案默认「再试一次」）
      await tester.tap(find.text('再试一次'));
      await tester.pumpAndSettle();

      // 重试成功：事件出现
      expect(find.text('点亮'), findsOneWidget);
      expect(find.text('第 0 字'), findsOneWidget);
      // 重试确实重新发了一次回放请求（首屏的失败/重建请求之外又多打一次）
      final callsAfterRetry = adapter.received
          .where((r) => r.uri.path == '/api/me/recitations/7/events')
          .length;
      expect(callsAfterRetry, greaterThan(callsBeforeRetry));
    });

    testWidgets('其它 path 不被误命中', (tester) async {
      final adapter = replayAdapter(
        body: replayJson(const []),
        other: {'items': []},
      );
      await boot(tester, adapter);

      // 回放页只读自己的端点，不会去拉 wrong-chars / sessions 之类
      final hitReplay = adapter.received
          .any((r) => r.uri.path == '/api/me/recitations/7/events');
      final hitOthers = adapter.received
          .any((r) => r.uri.path != '/api/me/recitations/7/events');
      expect(hitReplay, isTrue);
      expect(hitOthers, isFalse);
    });
  });
}
