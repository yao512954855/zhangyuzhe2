/// WP-APP-05 组件测试 —— 我的统计页（F3 / U4）。
///
/// 覆盖：
/// - U4 / F3：「我的统计」页渲染真实进度（总体正确率 / 已掌握 / 连续 / 累计），
///   数据全部来自 `GET /api/me/progress`。
/// - **反向护栏**：姚工 2026-09-25 裁决「正确率趋势」**一期不做、连占位一起摘掉**
///   （需求 §5.9 / 30-规格 F3·U4 / 趋势图组件已同步摘除）。这里钉住
///   「页面上再也找不到趋势卡片」，防止以后有人照**旧版**规格把它加回来。
///
/// 注：本文件只依赖干净模块 `my_stats_page`
/// （不碰 `MinePage` / `wrong_book_page`，避免被 APP-03 未落地的
/// `practice_page.dart` 编译链阻断 —— 见交付说明 D-APP03）。
library;

import 'package:flutter_test/flutter_test.dart';

import 'test_app05_helpers.dart';
import 'package:student_app/core/widgets/feedback.dart';
import 'package:student_app/features/mine/my_stats_page.dart';

void main() {
  group('U4 / F3 我的统计页', () {
    testWidgets('渲染真实进度：总体正确率 / 已掌握 / 连续 / 累计 都来自 /api/me/progress',
        (tester) async {
      useTallScreen(tester);
      final (local, serverStore) = await openTestStore();
      final prog = progressJson(
        totalWords: 120,
        practicedWords: 80,
        masteredWords: 50,
        streakDays: 7,
        accuracy: 0.85,
      );
      await tester.pumpWidget(
        bootApp(
          const MyStatsPage(),
          local: local,
          serverStore: serverStore,
          adapter: defaultAdapter(progress: prog),
        ),
      );
      await tester.pumpAndSettle();

      // 总体正确率大数字。
      expect(find.text('85%'), findsWidgets);
      expect(find.text('总体正确率'), findsOneWidget);
      // F3 真实数字网格。
      expect(find.text('已掌握词条'), findsOneWidget);
      expect(find.text('50'), findsWidgets); // 已掌握词条 50
      expect(find.text('连续练习(天)'), findsOneWidget);
      expect(find.text('7'), findsWidgets); // 连续 7 天
      expect(find.text('累计练习'), findsOneWidget);
      expect(find.text('80'), findsWidgets); // 累计练习 80
    });

    testWidgets('摘掉趋势图后：页面不再出现「正确率趋势」卡片（反向护栏）', (tester) async {
      useTallScreen(tester);
      final (local, serverStore) = await openTestStore();
      await tester.pumpWidget(
        bootApp(
          const MyStatsPage(),
          local: local,
          serverStore: serverStore,
          adapter: defaultAdapter(),
        ),
      );
      await tester.pumpAndSettle();

      // 一期不做 —— 不留任何占位。有数据时也不能冒出来。
      expect(find.text('正确率趋势'), findsNothing);
      expect(find.textContaining('进步曲线'), findsNothing);
      // 页面本身是好的：真实数字网格仍在。
      expect(find.text('总体正确率'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('进度接口报错时给中文错误而非崩溃', (tester) async {
      useTallScreen(tester);
      final (local, serverStore) = await openTestStore();
      await tester.pumpWidget(
        bootApp(
          const MyStatsPage(),
          local: local,
          serverStore: serverStore,
          // 让 /api/me/progress 返回非零 code 触发 ApiException（生产解包路径）。
          adapter: JsonAdapter((options) {
            if (options.uri.path == '/api/me/progress') {
              return '{"code":10001,"message":"登录已过期","data":null,"trace_id":"t"}';
            }
            return okBody(const {});
          }),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(ErrorView), findsOneWidget);
      expect(find.textContaining('登录已过期'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });
}
