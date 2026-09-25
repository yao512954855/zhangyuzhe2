/// 提示按钮（**英语三档提示，WP-2J / 契约 §15.4**）—— F12。
///
/// 二期新增独立 hint 端点，二档（首字母）/ 三档（完整答案）由服务端按失败次数
/// 门控下发（组卷响应 §9.1 仍绝不返回答案）。本按钮**服务端驱动**渲染：
/// - `hintContent`：服务端下发的提示文案（覆盖一/二/三档），非空即展示；
/// - `exhausted`：当前门控下是否已无更高档可升，**为 true 时按钮禁用**；
/// - 兼容旧调用方（不传 `hintContent`）：回退用 `placeholderCount` 兜底渲染一档。
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
    this.hintContent,
    this.exhausted = false,
  });

  /// 当前已升到的档（0 = 未用）。来自控制器 `state.hintLevel`。
  final int hintLevel;

  /// 一档占位框数量（本地已知，旧调用方兜底渲染用）。
  final int placeholderCount;

  /// 点击「提示一档」回调（控制器 `escalateHint`）。
  final VoidCallback onHint;

  /// 服务端下发的提示文案（null = 尚未取到提示）。
  final String? hintContent;

  /// 当前门控下是否已无更高档可升；为 true 时按钮置灰。
  final bool exhausted;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    // 已达上限（3 档）或当前门控用尽 → 不可再点。
    final canEscalate = !exhausted && hintLevel < kEnglishMaxHintLevel;
    final label = hintLevel == 0 ? '我卡住了 / 提示' : '再提示一档';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AppButton(
          label: label,
          kind: AppButtonKind.secondary,
          icon: Icons.lightbulb_outline_rounded,
          onPressed: canEscalate ? onHint : null,
        ),
        if (hintContent != null) ...[
          const SizedBox(height: AgeProfile.elementGap),
          InlineNotice(kind: NoticeKind.hint, text: hintContent!),
        ] else if (hintLevel > 0) ...[
          // 兼容旧调用方：用占位框数量兜底显示一档提示。
          const SizedBox(height: AgeProfile.elementGap),
          InlineNotice(
            kind: NoticeKind.hint,
            text: placeholderCount > 0
                ? '已给你一档提示：这个词有 $placeholderCount 个字母'
                : '已给你一档提示',
          ),
        ],
        if (exhausted && hintLevel > 0) ...[
          const SizedBox(height: AgeProfile.elementGap),
          InlineNotice(
            kind: NoticeKind.hint,
            text: hintLevel < kEnglishMaxHintLevel
                ? '当前提示已用完，先再试一次也许能解锁下一档'
                : '三档提示都已用完',
          ),
        ],
        // 占用一行提示文案高度，避免内容出现时布局跳动。
        if (hintLevel == 0 && hintContent == null) SizedBox(height: profile.caption + 8),
      ],
    );
  }
}
