/// WP-2G 我的作业页 —— `HomeworkPage` 组件测试。
///
/// 钉住容易假绿的事：
/// 1. **请求打到正确端点与方法**：列表 = `GET /api/me/homework`。
/// 2. **空列表如实提示**：后端回空 items → 显示「还没有作业」，不编造。
/// 3. **提交状态原样呈现**：pending → 待提交、done → 已完成；学科标签按后端值显示。
/// 4. **错误态可重试**：后端回业务失败码 → 中文错误；点重试后拉到数据。

library;

import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/net/api_exception.dart';
import 'package:student_app/features/homework/homework_page.dart';

import 'test_app05_helpers.dart';

/// 构造一条作业（契约 §15.2：`{id,title,subject,due_at,status}`）。
Map<String, dynamic> hwJson({
  required int id,
  required String title,
  required String subject,
  String? dueAt,
  required String status,
}) =>
    {
      'id': id,
      'title': title,
      'subject': subject,
      if (dueAt != null) 'due_at': dueAt,
      'status': status,
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    (local, serverStore) = await openTestStore();
  });

  JsonAdapter homeworkAdapter({
    required Map<String, dynamic> body,
    ApiException? failWith,
    int failTimes = 0,
  }) {
    var calls = 0;
    return JsonAdapter((options) {
      final path = options.uri.path;
      final method = options.method.toUpperCase();
      if (path == '/api/me/homework' && method == 'GET') {
        calls++;
        if (calls <= failTimes && failWith != null) {
          return '{"code":${failWith.code},"message":"${failWith.message}",'
              '"data":null,"trace_id":"t"}';
        }
        return okBody(body);
      }
      return okBody(const {});
    });
  }

  Future<void> boot(WidgetTester tester, JsonAdapter adapter) async {
    useTallScreen(tester);
    await tester.pumpWidget(
      bootApp(
        const HomeworkPage(),
        local: local,
        serverStore: serverStore,
        adapter: adapter,
      ),
    );
    await tester.pumpAndSettle();
  }

  group('列表渲染', () {
    testWidgets('空列表如实提示「还没有作业」', (tester) async {
      final adapter = homeworkAdapter(body: {'items': []});
      await boot(tester, adapter);

      expect(find.text('还没有作业'), findsOneWidget);
      expect(
        adapter.received.any((r) => r.uri.path == '/api/me/homework'),
        isTrue,
      );
    });

    testWidgets('提交状态与学科原样呈现', (tester) async {
      final adapter = homeworkAdapter(
        body: {
          'items': [
            hwJson(
              id: 1,
              title: '第一单元作业',
              subject: 'english',
              dueAt: '2026-10-01T09:00:00Z',
              status: 'pending',
            ),
            hwJson(
              id: 2,
              title: '古诗背诵',
              subject: 'chinese',
              dueAt: '2026-10-02T09:00:00Z',
              status: 'done',
            ),
          ],
        },
      );
      await boot(tester, adapter);

      expect(find.text('第一单元作业'), findsOneWidget);
      expect(find.text('古诗背诵'), findsOneWidget);
      // 学科标签（后端 subject 原样映射）
      expect(find.text('英语'), findsOneWidget);
      expect(find.text('语文'), findsOneWidget);
      // 提交状态
      expect(find.text('待提交'), findsOneWidget);
      expect(find.text('已完成'), findsOneWidget);
      // 截止时间（取 ISO 前 10 位）
      expect(find.textContaining('截止 2026-10-01'), findsOneWidget);
      expect(find.textContaining('截止 2026-10-02'), findsOneWidget);
    });
  });

  group('错误态', () {
    testWidgets('后端失败 → 中文错误；点重试后拉到数据', (tester) async {
      final adapter = homeworkAdapter(
        body: {
          'items': [
            hwJson(
              id: 1,
              title: '第一单元作业',
              subject: 'english',
              status: 'pending',
            ),
          ],
        },
        failWith: ApiException.business(10002, '登录已过期，请重新登录'),
        failTimes: 1,
      );
      await boot(tester, adapter);

      expect(find.text('再试一次'), findsOneWidget);
      expect(find.textContaining('登录已过期'), findsOneWidget);

      await tester.tap(find.text('再试一次'));
      await tester.pumpAndSettle();

      expect(find.text('第一单元作业'), findsOneWidget);
    });
  });
}
