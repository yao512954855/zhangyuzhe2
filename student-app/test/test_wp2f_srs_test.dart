/// WP-2F 间隔重复复习页 —— `SrsPage` 组件测试。
///
/// 钉住容易假绿的事：
/// 1. **请求打到正确端点与方法**：列表 = `GET /api/me/srs/due`，复习 = `POST /api/me/srs/review`。
/// 2. **空列表如实提示**：后端回空 items → 显示「今天没有要复习的」，不编造。
/// 3. **复习真的回传 grade**：点「记得」(grade=5) → POST body 含 `word_id` / `grade`，
///    成功后列表重新拉取（SM-2 推进由后端做，客户端不重算）。
/// 4. **错误态可重试**：后端回业务失败码 → 中文错误；点重试后拉到数据。

library;

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/features/srs/srs_page.dart';

import 'test_app05_helpers.dart';

/// 构造一条到期复习项（契约 §15.2 字段）。
Map<String, dynamic> srsItemJson({
  required int wordId,
  required String en,
  required String cn,
  String? unitName,
  int intervalDays = 1,
  double ease = 2.5,
  int reps = 0,
  int lapses = 0,
  String dueAt = '2026-09-25T09:00:00Z',
}) =>
    {
      'word_id': wordId,
      'en': en,
      'cn': cn,
      if (unitName != null) 'unit_name': unitName,
      'interval_days': intervalDays,
      'ease': ease,
      'reps': reps,
      'lapses': lapses,
      'due_at': dueAt,
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    (local, serverStore) = await openTestStore();
  });

  /// 适配 SRS 两个端点的假件。
  JsonAdapter srsAdapter({
    required Map<String, dynamic> dueBody,
    Map<String, dynamic>? reviewBody,
    ApiException? failDueWith,
    int failTimes = 0,
  }) {
    var dueCalls = 0;
    return JsonAdapter((options) {
      final path = options.uri.path;
      final method = options.method.toUpperCase();
      if (path == '/api/me/srs/due' && method == 'GET') {
        dueCalls++;
        if (dueCalls <= failTimes && failDueWith != null) {
          return '{"code":${failDueWith.code},"message":"${failDueWith.message}",'
              '"data":null,"trace_id":"t"}';
        }
        return okBody(dueBody);
      }
      if (path == '/api/me/srs/review' && method == 'POST') {
        return okBody(reviewBody ??
            {
              'next_due': '2026-10-01T00:00:00Z',
              'interval_days': 3,
              'reps': 1,
              'ease': 2.5,
              'lapses': 0,
            });
      }
      return okBody(const {});
    });
  }

  Future<void> boot(WidgetTester tester, JsonAdapter adapter) async {
    useTallScreen(tester);
    await tester.pumpWidget(
      bootApp(
        const SrsPage(),
        local: local,
        serverStore: serverStore,
        adapter: adapter,
      ),
    );
    await tester.pumpAndSettle();
  }

  group('列表渲染', () {
    testWidgets('空列表如实提示「今天没有要复习的」', (tester) async {
      final adapter = srsAdapter(dueBody: {'items': []});
      await boot(tester, adapter);

      expect(find.text('今天没有要复习的'), findsOneWidget);
      // GET 确实打到正确端点
      expect(
        adapter.received.any((r) => r.uri.path == '/api/me/srs/due'),
        isTrue,
      );
    });

    testWidgets('到期词条与复习按钮都渲染', (tester) async {
      final adapter = srsAdapter(
        dueBody: {
          'items': [
            srsItemJson(wordId: 101, en: 'apple', cn: '苹果', unitName: 'Unit 1'),
          ],
        },
      );
      await boot(tester, adapter);

      expect(find.text('apple'), findsWidgets);
      expect(find.text('苹果'), findsWidgets);
      expect(find.text('Unit 1'), findsWidgets);
      expect(find.widgetWithText(ElevatedButton, '记得'), findsWidgets);
      expect(find.widgetWithText(OutlinedButton, '忘了'), findsWidgets);
    });
  });

  group('复习', () {
    testWidgets('点「记得」→ POST 带 word_id/grade，且列表刷新', (tester) async {
      final adapter = srsAdapter(
        dueBody: {
          'items': [srsItemJson(wordId: 101, en: 'apple', cn: '苹果')],
        },
      );
      await boot(tester, adapter);

      final getBefore =
          adapter.received.where((r) => r.uri.path == '/api/me/srs/due').length;

      await tester.tap(find.widgetWithText(ElevatedButton, '记得').first);
      await tester.pumpAndSettle();

      final post = adapter.received.firstWhere(
        (r) => r.uri.path == '/api/me/srs/review' && r.method.toUpperCase() == 'POST',
      );
      final raw = post.data;
      final body = raw is String
          ? (jsonDecode(raw) as Map<String, dynamic>)
          : Map<String, dynamic>.from(raw as Map);
      expect(body['word_id'], 101);
      expect(body['grade'], 5);

      // 复习成功后列表重新拉取
      final getAfter =
          adapter.received.where((r) => r.uri.path == '/api/me/srs/due').length;
      expect(getAfter, greaterThan(getBefore));
    });

    testWidgets('点「忘了」→ POST 带 grade=0', (tester) async {
      final adapter = srsAdapter(
        dueBody: {
          'items': [srsItemJson(wordId: 202, en: 'banana', cn: '香蕉')],
        },
      );
      await boot(tester, adapter);

      await tester.tap(find.widgetWithText(OutlinedButton, '忘了').first);
      await tester.pumpAndSettle();

      final post = adapter.received.firstWhere(
        (r) => r.uri.path == '/api/me/srs/review' && r.method.toUpperCase() == 'POST',
      );
      final raw = post.data;
      final body = raw is String
          ? (jsonDecode(raw) as Map<String, dynamic>)
          : Map<String, dynamic>.from(raw as Map);
      expect(body['word_id'], 202);
      expect(body['grade'], 0);
    });
  });

  group('错误态', () {
    testWidgets('后端失败 → 中文错误；点重试后拉到数据', (tester) async {
      final adapter = srsAdapter(
        dueBody: {
          'items': [srsItemJson(wordId: 101, en: 'apple', cn: '苹果')],
        },
        failDueWith: ApiException.business(10002, '登录已过期，请重新登录'),
        failTimes: 1,
      );
      await boot(tester, adapter);

      expect(find.text('再试一次'), findsOneWidget);
      expect(find.textContaining('登录已过期'), findsOneWidget);

      await tester.tap(find.text('再试一次'));
      await tester.pumpAndSettle();

      expect(find.text('apple'), findsWidgets);
    });
  });
}
