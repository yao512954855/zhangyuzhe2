/// 键盘输入面板 —— F8 / U13 降级路径。
///
/// 仅在语音不可用 / 连续失败时才出现（非主路径）。提交走
/// `POST /api/sessions/{id}/answers`，`used_keyboard=1` 由控制器上报。
library;

import 'package:flutter/material.dart';

import '../../../core/theme/age_profile.dart';
import '../../../core/widgets/app_button.dart';

/// 键盘输入面板。
class KeyboardInputPanel extends StatefulWidget {
  const KeyboardInputPanel({
    super.key,
    required this.onSubmit,
    this.onCancel,
    this.hint,
  });

  /// 提交（带学生打出的文本）。
  final void Function(String text) onSubmit;

  /// 取消（回到语音）。
  final VoidCallback? onCancel;

  /// 题目提示（如「看中文，说英文」），用于占位文案。
  final String? hint;

  @override
  State<KeyboardInputPanel> createState() => _KeyboardInputPanelState();
}

class _KeyboardInputPanelState extends State<KeyboardInputPanel> {
  final TextEditingController _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    final text = _controller.text.trim();
    if (text.isEmpty) return;
    widget.onSubmit(text);
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: _controller,
          autofocus: true,
          textAlign: TextAlign.center,
          style: profile.wordStyle,
          decoration: InputDecoration(
            hintText: widget.hint == null ? '在这里打字' : '${widget.hint}：在这里打字',
            hintStyle: profile.captionStyle,
          ),
          onSubmitted: (_) => _submit(),
        ),
        const SizedBox(height: AgeProfile.elementGap),
        AppButton(label: '提交', onPressed: _submit),
        if (widget.onCancel != null) ...[
          const SizedBox(height: AgeProfile.adjacentButtonGap),
          AppButton(label: '还是用麦克风', kind: AppButtonKind.text, onPressed: widget.onCancel),
        ],
      ],
    );
  }
}
