/// WP-APP-05 组件测试 —— 我的统计页 / 趋势图（F3 / U4 / U10）。
///
/// 覆盖：
/// - U4 / F3：「我的统计」页渲染真实进度（总体正确率 / 已掌握 / 连续 / 累计），
///   并展示「正确率趋势」卡片（U10 的诚实占位）。
/// - U10：趋势图在**空数据 / 单点 / 多点**三种输入下都能静态绘制、绝不抛异常
///   （红线 3：手绘 CustomPainter，无图表库）。
///
/// 注：本文件只依赖干净模块 `my_stats_page` / `widgets/trend_chart`
/// （不碰 `MinePage` / `wrong_book_page`，避免被 APP-03 未落地的
/// `practice_page.dart` 编译链阻断 —— 见交付说明 D-APP03）。
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'test_app05_helpers.dart';
import 'package:student_app/core/widgets/feedback.dart';
import 'package:student_app/features/mine/my_stats_page.dart';
import 'package:student_app/features/mine/widgets/trend_chart.dart';

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

    testWidgets('展示趋势卡片标题（U10：暂无个人历史接口，诚实占位不伪造曲线）',
        (tester) async {
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
      // 趋势卡片始终可见（即使暂无数据）。
      expect(find.text('正确率趋势'), findsOneWidget);
      // MyStatsPage 默认传空列表 → 画布显示占位文案（CustomPainter 绘制，
      // 非 Flutter Text 控件，这里只断言卡片标题存在，不伪造曲线由代码保证）。
      expect(find.byType(TrendChart), findsOneWidget);
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

  group('U10 趋势图鲁棒性（红线3：手绘，无图表库）', () {
    Future<void> pumpChart(WidgetTester tester, List<TrendPoint> points) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.build(palette: AppPalettes.day),
          home: AgeScope(
            profile: AgeProfile.of(AgeBand.junior),
            child: Padding(
              padding: const EdgeInsets.all(8),
              child: TrendChart(
                points: points,
                lineColor: Colors.blue,
                axisColor: Colors.grey,
                labelColor: Colors.grey,
                hintColor: Colors.grey,
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
    }

    testWidgets('空数据：只渲染占位、不抛异常（不伪造曲线）', (tester) async {
      useTallScreen(tester);
      await pumpChart(tester, const []);
      expect(find.byType(TrendChart), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('单点：只画点不连线、不抛异常', (tester) async {
      useTallScreen(tester);
      await pumpChart(tester, const [TrendPoint('9/1', 0.8)]);
      expect(find.byType(TrendChart), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('多点：连成折线 + 标注，不抛异常', (tester) async {
      useTallScreen(tester);
      await pumpChart(tester, const [
        TrendPoint('9/1', 0.6),
        TrendPoint('9/2', 0.7),
        TrendPoint('9/3', 0.9),
      ]);
      expect(find.byType(TrendChart), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('边界：value 极值 0 与 1 都不崩', (tester) async {
      useTallScreen(tester);
      await pumpChart(tester, const [
        TrendPoint('a', 0.0),
        TrendPoint('b', 1.0),
      ]);
      expect(tester.takeException(), isNull);
    });
  });
}
