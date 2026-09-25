/// 三套护眼主题 token —— UI 规范 §2（硬约束）。
///
/// 设计原则：
/// 1. **色值原样保留 hex 字符串**，与《背记系统UI设计规范》表格逐条对得上，
///    改色时能一眼比对，也方便测试直接断言 hex。
/// 2. 夜间主题有 5 个 token 规范里**没定义**（`primaryDark` / `textFaint` /
///    `successLight` / `errorLight` / `hintLight`），本文件按「亮度同比例推导」
///    补齐，已记入 `docs/dev/02-实现约定与复用经验.md` 待裁决清单。
/// 3. 对比度不靠肉眼，`test/test_app01_theme.dart` 会真的算 WCAG 比值并断言。
library;

import 'package:flutter/material.dart';

import '../util/format.dart';

/// 主题标识。
enum AppThemeId {
  /// 日间 · 暖白（默认）
  day,
  paper,
  night;

  String get label => switch (this) {
        AppThemeId.day => '日间',
        AppThemeId.paper => '纸质护眼',
        AppThemeId.night => '夜间',
      };
}

/// 一套主题的全部色值（0xRRGGBB）。
@immutable
class AppPalette {
  const AppPalette({
    required this.id,
    required this.bg,
    required this.card,
    required this.primary,
    required this.primaryLight,
    required this.primaryDark,
    required this.textMain,
    required this.textSub,
    required this.textFaint,
    required this.divider,
    required this.success,
    required this.successLight,
    required this.error,
    required this.errorLight,
    required this.hint,
    required this.hintLight,
  });

  final AppThemeId id;

  /// 页面背景。
  final int bg;

  /// 卡片背景。
  final int card;

  final int primary;
  final int primaryLight;
  final int primaryDark;
  final int textMain;
  final int textSub;
  final int textFaint;
  final int divider;
  final int success;
  final int successLight;
  final int error;
  final int errorLight;
  final int hint;
  final int hintLight;

  bool get isDark => id == AppThemeId.night;

  Color _c(int rgb) => Color(0xFF000000 | rgb);

  Color get bgColor => _c(bg);
  Color get cardColor => _c(card);
  Color get primaryColor => _c(primary);
  Color get primaryLightColor => _c(primaryLight);
  Color get primaryDarkColor => _c(primaryDark);
  Color get textMainColor => _c(textMain);
  Color get textSubColor => _c(textSub);
  Color get textFaintColor => _c(textFaint);
  Color get dividerColor => _c(divider);
  Color get successColor => _c(success);
  Color get successLightColor => _c(successLight);
  Color get errorColor => _c(error);
  Color get errorLightColor => _c(errorLight);
  Color get hintColor => _c(hint);
  Color get hintLightColor => _c(hintLight);

  /// 只用色值做相等判断，方便测试比对。
  Map<String, int> get tokens => {
        'bg': bg,
        'card': card,
        'primary': primary,
        'primaryLight': primaryLight,
        'primaryDark': primaryDark,
        'textMain': textMain,
        'textSub': textSub,
        'textFaint': textFaint,
        'divider': divider,
        'success': success,
        'successLight': successLight,
        'error': error,
        'errorLight': errorLight,
        'hint': hint,
        'hintLight': hintLight,
      };
}

/// 三套主题的色值表 —— 严格照 UI 规范 §2.1 / §2.2 / §2.3。
abstract final class AppPalettes {
  /// 日间 · 暖白（默认）。正文对比度 `#2B2B2B` on `#F7F5F0` = 13.00:1（WCAG 实算）。
  static const day = AppPalette(
    id: AppThemeId.day,
    bg: 0xF7F5F0,
    card: 0xFFFFFF,
    primary: 0x2E7D74,
    primaryLight: 0xE3F0EE,
    primaryDark: 0x1F5C55,
    textMain: 0x2B2B2B,
    textSub: 0x6B6B6B,
    textFaint: 0x9A9A9A,
    divider: 0xE8E4DC,
    success: 0x4CAF7D,
    successLight: 0xE8F5EE,
    error: 0xE5645C,
    errorLight: 0xFBEDEC,
    hint: 0xE8A33D,
    hintLight: 0xFCF3E4,
  );

  /// 纸质护眼：只换背景/卡片/主文字，其余同日间（UI 规范 §2.2「其余同日间主题」）。
  static const paper = AppPalette(
    id: AppThemeId.paper,
    bg: 0xF5EEDF,
    card: 0xFBF7ED,
    primary: 0x2E7D74,
    primaryLight: 0xE3F0EE,
    primaryDark: 0x1F5C55,
    textMain: 0x3A3428,
    textSub: 0x6B6B6B,
    textFaint: 0x9A9A9A,
    divider: 0xE8E4DC,
    success: 0x4CAF7D,
    successLight: 0xE8F5EE,
    error: 0xE5645C,
    errorLight: 0xFBEDEC,
    hint: 0xE8A33D,
    hintLight: 0xFCF3E4,
  );

  /// 夜间：**不使用纯黑**（`#000000` 在发光屏上对比过强）。
  static const night = AppPalette(
    id: AppThemeId.night,
    bg: 0x1B1F23,
    card: 0x24292E,
    primary: 0x5CBDB0,
    primaryLight: 0x24352F,
    // ↓ 以下 5 项 UI 规范未定义，按同比例推导补齐
    primaryDark: 0x3E8F84,
    textMain: 0xD6D6D6,
    textSub: 0x9BA1A6,
    textFaint: 0x6E767B,
    divider: 0x32383E,
    success: 0x6FCF97,
    successLight: 0x22322A,
    error: 0xF08A82,
    errorLight: 0x3A2724,
    hint: 0xF0BE6D,
    hintLight: 0x3A3226,
  );

  static const all = <AppThemeId, AppPalette>{
    AppThemeId.day: day,
    AppThemeId.paper: paper,
    AppThemeId.night: night,
  };

  static AppPalette of(AppThemeId id) => all[id]!;
}

/// Material 没有语义槽的那些色（正确/提示/浅底），用 [ThemeExtension] 往下传。
///
/// 也顺带把 `textSub` / `textFaint` / `card` / `divider` 收进来 ——
/// Material 3 的 `TextTheme` 只区分 body/label，拿不到「弱化文字」这一档，
/// 页面里到处 `Theme.of(context).textTheme.bodySmall` 再手动调色更容易漂移。
@immutable
class AppExtraColors extends ThemeExtension<AppExtraColors> {
  const AppExtraColors({
    required this.card,
    required this.textSub,
    required this.textFaint,
    required this.divider,
    required this.success,
    required this.successLight,
    required this.error,
    required this.errorLight,
    required this.hint,
    required this.hintLight,
  });

  factory AppExtraColors.ofPalette(AppPalette p) => AppExtraColors(
        card: p.cardColor,
        textSub: p.textSubColor,
        textFaint: p.textFaintColor,
        divider: p.dividerColor,
        success: p.successColor,
        successLight: p.successLightColor,
        error: p.errorColor,
        errorLight: p.errorLightColor,
        hint: p.hintColor,
        hintLight: p.hintLightColor,
      );

  final Color card;
  final Color textSub;
  final Color textFaint;
  final Color divider;
  final Color success;
  final Color successLight;
  final Color error;
  final Color errorLight;
  final Color hint;
  final Color hintLight;

  @override
  AppExtraColors copyWith({
    Color? card,
    Color? textSub,
    Color? textFaint,
    Color? divider,
    Color? success,
    Color? successLight,
    Color? error,
    Color? errorLight,
    Color? hint,
    Color? hintLight,
  }) =>
      AppExtraColors(
        card: card ?? this.card,
        textSub: textSub ?? this.textSub,
        textFaint: textFaint ?? this.textFaint,
        divider: divider ?? this.divider,
        success: success ?? this.success,
        successLight: successLight ?? this.successLight,
        error: error ?? this.error,
        errorLight: errorLight ?? this.errorLight,
        hint: hint ?? this.hint,
        hintLight: hintLight ?? this.hintLight,
      );

  @override
  AppExtraColors lerp(ThemeExtension<AppExtraColors>? other, double t) {
    if (other is! AppExtraColors) {
      return this;
    }
    return AppExtraColors(
      card: Color.lerp(card, other.card, t)!,
      textSub: Color.lerp(textSub, other.textSub, t)!,
      textFaint: Color.lerp(textFaint, other.textFaint, t)!,
      divider: Color.lerp(divider, other.divider, t)!,
      success: Color.lerp(success, other.success, t)!,
      successLight: Color.lerp(successLight, other.successLight, t)!,
      error: Color.lerp(error, other.error, t)!,
      errorLight: Color.lerp(errorLight, other.errorLight, t)!,
      hint: Color.lerp(hint, other.hint, t)!,
      hintLight: Color.lerp(hintLight, other.hintLight, t)!,
    );
  }

  /// 便捷取值：`context.extraColors.hint`。
  static AppExtraColors of(BuildContext context) =>
      Theme.of(context).extension<AppExtraColors>()!;
}

/// 由「调色板 + 分龄档位」构建 [ThemeData]。
abstract final class AppTheme {
  /// 主题切换动画时长：200ms 淡入，**避免整树重建导致闪白**（UI 规范 §2.5 禁闪烁）。
  static const Duration switchDuration = Duration(milliseconds: 200);

  static ThemeData build({required AppPalette palette}) {
    final scheme = ColorScheme.fromSeed(
      seedColor: palette.primaryColor,
      brightness: palette.isDark ? Brightness.dark : Brightness.light,
    ).copyWith(
      primary: palette.primaryColor,
      onPrimary: palette.isDark ? const Color(0xFF10231F) : Colors.white,
      primaryContainer: palette.primaryLightColor,
      onPrimaryContainer: palette.textMainColor,
      surface: palette.bgColor,
      onSurface: palette.textMainColor,
      error: palette.errorColor,
      onError: Colors.white,
      outlineVariant: palette.dividerColor,
    );

    // 字重只用 400 / 500 两档（UI 规范 §3.2：不用 600/700）
    TextStyle w500(double size) =>
        TextStyle(fontSize: size, fontWeight: FontWeight.w500);
    TextStyle w400(double size) =>
        TextStyle(fontSize: size, fontWeight: FontWeight.w400);

    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: palette.bgColor,
      fontFamily: null, // 中文用系统默认（苹方/鸿蒙 Sans/思源黑体），不打包字体
      textTheme: TextTheme(
        // 正文 18/17/16、行高 1.7 —— 这里放低年级档位，分龄字号在页面层叠加
        bodyLarge: w400(18).copyWith(height: 1.7, color: palette.textMainColor),
        bodyMedium: w400(18).copyWith(height: 1.7, color: palette.textMainColor),
        bodySmall: w400(16).copyWith(height: 1.6, color: palette.textSubColor),
        titleLarge: w500(26).copyWith(height: 1.4, color: palette.textMainColor),
        titleMedium: w500(20).copyWith(height: 1.5, color: palette.textMainColor),
        labelLarge: w500(20).copyWith(color: palette.textMainColor),
      ),
      extensions: <ThemeExtension<dynamic>>[
        AppExtraColors.ofPalette(palette),
      ],
      appBarTheme: AppBarTheme(
        backgroundColor: palette.bgColor,
        foregroundColor: palette.textMainColor,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: true,
        titleTextStyle: w500(22).copyWith(color: palette.textMainColor),
      ),
      cardTheme: CardThemeData(
        color: palette.cardColor,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: palette.dividerColor, width: 0.5),
        ),
      ),
      dividerTheme: DividerThemeData(
        color: palette.dividerColor,
        thickness: 0.5,
        space: 0.5,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: palette.cardColor,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: palette.dividerColor, width: 0.5),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: palette.dividerColor, width: 0.5),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: palette.primaryColor, width: 1.5),
        ),
        hintStyle: w400(18).copyWith(color: palette.textFaintColor),
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: palette.cardColor,
        contentTextStyle: w400(16).copyWith(color: palette.textMainColor),
        behavior: SnackBarBehavior.floating,
      ),
      // 答错不弹模态框（UI 规范 §5.2），所以对话框统一柔和样式，只给护眼提醒用
      dialogTheme: DialogThemeData(
        backgroundColor: palette.cardColor,
        shape:
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        titleTextStyle: w500(20).copyWith(color: palette.textMainColor),
        contentTextStyle: w400(18).copyWith(
          height: 1.7,
          color: palette.textMainColor,
        ),
      ),
    );
  }

  /// 把 `#RRGGBB` 转成 [Color]（供测试与调试用）。
  static Color colorOf(String hex) => Color(0xFF000000 | parseHex(hex));
}
