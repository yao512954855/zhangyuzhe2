/// 分龄字号与触控档位 —— UI 规范 §3.3 + §4.1（硬约束）。
///
/// 使用对象是 6–15 岁学生，**字号与触控目标必须随年级档位变化**，
/// 且 `正文 ≥ 16sp` / `触控 ≥ 48dp` 是**不可突破的下限**。
///
/// ⚠️ 已知规格内部矛盾（已记入 `docs/dev/02` 待裁决清单）：
/// 30-学生端工作包规格 F4 把触控写成 `64/56/48`（三档），
/// 而 UI 规范 §4.1 的主按钮是 `64/56/52`、次级按钮 `56/52/48`。
/// 本文件**以 UI 规范 §4.1 为准**（它是更细的分元素表格，且文档开头明确
/// 「UI 规范为硬约束」），两处共同的下限 48dp 一致，不影响验收。
library;

import 'package:flutter/material.dart';

/// 年龄档位：低 / 中 / 高年级。
enum AgeBand {
  /// 1–3 年级
  junior,

  /// 4–6 年级
  middle,

  /// 7–9 年级
  senior;

  String get label => switch (this) {
        AgeBand.junior => '低年级',
        AgeBand.middle => '中年级',
        AgeBand.senior => '高年级',
      };
}

/// 一个档位下的全部字号（sp）与触控高度（dp）。
@immutable
class AgeProfile {
  const AgeProfile({
    required this.band,
    required this.pageTitle,
    required this.cardTitle,
    required this.body,
    required this.caption,
    required this.reciteBig,
    required this.wordBig,
    required this.buttonText,
    required this.primaryButtonHeight,
    required this.secondaryButtonHeight,
    required this.listItemHeight,
    required this.iconButtonSize,
  });

  final AgeBand band;

  // ---- 字号 ----
  final double pageTitle;
  final double cardTitle;
  final double body;
  final double caption;

  /// 课文背诵大字（行高 ≥2.0，否则逐字点亮会糊成一片）。
  final double reciteBig;

  /// 英文单词展示。
  final double wordBig;

  final double buttonText;

  // ---- 触控 ----
  final double primaryButtonHeight;
  final double secondaryButtonHeight;
  final double listItemHeight;
  final double iconButtonSize;

  // ---- 硬下限（UI 规范 §3.3 / §4.1）----
  static const double minFontSize = 16;
  static const double minTouchTarget = 48;

  // ---- 间距（UI 规范 §4.1）----
  static const double elementGap = 16;
  static const double adjacentButtonGap = 20;
  static const double screenEdgeInset = 16;

  /// 课文背诵行高，必须 ≥ 2.0（UI 规范 §3.3 明文要求）。
  static const double reciteLineHeight = 2.0;

  static const _junior = AgeProfile(
    band: AgeBand.junior,
    pageTitle: 26,
    cardTitle: 20,
    body: 18,
    caption: 16,
    reciteBig: 36,
    wordBig: 40,
    buttonText: 20,
    primaryButtonHeight: 64,
    secondaryButtonHeight: 56,
    listItemHeight: 72,
    iconButtonSize: 64,
  );

  static const _middle = AgeProfile(
    band: AgeBand.middle,
    pageTitle: 24,
    cardTitle: 19,
    body: 17,
    caption: 16,
    reciteBig: 30,
    wordBig: 34,
    buttonText: 18,
    primaryButtonHeight: 56,
    secondaryButtonHeight: 52,
    listItemHeight: 68,
    iconButtonSize: 56,
  );

  static const _senior = AgeProfile(
    band: AgeBand.senior,
    pageTitle: 22,
    cardTitle: 18,
    body: 16,
    caption: 16,
    reciteBig: 26,
    wordBig: 30,
    buttonText: 17,
    primaryButtonHeight: 52,
    secondaryButtonHeight: 48,
    listItemHeight: 64,
    iconButtonSize: 48,
  );

  static const all = <AgeBand, AgeProfile>{
    AgeBand.junior: _junior,
    AgeBand.middle: _middle,
    AgeBand.senior: _senior,
  };

  static AgeProfile of(AgeBand band) => all[band]!;

  /// 年级（1–9）→ 档位。
  ///
  /// 越界一律 clamp（`<1` 视为低年级、`>9` 视为高年级），
  /// **不抛异常** —— 年级来自服务端与本地偏好两处，脏数据不该让 App 崩在启动路径上。
  /// `null`（尚未选择）默认**低年级**：字号最大最稳妥，宁可字大不可看不清。
  static AgeProfile ofGrade(int? grade) {
    if (grade == null || grade <= 3) {
      return _junior;
    }
    if (grade <= 6) {
      return _middle;
    }
    return _senior;
  }

  /// 该档位的正文字号，**保证不低于硬下限**（防御性：改表时改错了也会被拦下）。
  double get safeBody => body < minFontSize ? minFontSize : body;

  /// 该档位的辅说明字号（同样受下限保护）。
  double get safeCaption => caption < minFontSize ? minFontSize : caption;

  /// 主按钮高度（受 48dp 下限保护）。
  double get safePrimaryHeight =>
      primaryButtonHeight < minTouchTarget ? minTouchTarget : primaryButtonHeight;

  /// 图标按钮边长（受 48dp 下限保护）。
  double get safeIconSize =>
      iconButtonSize < minTouchTarget ? minTouchTarget : iconButtonSize;

  TextStyle get pageTitleStyle =>
      TextStyle(fontSize: pageTitle, fontWeight: FontWeight.w500, height: 1.4);

  TextStyle get cardTitleStyle =>
      TextStyle(fontSize: cardTitle, fontWeight: FontWeight.w500, height: 1.5);

  TextStyle get bodyStyle =>
      TextStyle(fontSize: safeBody, fontWeight: FontWeight.w400, height: 1.7);

  TextStyle get captionStyle =>
      TextStyle(fontSize: safeCaption, fontWeight: FontWeight.w400, height: 1.6);

  /// 课文背诵大字（行高 2.0，逐字点亮用）。
  TextStyle get reciteStyle => TextStyle(
        fontSize: reciteBig,
        fontWeight: FontWeight.w400,
        height: reciteLineHeight,
      );

  TextStyle get wordStyle =>
      TextStyle(fontSize: wordBig, fontWeight: FontWeight.w500, height: 1.4);

  TextStyle get buttonTextStyle =>
      TextStyle(fontSize: buttonText, fontWeight: FontWeight.w500, height: 1.0);
}

/// 把分龄档位往下传，页面里 `AgeScope.of(context).bodyStyle` 即可取用。
///
/// 用 [InheritedWidget] 而不是 riverpod：档位随年级变化频率低、
/// 且每个页面渲染时都需要，走 InheritedWidget 的依赖通知更轻、也不需要 watch。
class AgeScope extends InheritedWidget {
  const AgeScope({super.key, required this.profile, required super.child});

  final AgeProfile profile;

  static AgeProfile of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AgeScope>();
    // 兜底低年级：App 一定在 AgeScope 内，这里只是防页面被单独 pump 时炸掉
    return scope?.profile ?? AgeProfile.of(AgeBand.junior);
  }

  @override
  bool updateShouldNotify(AgeScope oldWidget) =>
      oldWidget.profile.band != profile.band;
}
