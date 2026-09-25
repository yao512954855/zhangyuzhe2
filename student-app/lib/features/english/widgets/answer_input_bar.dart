/// 键盘兜底入口条 —— F8 / U7。
///
/// 「改用键盘」**常驻但视觉弱化**（不做主按钮，用 text 按钮）：
/// - 默认不主动引导，只在语音不可用 / 连续失败时才高亮提示；
/// - 任意时刻点都能立即切键盘（U7：入口无需滚动即可见）。
library;

import 'package:flutter/material.dart';

import '../../../core/widgets/app_button.dart';

/// 常驻的「改用键盘」入口（弱化展示）。
class AnswerInputBar extends StatelessWidget {
  const AnswerInputBar({super.key, required this.onUseKeyboard});

  final VoidCallback onUseKeyboard;

  @override
  Widget build(BuildContext context) {
    return AppButton(
      label: '改用键盘',
      kind: AppButtonKind.text,
      onPressed: onUseKeyboard,
    );
  }
}
