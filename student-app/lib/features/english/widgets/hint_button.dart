/// 提示按钮 + 三档提示内容 —— F12。
///
/// 卡壳时（静音 8 秒或主动点）给三档提示：
/// - 一档：显示占位框数量（客户端已知 `target_len`，本地可渲染）；
/// - 二档 / 三档：首字母 / 完整答案 —— **需服务端下发答案**，但 §9 无英语 hint
///   端点、且 §9.1 明文「组卷响应绝不含答案」，客户端无法本地渲染
///   （偏离项 D-APP03-02）。这里如实渲染「已升到第几档」并保留占位，待服务端端点补齐。
///
/// 每用一次提示都计入 `usedHintCount`，随答案上报（影响得分，契约 §9.2）。
library;

import 'package:flutter/material.dart';

import '../../../core/theme/age_profile.dart';
import '../../../core/widgets/app_button.dart';
import '../../../core/widgets/feedback.dart';

/// 提示按钮与已升档的提示内容。
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

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AppButton(
          label: hintLevel == 0 ? '我卡住了 / 提示' : '再提示一档',
          kind: AppButtonKind.secondary,
          icon: Icons.lightbulb_outline_rounded,
          onPressed: hintLevel >= 3 ? null : onHint,
        ),
        if (hintLevel > 0) ...[
          const SizedBox(height: AgeProfile.elementGap),
          InlineNotice(
            kind: NoticeKind.hint,
            text: placeholderCount > 0
                ? '已给你第 $hintLevel 档提示：这个词有 $placeholderCount 个字母'
                : '已给你第 $hintLevel 档提示',
          ),
        ],
        // 占用一行提示文案高度，避免内容出现时布局跳动。
        if (hintLevel == 0) SizedBox(height: profile.caption + 8),
      ],
    );
  }
}
