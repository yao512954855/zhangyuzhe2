/// 主题切换控制 —— UI 规范 §2.4 / §6.2（U9）。
///
/// 四种模式：跟随系统 / 日间 / 纸质护眼 / 夜间，外加「自动暖色」开关。
///
/// **切换不闪白**是硬要求（§2.5 禁闪烁），做法见 `app.dart`：
/// 三套主题都从 MaterialApp 的同一个 `theme:` 传入（不靠 `darkTheme` 切换），
/// 让框架走 `AnimatedTheme` 的 200ms 淡入，而不是"先白后黑"地重建。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers.dart';
import 'app_theme.dart';

/// 学生选择的主题模式。
enum AppThemeMode {
  /// 跟随系统（默认）。
  system,
  day,
  paper,
  night;

  String get label => switch (this) {
        AppThemeMode.system => '跟随系统',
        AppThemeMode.day => '日间',
        AppThemeMode.paper => '纸质护眼',
        AppThemeMode.night => '夜间',
      };

  static AppThemeMode parse(String? raw) {
    return AppThemeMode.values.firstWhere(
      (m) => m.name == raw,
      orElse: () => AppThemeMode.system,
    );
  }
}

/// 当前主题状态。
@immutable
class AppThemeState {
  const AppThemeState({required this.mode, required this.autoWarm});

  final AppThemeMode mode;

  /// 19:00–07:00 自动切夜间（UI 规范 §6.2），可关。
  final bool autoWarm;

  @override
  bool operator ==(Object other) =>
      other is AppThemeState && other.mode == mode && other.autoWarm == autoWarm;

  @override
  int get hashCode => Object.hash(mode, autoWarm);
}

/// 自动暖色的生效时段：19:00 至次日 07:00。
const int kAutoWarmStartHour = 19;
const int kAutoWarmEndHour = 7;

/// 当前是否处在「自动暖色」时段内。
bool isWarmHour(DateTime now) =>
    now.hour >= kAutoWarmStartHour || now.hour < kAutoWarmEndHour;

/// 由「模式 + 自动暖色 + 系统亮度 + 当前时间」算出**实际生效**的主题。
///
/// 抽成顶层纯函数：这段逻辑有三层优先级，埋在 Widget 里最容易写反，
/// 单独放出来就能直接对着时间点断言（见 `test/test_app01_theme.dart`）。
///
/// 优先级：**自动暖色 > 手动选择 > 跟随系统**。
/// 也就是说：打开自动暖色后，晚上即使手动选了「日间」也会走夜间 ——
/// 这是"护眼优先于功能丰富度"铁律的直接体现（30 文档开头）。
AppThemeId resolveThemeId({
  required AppThemeMode mode,
  required bool autoWarm,
  required Brightness platformBrightness,
  required DateTime now,
}) {
  if (autoWarm && isWarmHour(now)) {
    return AppThemeId.night;
  }
  return switch (mode) {
    AppThemeMode.system => platformBrightness == Brightness.dark
        ? AppThemeId.night
        : AppThemeId.day,
    AppThemeMode.day => AppThemeId.day,
    AppThemeMode.paper => AppThemeId.paper,
    AppThemeMode.night => AppThemeId.night,
  };
}

/// 主题控制器（riverpod）。
class ThemeController extends Notifier<AppThemeState> {
  @override
  AppThemeState build() {
    final store = ref.read(localStoreProvider);
    return AppThemeState(
      mode: AppThemeMode.parse(store.themeId),
      autoWarm: store.autoWarm,
    );
  }

  Future<void> setMode(AppThemeMode mode) async {
    state = AppThemeState(mode: mode, autoWarm: state.autoWarm);
    await ref.read(localStoreProvider).setThemeId(mode.name);
  }

  Future<void> setAutoWarm(bool value) async {
    state = AppThemeState(mode: state.mode, autoWarm: value);
    await ref.read(localStoreProvider).setAutoWarm(value);
  }

  /// 当前应使用的调色板。
  AppPalette paletteNow({
    required Brightness platformBrightness,
    DateTime? now,
  }) {
    final id = resolveThemeId(
      mode: state.mode,
      autoWarm: state.autoWarm,
      platformBrightness: platformBrightness,
      now: now ?? DateTime.now(),
    );
    return AppPalettes.of(id);
  }
}

/// 主题状态 Provider。
final themeControllerProvider =
    NotifierProvider<ThemeController, AppThemeState>(ThemeController.new);
