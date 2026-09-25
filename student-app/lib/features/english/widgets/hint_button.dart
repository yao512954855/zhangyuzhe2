/// 提示按钮（**英语一期只有一档**）—— F12。
///
/// 2026-09-25 裁决 **B**（见需求 §5.7「学科范围」、`docs/dev/02-实现约定与复用经验.md` §八 #71）：
/// 需求 §5.7 的三档提示**划归语文**，英语侧只剩**一档**可用：
/// - 一档：显示占位框数量（客户端已知 `target_len`，本地可渲染）；
/// - 二档（首字母）/ 三档（完整答案）：**需服务端下发答案侧信息**，但 §9.1 明文
///   「组卷响应**绝不返回答案**」、且 hint 端点只存在于 §9.4 的**语文**背诵通道
///   → 英语侧**无来源，一期不做**。
///
/// 因此按钮在 `kEnglishMaxHintLevel`（= 1）档后**置灰**——
/// **不再出现「再提示一档」这种点了给不出内容的假入口**（原先的虚指文案已删）。
///
/// 每用一次提示都计入 `usedHintCount`，随答案上报（影响得分，契约 §9.2）。
library;

import 'package:flutter/material.dart';

import '../../../core/theme/age_profile.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/feedback.dart';
import '../practice_controller.dart';

/// 提示按钮与已给出的提示内容。
class HintButton extends StatelessWidget {
  const HintButton({
    super.key,
    required this.hintLevel,
    required this.placeholderCount,
    required this.onHint,
  });

  final int hintLevel;
  final int placeholderCount;
  final VoidCallback onHint;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    // 英语一期封顶一档：到顶之后没有更多可给的内容，按钮就必须真的不可点。
    final canEscalate = hintLevel < kEnglishMaxHintLevel;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AppButton(
          label: '我卡住了 / 提示',
          kind: AppButtonKind.secondary,
          icon: Icons.lightbulb_outline_rounded,
          onPressed: canEscalate ? onHint : null,
        ),
        if (hintLevel > 0) ...[
          const SizedBox(height: AgeProfile.elementGap),
          InlineNotice(
            kind: NoticeKind.hint,
            text: placeholderCount > 0
                ? '已给你一档提示：这个词有 $placeholderCount 个字母'
                : '已给你一档提示',
          ),
        ],
        // 占用一行提示文案高度，避免内容出现时布局跳动。
        if (hintLevel == 0) SizedBox(height: profile.caption + 8),
      ],
    );
  }
}
