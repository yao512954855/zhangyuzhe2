/// App 外壳 —— 30-APP-01 F1 ~ F5。
///
/// 三件事在这里收口：
/// 1. **主题**：三套调色板 + 自动暖色，切换走 200ms 淡入（**不闪白**，UI 规范 §2.5）；
/// 2. **分龄**：[AgeScope] 挂在 `MaterialApp.builder` 上，所有页面都能取到档位；
/// 3. **护眼**：跟着 App 生命周期累积时长，到点弹休息页（首页/练习页都生效）。
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/providers.dart';
import 'core/router/app_router.dart';
import 'core/theme/age_profile.dart';
import 'core/theme/app_theme.dart';
import 'core/theme/theme_controller.dart';
import 'features/eye_care/eye_rest_page.dart';

/// 学生端根组件。
class StudentApp extends ConsumerStatefulWidget {
  const StudentApp({super.key, this.clock});

  /// 时间源。默认取系统时间；测试注入固定时间，
  /// 否则「19:00 后自动切夜间」这类逻辑会随跑测试的钟点变红或变绿。
  final DateTime Function()? clock;

  @override
  ConsumerState<StudentApp> createState() => _StudentAppState();
}

class _StudentAppState extends ConsumerState<StudentApp>
    with WidgetsBindingObserver {
  final _navigatorKey = GlobalKey<NavigatorState>();
  StreamSubscription<void>? _restSub;
  bool _resting = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _bindEyeCare();
  }

  void _bindEyeCare() {
    final eyeCare = ref.read(eyeCareProvider);
    _restSub = eyeCare.onRestDue.listen((_) => _showRest());
    eyeCare.startSession();
  }

  Future<void> _showRest() async {
    if (_resting || !mounted) {
      return;
    }
    final context = _navigatorKey.currentContext;
    if (context == null) {
      return;
    }
    _resting = true;
    await showEyeRest(context);
    _resting = false;
    if (mounted) {
      // 学生点完「我休息好了」→ 重新开始计时
      ref.read(eyeCareProvider).markRested();
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final eyeCare = ref.read(eyeCareProvider);
    switch (state) {
      case AppLifecycleState.resumed:
        eyeCare.startSession();
      case AppLifecycleState.paused:
      case AppLifecycleState.inactive:
      case AppLifecycleState.detached:
      case AppLifecycleState.hidden:
        // 切出去不算用眼，暂停累计但**不清零**
        eyeCare.stopSession();
    }
  }

  @override
  void dispose() {
    _restSub?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final themeState = ref.watch(themeControllerProvider);
    final profile = ref.watch(ageProfileProvider);

    final warm = themeState.autoWarm && isWarmHour(widget.clock?.call() ?? DateTime.now());
    final (ThemeMode themeMode, AppPalette lightPalette) = switch (themeState.mode) {
      // 自动暖色优先于手动选择（护眼优先于功能丰富度）
      _ when warm => (ThemeMode.dark, AppPalettes.day),
      AppThemeMode.night => (ThemeMode.dark, AppPalettes.day),
      AppThemeMode.paper => (ThemeMode.light, AppPalettes.paper),
      AppThemeMode.day => (ThemeMode.light, AppPalettes.day),
      AppThemeMode.system => (ThemeMode.system, AppPalettes.day),
    };

    return MaterialApp(
      title: '背记系统',
      debugShowCheckedModeBanner: false,
      navigatorKey: _navigatorKey,
      theme: AppTheme.build(palette: lightPalette),
      darkTheme: AppTheme.build(palette: AppPalettes.night),
      themeMode: themeMode,
      // 200ms 淡入：避免"先白后黑"的整树重建（UI 规范 §2.5 禁闪烁）
      themeAnimationDuration: AppTheme.switchDuration,
      themeAnimationCurve: Curves.easeOut,
      initialRoute: AppRouter.initialRoute,
      routes: AppRouter.routes,
      onUnknownRoute: AppRouter.onUnknownRoute,
      builder: (context, child) {
        return AgeScope(
          profile: profile,
          // 系统字体放大尊重，但**上限 1.3**：再大分龄布局会被撑破，
          // 而下限锁 1.0 —— UI 规范 §3.3 的 16sp 下限不能被系统缩到更小
          child: MediaQuery.withClampedTextScaling(
            minScaleFactor: 1.0,
            maxScaleFactor: 1.3,
            child: child ?? const SizedBox.shrink(),
          ),
        );
      },
    );
  }
}
