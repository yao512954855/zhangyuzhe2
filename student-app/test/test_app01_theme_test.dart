/// WP-APP-01 主题与分龄 —— UI 规范 §2 / §3.3 / §4.1 / §6。
///
/// 这些断言的价值在于：**把设计文档里的"硬约束"变成会报错的代码**。
/// 改错一个色值、把字号调小一档，`flutter test` 立刻红。
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:student_app/core/theme/age_profile.dart';
import 'package:student_app/core/theme/app_theme.dart';
import 'package:student_app/core/theme/theme_controller.dart';
import 'package:student_app/core/util/format.dart';

void main() {
  group('色值解析', () {
    test('支持 #RRGGBB / RRGGBB / #RGB', () {
      expect(parseHex('#F7F5F0'), 0xF7F5F0);
      expect(parseHex('F7F5F0'), 0xF7F5F0);
      expect(parseHex('  #f7f5f0  '), 0xF7F5F0);
      expect(parseHex('#ABC'), 0xAABBCC);
    });

    test('非法色值直接抛错（开发期就该炸，不要静默兜底）', () {
      expect(() => parseHex('#12345'), throwsFormatException);
      expect(() => parseHex('#GGGGGG'), throwsFormatException);
      expect(() => parseHex(''), throwsFormatException);
    });

    test('rgbOf 拆通道', () {
      expect(rgbOf(0xF7F5F0), (0xF7, 0xF5, 0xF0));
    });
  });

  group('三套主题 · 对比度（UI 规范 §2.1 / §6.3）', () {
    test('日间暖白：正文对比度 13.00:1（§2.1 已修正标注）', () {
      final ratio = contrastRatio(AppPalettes.day.textMain, AppPalettes.day.bg);

      // ✅ 2026-09-25 裁决（§八 #38）：原 §2.1 标注「≈11.5:1」是**算错**（WCAG 实算 13.00）；
      // 且原 §6.3 的「8:1 ~ 12:1」把 13.00 判成超上限 —— 该区间已**废止**。
      // 新口径：正文以 WCAG AA 为下限（≥4.5:1），**不设上限**。色值一个都没动。
      // 实测值钉在这里：色值一旦被改动就会红，逼改动者回到 §2.1 的口径注。
      expect(ratio, closeTo(13.0, 0.1));
      expect(ratio, greaterThanOrEqualTo(4.5), reason: '正文必须达 WCAG AA');
    });

    test('纸质护眼与夜间正文对比度均达 WCAG AA（≥4.5:1，不再设上限）', () {
      final paper = contrastRatio(AppPalettes.paper.textMain, AppPalettes.paper.bg);
      final night = contrastRatio(AppPalettes.night.textMain, AppPalettes.night.bg);
      expect(paper, closeTo(10.68, 0.1));
      expect(night, closeTo(11.41, 0.1));
      // 「≤12.0」的上限断言已随 §6.3 废止一并删除（12:1 上限无 WCAG 依据）。
      for (final ratio in [paper, night]) {
        expect(ratio, greaterThanOrEqualTo(4.5));
      }
    });

    test('夜间不是纯黑背景、也不是纯白文字（§2.3）', () {
      expect(AppPalettes.night.bg, isNot(0x000000), reason: '§2.3 明确不用纯黑');
      expect(AppPalettes.night.textMain, isNot(0xFFFFFF), reason: '§2.3 明确不用纯白');
    });

    test('三套主题的背景都不是纯白/纯黑（§2.5 配色禁忌）', () {
      for (final palette in AppPalettes.all.values) {
        expect(palette.bg, isNot(0xFFFFFF), reason: '${palette.id} 背景是纯白');
        expect(palette.bg, isNot(0x000000), reason: '${palette.id} 背景是纯黑');
      }
    });

    test('次要文字对比度达 WCAG AA（≥4.5:1）', () {
      // 实算：日间 4.89 / 纸质 4.61 / 夜间 6.35
      for (final palette in AppPalettes.all.values) {
        final ratio = contrastRatio(palette.textSub, palette.bg);
        expect(ratio, greaterThanOrEqualTo(4.5), reason: '${palette.id} 次要文字对比度不足');
      }
    });

    test('弱化文字（占位/说明）实算值记录 —— ⚠️ 低于 AA，规范已如实登记不视为达标', () {
      // 2026-09-25 裁决（§八 #39）：§2.1 给的 `textFaint` 三套实算 2.58 / 2.44 / 3.58，
      // **均低于 WCAG AA 的 4.5:1**。规范已把该情况**如实记入 §2.1 实测表（标 ⚠️）**
      // 并明确「不视为达标」；它只承载占位/说明这类非判读信息，故一期不阻断。
      // 若后续要用它承载关键信息，必须先调深色值 —— 那时这几条断言该改成 ≥4.5。
      final ratios = {
        AppThemeId.day: contrastRatio(AppPalettes.day.textFaint, AppPalettes.day.bg),
        AppThemeId.paper: contrastRatio(AppPalettes.paper.textFaint, AppPalettes.paper.bg),
        AppThemeId.night: contrastRatio(AppPalettes.night.textFaint, AppPalettes.night.bg),
      };
      expect(ratios[AppThemeId.day]!, closeTo(2.58, 0.1));
      expect(ratios[AppThemeId.paper]!, closeTo(2.44, 0.1));
      expect(ratios[AppThemeId.night]!, closeTo(3.58, 0.1));
      for (final ratio in ratios.values) {
        expect(ratio, greaterThan(2.0), reason: '再低就完全看不清了');
      }
    });

    test('日间卡片色值必须还是 #FFFFFF（§2.1 原文；正文 on 卡片 14.16:1）', () {
      // 2026-09-25 复核（§八 #40）：条目原文「卡片 #FFFFFF on 背景 #F7F5F0 实测 14.16:1」
      // **把数字挂错了对象** —— 14.16 是「正文 #2B2B2B on 卡片 #FFFFFF」；
      // 「卡片 on 背景」实值为 1.09:1，那是**相邻色块层次**，不是文字对比度，本就不该套 AA。
      // 正文 on 卡片 14.16:1 在新口径下完全合规（≥4.5、不设上限）。
      expect(AppPalettes.day.card, 0xFFFFFF);
    });

    test('夜间主题补齐了 5 个规范未定义的色值（不能是 0）', () {
      const night = AppPalettes.night;
      expect(night.primaryDark, isNot(0));
      expect(night.textFaint, isNot(0));
      expect(night.successLight, isNot(0));
      expect(night.errorLight, isNot(0));
      expect(night.hintLight, isNot(0));
    });
  });

  group('分龄字号与触控（§3.3 / §4.1）', () {
    test('年级 → 档位映射', () {
      expect(AgeProfile.ofGrade(1).band, AgeBand.junior);
      expect(AgeProfile.ofGrade(3).band, AgeBand.junior);
      expect(AgeProfile.ofGrade(4).band, AgeBand.middle);
      expect(AgeProfile.ofGrade(6).band, AgeBand.middle);
      expect(AgeProfile.ofGrade(7).band, AgeBand.senior);
      expect(AgeProfile.ofGrade(9).band, AgeBand.senior);
    });

    test('脏年级不崩，按边界 clamp', () {
      expect(AgeProfile.ofGrade(null).band, AgeBand.junior, reason: '没选过时字号最大最稳');
      expect(AgeProfile.ofGrade(0).band, AgeBand.junior);
      expect(AgeProfile.ofGrade(-3).band, AgeBand.junior);
      expect(AgeProfile.ofGrade(99).band, AgeBand.senior);
    });

    test('三档字号与 UI 规范 §3.3 表格逐项对齐', () {
      expect(AgeProfile.of(AgeBand.junior).body, 18);
      expect(AgeProfile.of(AgeBand.middle).body, 17);
      expect(AgeProfile.of(AgeBand.senior).body, 16);
      expect(AgeProfile.of(AgeBand.junior).reciteBig, 36);
      expect(AgeProfile.of(AgeBand.middle).reciteBig, 30);
      expect(AgeProfile.of(AgeBand.senior).reciteBig, 26);
      expect(AgeProfile.of(AgeBand.junior).wordBig, 40);
      expect(AgeProfile.of(AgeBand.middle).wordBig, 34);
      expect(AgeProfile.of(AgeBand.senior).wordBig, 30);
    });

    test('字号下限 16sp 不可突破', () {
      for (final profile in AgeProfile.all.values) {
        expect(profile.safeBody, greaterThanOrEqualTo(16));
        expect(profile.safeCaption, greaterThanOrEqualTo(16));
        expect(profile.body, greaterThanOrEqualTo(AgeProfile.minFontSize));
        expect(profile.caption, greaterThanOrEqualTo(AgeProfile.minFontSize));
      }
    });

    test('触控下限 48dp 不可突破（含高年级次级按钮）', () {
      for (final profile in AgeProfile.all.values) {
        expect(profile.primaryButtonHeight, greaterThanOrEqualTo(48));
        expect(profile.secondaryButtonHeight, greaterThanOrEqualTo(48));
        expect(profile.iconButtonSize, greaterThanOrEqualTo(48));
        expect(profile.safePrimaryHeight, greaterThanOrEqualTo(48));
        expect(profile.safeIconSize, greaterThanOrEqualTo(48));
      }
      // 高年级次级按钮正好是下限，钉住它防止被继续调小
      expect(AgeProfile.of(AgeBand.senior).secondaryButtonHeight, 48);
    });

    test('课文背诵行高 ≥2.0（否则逐字点亮会糊）', () {
      expect(AgeProfile.reciteLineHeight, greaterThanOrEqualTo(2.0));
      for (final profile in AgeProfile.all.values) {
        expect(profile.reciteStyle.height, greaterThanOrEqualTo(2.0));
      }
    });

    test('字重只用 400 / 500（§3.2）', () {
      for (final profile in AgeProfile.all.values) {
        expect(profile.bodyStyle.fontWeight, FontWeight.w400);
        expect(profile.cardTitleStyle.fontWeight, FontWeight.w500);
        expect(profile.reciteStyle.fontWeight, FontWeight.w400);
      }
    });
  });

  group('自动暖色与主题解析（§2.4 / §6.2）', () {
    DateTime at(int hour, [int minute = 0]) =>
        DateTime(2026, 9, 24, hour, minute);

    test('暖色时段：19:00 ~ 次日 06:59 一律夜间', () {
      for (final hour in [19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5, 6]) {
        final id = resolveThemeId(
          mode: AppThemeMode.day, // 就算手动选了日间，也要被按到夜间
          autoWarm: true,
          platformBrightness: Brightness.light,
          now: at(hour),
        );
        expect(id, AppThemeId.night, reason: '$hour 点应该自动用夜间');
      }
    });

    test('暖色时段边界', () {
      expect(
        resolveThemeId(
          mode: AppThemeMode.day,
          autoWarm: true,
          platformBrightness: Brightness.light,
          now: at(6, 59),
        ),
        AppThemeId.night,
      );
      expect(
        resolveThemeId(
          mode: AppThemeMode.system,
          autoWarm: true,
          platformBrightness: Brightness.light,
          now: at(7, 0),
        ),
        AppThemeId.day,
      );
      expect(
        resolveThemeId(
          mode: AppThemeMode.system,
          autoWarm: true,
          platformBrightness: Brightness.light,
          now: at(18, 59),
        ),
        AppThemeId.day,
      );
    });

    test('关掉自动暖色后，白天按手动选择走', () {
      expect(
        resolveThemeId(
          mode: AppThemeMode.paper,
          autoWarm: false,
          platformBrightness: Brightness.light,
          now: at(21),
        ),
        AppThemeId.paper,
      );
      expect(
        resolveThemeId(
          mode: AppThemeMode.night,
          autoWarm: false,
          platformBrightness: Brightness.light,
          now: at(12),
        ),
        AppThemeId.night,
      );
    });

    test('跟随系统：白天浅色用日间，系统深色用夜间', () {
      expect(
        resolveThemeId(
          mode: AppThemeMode.system,
          autoWarm: false,
          platformBrightness: Brightness.light,
          now: at(12),
        ),
        AppThemeId.day,
      );
      expect(
        resolveThemeId(
          mode: AppThemeMode.system,
          autoWarm: false,
          platformBrightness: Brightness.dark,
          now: at(12),
        ),
        AppThemeId.night,
      );
    });

    test('isWarmHour 与常量一致', () {
      expect(kAutoWarmStartHour, 19);
      expect(kAutoWarmEndHour, 7);
      expect(isWarmHour(at(19)), isTrue);
      expect(isWarmHour(at(6, 59)), isTrue);
      expect(isWarmHour(at(7)), isFalse);
      expect(isWarmHour(at(18, 59)), isFalse);
    });

    test('主题模式名可往返解析', () {
      for (final mode in AppThemeMode.values) {
        expect(AppThemeMode.parse(mode.name), mode);
      }
      expect(AppThemeMode.parse(null), AppThemeMode.system);
      expect(AppThemeMode.parse('不存在的模式'), AppThemeMode.system);
    });
  });

  group('主题构建', () {
    test('三套调色板都能构建出 ThemeData 且带扩展色', () {
      for (final palette in AppPalettes.all.values) {
        final theme = AppTheme.build(palette: palette);
        expect(theme.colorScheme.primary, palette.primaryColor);
        expect(theme.scaffoldBackgroundColor, palette.bgColor);
        expect(theme.extension<AppExtraColors>(), isNotNull);
      }
    });

    test('夜间主题 brightness 为 dark', () {
      final theme = AppTheme.build(palette: AppPalettes.night);
      expect(theme.colorScheme.brightness, Brightness.dark);
    });

    test('切换动画时长 200ms（§5.1 规定 200–300ms）', () {
      expect(AppTheme.switchDuration.inMilliseconds, 200);
    });
  });

  group('格式化工具', () {
    test('计时格式化', () {
      expect(formatClock(0), '0:00');
      expect(formatClock(9), '0:09');
      expect(formatClock(75), '1:15');
      expect(formatClock(-5), '0:00');
    });

    test('预计用时：不足 1 分钟不出现「0 分钟」', () {
      expect(formatEstimate(0), '不到 1 分钟');
      expect(formatEstimate(30), '不到 1 分钟');
      expect(formatEstimate(60), '约 1 分钟');
      expect(formatEstimate(300), '约 5 分钟');
      expect(formatEstimate(3600), '约 1 小时');
      expect(formatEstimate(5400), '约 1 小时 30 分钟');
    });

    test('分数与百分比取整', () {
      expect(formatScore(83.3333), '83');
      expect(formatPercent(0.666), '67%');
    });
  });
}
