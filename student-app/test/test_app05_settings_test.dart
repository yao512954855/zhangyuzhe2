/// WP-APP-05 设置/关于 —— F4 / F6 / F8 + U6。
///
/// - F4：主题切换（复用 showThemeSwitcher）、护眼开关、服务器地址、关于；
/// - F6：自动暖色开关即时生效并持久化；
/// - F8：关于页展示版本号/服务器地址/连接状态；
/// - U6：护眼开关可关，关闭后累计清零（由 EyeCareService 保证，见 eye_care 测试）。
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/features/mine/settings_page.dart';

import 'test_app05_helpers.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late LocalStore local;
  late ServerStore serverStore;

  setUp(() async {
    (local, serverStore) = await openTestStore();
  });

  group('F4 主题切换：复用 showThemeSwitcher，选择即时生效并持久化', () {
    testWidgets('点主题卡片 → 底部弹层出现四态 → 选「夜间」后副标题与本地都更新', (tester) async {
      useTallScreen(tester);
      final adapter = defaultAdapter();
      await tester.pumpWidget(bootApp(const SettingsPage(), local: local, serverStore: serverStore, adapter: adapter));
      await tester.pumpAndSettle();

      // 初始副标题为「跟随系统」
      expect(find.text('跟随系统'), findsOneWidget);

      // 点「主题」卡片，打开主题切换弹层
      await tester.tap(find.widgetWithText(Card, '主题'));
      await tester.pumpAndSettle();

      // 弹层里四态都在
      expect(find.text('跟随系统'), findsWidgets);
      expect(find.text('日间'), findsOneWidget);
      expect(find.text('纸质护眼'), findsOneWidget);
      expect(find.text('夜间'), findsOneWidget);

      // 选「夜间」
      await tester.tap(find.text('夜间'));
      await tester.pumpAndSettle();

      // 设置页副标题更新（副标题 + 仍未关闭的弹层里都含「夜间」）+ 本地持久化
      expect(find.text('夜间'), findsWidgets);
      expect(local.themeId, 'night');
    });
  });

  group('F6 自动暖色开关', () {
    testWidgets('默认开启；关掉后本地与状态都变为关闭', (tester) async {
      useTallScreen(tester);
      final adapter = defaultAdapter();
      await tester.pumpWidget(bootApp(const SettingsPage(), local: local, serverStore: serverStore, adapter: adapter));
      await tester.pumpAndSettle();

      expect(local.autoWarm, isTrue, reason: '默认开启自动暖色');

      // 第一个 Switch = 「晚上自动变暗」
      final autoWarmSwitch = find.byType(Switch).at(0);
      await tester.tap(autoWarmSwitch);
      await tester.pumpAndSettle();

      expect(local.autoWarm, isFalse);
    });
  });

  group('U6 护眼开关', () {
    testWidgets('关掉护眼提醒后本地持久化为关闭', (tester) async {
      useTallScreen(tester);
      final adapter = defaultAdapter();
      await tester.pumpWidget(bootApp(const SettingsPage(), local: local, serverStore: serverStore, adapter: adapter));
      await tester.pumpAndSettle();

      expect(local.eyeCareEnabled, isTrue, reason: '默认开启护眼');

      // 第二个 Switch = 「护眼提醒」
      final eyeSwitch = find.byType(Switch).at(1);
      await tester.tap(eyeSwitch);
      await tester.pumpAndSettle();

      expect(local.eyeCareEnabled, isFalse);
    });
  });

  group('F8 关于页', () {
    testWidgets('展示版本号 / 服务器地址 / 连接状态', (tester) async {
      useTallScreen(tester);
      final adapter = defaultAdapter();
      await tester.pumpWidget(bootApp(const SettingsPage(), local: local, serverStore: serverStore, adapter: adapter));
      await tester.pumpAndSettle();

      await tester.tap(find.widgetWithText(Card, '关于'));
      await tester.pumpAndSettle();

      expect(find.text('版本号'), findsOneWidget);
      expect(find.text('服务器地址'), findsOneWidget);
      expect(find.text('连接状态'), findsOneWidget);
      // 默认 adapter 对 /api/ping 回 200 → 已连接
      expect(find.text('已连接到服务器'), findsOneWidget);
      // 服务器地址展示为 host:port
      expect(find.text('$kTestServerHost:$kTestServerPort'), findsOneWidget);
    });
  });

  group('F4 服务器地址入口', () {
    testWidgets('设置页展示当前服务器地址', (tester) async {
      useTallScreen(tester);
      final adapter = defaultAdapter();
      await tester.pumpWidget(bootApp(const SettingsPage(), local: local, serverStore: serverStore, adapter: adapter));
      await tester.pumpAndSettle();
      expect(find.text('$kTestServerHost:$kTestServerPort'), findsOneWidget);
    });
  });
}
