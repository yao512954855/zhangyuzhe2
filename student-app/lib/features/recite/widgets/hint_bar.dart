/// 三档提示条 —— WP-APP-04 F6 / F7 / F13（UI 规范 §7.7）。
///
/// 渲染规则（与契约、后端 `engine.hint_text` 严格一致）：
/// - **一档**（[ReciteHint.level]==1）：服务端返回 `"_"×剩余字数`
///   → 渲染成一排**空框**（"后面还有 N 个字"），不显示任何答案；
/// - **二档**（level==2）：服务端返回**下一个字** → 直接显示该字；
/// - **三档**（level==3）：服务端返回「下一个字 + 其后若干字」窗口 → 显示该文本。
///
/// ⚠️ 客户端**只渲染服务端下发的 `content`**，不自行拼接答案
/// （契约 §13 禁止客户端自对齐，提示文案口径也以服务端为准）。
/// 规范 §5.7 写"三档=完整答案"，而后端实现给的是「下一个字 + 4 字窗口」，
/// 这处口径差异见交付说明偏离项 D-APP04-02；本组件按服务端实际返回渲染，
/// 无论后端将来改成"完整答案"还是维持窗口，UI 都不用动。
///
/// 双通道约束（UI 规范 §2.5）：提示用「灯泡图标 + 颜色 + 文案」，不靠颜色单独表意。
library;

import 'package:flutter/material.dart';

import '../../../core/theme/age_profile.dart';
import '../../../core/theme/app_theme.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/feedback.dart';
import '../recite_controller.dart';

/// 三档提示展示条。
class HintBar extends StatelessWidget {
  const HintBar({
    super.key,
    this.hint,
    this.usedCount = 0,
    this.onStuck,
    this.onSettings,
  });

  /// 最近一次服务端下发的提示（null = 此刻没有提示）。
  final ReciteHint? hint;

  /// 累计已用提示次数（来自 [ReciteProgress.hintTotal]），用于"界面显示当前已用提示次数"。
  final int usedCount;

  /// 「我卡住了」按钮（手动降一档）。
  final VoidCallback? onStuck;

  /// 麦克风被永久拒绝时出现的「去设置」入口（可选）。
  final VoidCallback? onSettings;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    final children = <Widget>[];

    if (hint != null && hint!.level > 0) {
      children.add(_HintContent(hint: hint!));
    }

    // 已用提示次数（F7 明文：界面要显示）。
    children.add(
      Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Text(
          '已用提示 $usedCount 次',
          style: profile.captionStyle.copyWith(color: extra.textFaint),
        ),
      ),
    );

    // 「我卡住了」主降档入口（手动触发，F7）。
    if (onStuck != null) {
      children.add(
        Padding(
          padding: const EdgeInsets.only(top: 10),
          child: AppButton(
            label: '我卡住了',
            kind: AppButtonKind.secondary,
            icon: Icons.lightbulb_outline_rounded,
            onPressed: onStuck,
          ),
        ),
      );
    }

    // 麦克风被永久拒绝 → 给一条去设置的说明（F10：识别失败要反馈，不能静默）。
    if (onSettings != null) {
      children.add(
        Padding(
          padding: const EdgeInsets.only(top: 10),
          child: InlineNotice(
            kind: NoticeKind.error,
            text: '麦克风还没打开，去设置里允许一下，就能继续背了。',
            onClose: onSettings,
          ),
        ),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: children,
    );
  }
}

/// 把一条提示渲染成「图标 + 文案」（双通道）。
class _HintContent extends StatelessWidget {
  const _HintContent({required this.hint});

  final ReciteHint hint;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;

    final (String caption, Widget body) = switch (hint.level) {
      1 => (
          '还有 ${_boxCount(hint.content)} 个字要背',
          _BoxRow(content: hint.content ?? ''),
        ),
      2 => (
          '下一个字是',
          Text(
            hint.content ?? '',
            style: profile.reciteStyle.copyWith(
              color: palette.primary,
              fontWeight: FontWeight.w500,
            ),
          ),
        ),
      _ => (
          '接着背这几个字',
          Text(
            hint.content ?? '',
            style: profile.reciteStyle.copyWith(
              color: palette.primary,
              fontWeight: FontWeight.w500,
            ),
          ),
        ),
    };

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: extra.hintLight,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: extra.hint.withValues(alpha: 0.35), width: 0.5),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.lightbulb_rounded, size: profile.caption + 6, color: extra.hint),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  caption,
                  style: profile.captionStyle.copyWith(
                    color: extra.hint,
                    fontWeight: FontWeight.w500,
                  ),
                ),
                const SizedBox(height: 8),
                body,
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// 一档提示里下划线字符的数量（即"剩余字数"）。
  static int _boxCount(String? content) {
    if (content == null || content.isEmpty) return 0;
    return content.runes.where((r) => String.fromCharCode(r) == '_').length;
  }
}

/// 一档提示的「一排空框」：把下划线渲染成小格子，让学生对"还剩多少"有直观感受。
class _BoxRow extends StatelessWidget {
  const _BoxRow({required this.content});

  final String content;

  @override
  Widget build(BuildContext context) {
    final palette = Theme.of(context).colorScheme;
    final boxes = <Widget>[];
    for (final ch in content.runes) {
      final s = String.fromCharCode(ch);
      if (s == '_') {
        boxes.add(
          Container(
            width: 22,
            height: 28,
            margin: const EdgeInsets.only(right: 4, bottom: 4),
            decoration: BoxDecoration(
              border: Border.all(color: palette.primary.withValues(alpha: 0.6), width: 1.5),
              borderRadius: BorderRadius.circular(6),
            ),
          ),
        );
      } else if (s.trim().isNotEmpty) {
        boxes.add(
          Padding(
            padding: const EdgeInsets.only(right: 4, bottom: 4),
            child: Text(s, style: const TextStyle(fontSize: 22)),
          ),
        );
      }
    }
    return Wrap(children: boxes);
  }
}
