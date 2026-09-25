/// WP-2A 组件测试 —— 个人正确率趋势图（F3 趋势卡 + TrendChart）。
///
/// 覆盖：
/// - 我的统计页在拿到趋势数据时渲染「正确率趋势」卡片与折线图；
/// - 无数据时折线图显示空状态（"最近还没有练习记录"），不画空占位线；
/// - 趋势图组件本身用 [CustomPainter] 自绘（不依赖第三方图表库），能正常构建；
/// - 趋势接口报错时趋势卡片给中文错误而非整页崩溃。
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/widgets/feedback.dart';
import 'package:student_app/features/mine/my_stats_page.dart';
import 'package:student_app/features/mine/trend_chart.dart';

import 'test_app05_helpers.dart';

void main() {
  group('WP-2A 正确率趋势', () {
    testWidgets('我的统计页：有趋势数据时出现「正确率趋势」卡片与折线图', (tester) async {
      useTallScreen(tester);
      final (local, serverStore) = await openTestStore();
      final today =
          DateTime.now().toIso8601String().substring(0, 10);
      final d1 = DateTime.now()
          .subtract(const Duration(days: 1))
          .toIso8601String()
          .substring(0, 10);
      final d2 = DateTime.now()
          .subtract(const Duration(days: 2))
          .toIso8601String()
          .substring(0, 10);

      await tester.pumpWidget(
        bootApp(
          const MyStatsPage(),
          local: local,
          serverStore: serverStore,
          adapter: defaultAdapter(
            accuracyTrend: accuracyTrendJson([
              trendPointJson(date: d2, accuracy: 0.5, answered: 4, correct: 2),
              trendPointJson(date: d1, accuracy: 0.6, answered: 5, correct: 3),
              trendPointJson(date: today, accuracy: 1.0, answered: 4, correct: 4),
            ]),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('正确率趋势'), findsOneWidget);
      // 折线图组件被渲染（CustomPaint 在 TrendChart 内部）。
      expect(find.byType(TrendChart), findsOneWidget);
      // 头部显示最近一天正确率。
      expect(find.text('最近 100%'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('TrendChart 无数据：显示空状态文案，不抛异常', (tester) async {
      useTallScreen(tester);
      final (local, serverStore) = await openTestStore();
      await tester.pumpWidget(
        bootApp(
          Container(child: const TrendChart(points: [])),
          local: local,
          serverStore: serverStore,
          adapter: defaultAdapter(),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('最近还没有练习记录'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('TrendChart 有数据点：能正常构建折线（不崩溃）', (tester) async {
      useTallScreen(tester);
      final (local, serverStore) = await openTestStore();
      final points = [
        TrendPoint(date: '2026-09-23', accuracy: 0.5, answered: 4, correct: 2),
        TrendPoint(date: '2026-09-24', accuracy: 0.8, answered: 5, correct: 4),
        TrendPoint(date: '2026-09-25', accuracy: 1.0, answered: 4, correct: 4),
      ];
      await tester.pumpWidget(
        bootApp(
          Container(child: TrendChart(points: points)),
          local: local,
          serverStore: serverStore,
          adapter: defaultAdapter(),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(TrendChart), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('趋势接口报错：趋势卡片给中文错误而非整页崩溃', (tester) async {
      useTallScreen(tester);
      final (local, serverStore) = await openTestStore();
      await tester.pumpWidget(
        bootApp(
          const MyStatsPage(),
          local: local,
          serverStore: serverStore,
          adapter: JsonAdapter((options) {
            if (options.uri.path == '/api/me/accuracy-trend') {
              return '{"code":10001,"message":"登录已过期","data":null,"trace_id":"t"}';
            }
            return okBody(const {});
          }),
        ),
      );
      await tester.pumpAndSettle();
      // 进度块仍然正常，趋势块单独报错。
      expect(find.text('总体正确率'), findsOneWidget);
      expect(find.byType(ErrorView), findsWidgets);
      expect(find.textContaining('登录已过期'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });
}
