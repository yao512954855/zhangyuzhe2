/// WP-APP-05 护眼休息 —— F5 / F6 / U6 / U12。
///
/// - F5：休息页 20 秒倒计时 + 可提前结束 + 不强制锁（复用 APP-01 的 eye_rest_page）；
/// - U6：护眼关闭后不弹（EyeCareService 关闭时累计清零、不触发 onRestDue）；
/// - F6：自动暖色（resolveThemeId 在 19:00–07:00 且开启时强制夜间）。
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/widgets/app_button.dart';
import 'package:student_app/core/eye_care/eye_care_service.dart';
import 'package:student_app/core/theme/theme_controller.dart';
import 'package:student_app/features/eye_care/eye_rest_page.dart';

import 'test_app05_helpers.dart';

/// 点按钮把休息页 push 成普通路由的测试壳（避免 showDialog 的双层包裹，
/// 仍能完整跑通 EyeRestPage 的「提前结束 → maybePop」路径）。
class _EyeRestLauncher extends StatelessWidget {
  const _EyeRestLauncher();

  @override
  Widget build(BuildContext context) => Scaffold(
        body: ElevatedButton(
          onPressed: () => Navigator.of(context).push(
            MaterialPageRoute<void>(
              builder: (_) => const EyeRestPage(),
            ),
          ),
          child: const Text('open'),
        ),
      );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('U6 护眼关闭后不弹（EyeCareService 纯逻辑）', () {
    test('关闭状态下累计再久也不触发 onRestDue', () {
      final svc = EyeCareService(enabled: false);
      svc.tick(const Duration(minutes: 21));
      expect(svc.restDue, isFalse);
      expect(svc.accumulated, Duration.zero, reason: '关闭时累计必须清零');
    });

    test('开启状态累计满 20 分钟触发 onRestDue', () async {
      final svc = EyeCareService(enabled: true);
      // 先订阅（不立刻 await），再触发
      final expectFuture = expectLater(svc.onRestDue, emits(null));
      svc.tick(const Duration(minutes: 21));
      expect(svc.restDue, isTrue);
      await expectFuture;
    });

    test('中途关闭会清零已累计时长', () {
      final svc = EyeCareService(enabled: true);
      svc.tick(const Duration(minutes: 10));
      expect(svc.accumulated, const Duration(minutes: 10));
      svc.setEnabled(false);
      expect(svc.accumulated, Duration.zero);
      svc.setEnabled(true);
      expect(svc.restDue, isFalse);
    });
  });

  group('F6 自动暖色（resolveThemeId 纯逻辑）', () {
    test('开启自动暖色且在暖色时段 → 强制夜间', () {
      final id = resolveThemeId(
        mode: AppThemeMode.day,
        autoWarm: true,
        platformBrightness: Brightness.light,
        now: DateTime(2026, 1, 1, 21),
      );
      expect(id, AppThemeId.night);
    });

    test('开启自动暖色但不在暖色时段 → 跟随手动', () {
      final id = resolveThemeId(
        mode: AppThemeMode.day,
        autoWarm: true,
        platformBrightness: Brightness.light,
        now: DateTime(2026, 1, 1, 12),
      );
      expect(id, AppThemeId.day);
    });

    test('关闭自动暖色 → 用手动/系统', () {
      final id = resolveThemeId(
        mode: AppThemeMode.system,
        autoWarm: false,
        platformBrightness: Brightness.dark,
        now: DateTime(2026, 1, 1, 22),
      );
      expect(id, AppThemeId.night);
    });
  });

  group('F5/U12 休息页', () {
    testWidgets('显示倒计时数字与「我休息好了」，点它提前结束并关闭', (tester) async {
      useTallScreen(tester);
      final spy = PushSpy();
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.build(palette: AppPalettes.day),
          navigatorObservers: [spy],
          home: AgeScope(
            profile: AgeProfile.of(AgeBand.junior),
            child: const _EyeRestLauncher(),
          ),
        ),
      );
      await tester.tap(find.text('open'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 200));

      expect(find.text('我休息好了'), findsOneWidget);
      expect(find.text('看看窗外，让眼睛歇一歇吧'), findsOneWidget);
      expect(find.text('20'), findsWidgets);

      await tester.tap(find.widgetWithText(AppButton, '我休息好了'));
      // 退出动画约 300ms，给足时间再断言
      await tester.pump(const Duration(seconds: 1));
      // 提前结束 → 休息页路由被 pop（用 observer 断言，避免受退出动画时序影响）
      expect(spy.popped, isNotEmpty, reason: '点「我休息好了」应关闭休息页');
      // 关闭后回到 launcher（"open" 按钮重新可见），证明休息页已离开
      expect(find.text('open'), findsOneWidget);
    });

    testWidgets('倒计时跑满自动归零（restSeconds=2 加速验证）', (tester) async {
      useTallScreen(tester);
      await tester.pumpWidget(
        MaterialApp(
          theme: AppTheme.build(palette: AppPalettes.day),
          home: AgeScope(
            profile: AgeProfile.of(AgeBand.junior),
            child: const EyeRestPage(restSeconds: 2),
          ),
        ),
      );
      // 不用 pumpAndSettle：呼吸动画无限循环会使其超时
      await tester.pump();
      expect(find.text('2'), findsWidgets);
      await tester.pump(const Duration(seconds: 3));
      expect(find.text('0'), findsWidgets, reason: '倒计时应能在归零时停下并显示 0');
    });
  });
}
