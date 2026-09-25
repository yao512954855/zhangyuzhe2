/// 数字步进器 —— 配置页选「前 N 个 / 随机 N 个」与自定义区间用。
///
/// 为什么不用下拉框或输入框：
/// - 下拉框被 UI 规范 P5 明令禁止；
/// - 输入框会让低年级学生**调出键盘**，一屏被盖掉一半，
///   而且他们很容易输成 0、负数、或者把字数打进数量里。
/// 加减号一步一个值，出错也能立刻看出来。
///
/// 两个按钮都受 §4.1 的 48dp 触控下限保护。
library;

import 'package:flutter/material.dart';

import '../../../core/theme/age_profile.dart';
import '../../../core/theme/app_theme.dart';

class NumberStepper extends StatelessWidget {
  const NumberStepper({
    super.key,
    required this.value,
    required this.onChanged,
    this.min = 1,
    this.max = 1,
    this.unit = '个',
    this.caption,
  });

  final int value;
  final ValueChanged<int> onChanged;
  final int min;
  final int max;
  final String unit;

  /// 左侧说明（如「从第」「到第」）。
  final String? caption;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;
    final canDec = value > min;
    final canInc = value < max;

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (caption != null) ...[
          Text(caption!, style: profile.bodyStyle.copyWith(color: extra.textSub)),
          const SizedBox(width: 8),
        ],
        _StepButton(
          icon: Icons.remove_rounded,
          enabled: canDec,
          onTap: () => onChanged(value - 1),
          tooltip: '减少',
        ),
        Container(
          constraints: BoxConstraints(minWidth: 76),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: extra.card,
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: extra.divider, width: 0.5),
          ),
          // 数值用大一号的字，学生一眼能看清自己选了几个
          child: Text(
            '$value$unit',
            style: profile.cardTitleStyle.copyWith(color: palette.primary),
          ),
        ),
        _StepButton(
          icon: Icons.add_rounded,
          enabled: canInc,
          onTap: () => onChanged(value + 1),
          tooltip: '增加',
        ),
      ],
    );
  }
}

class _StepButton extends StatelessWidget {
  const _StepButton({
    required this.icon,
    required this.enabled,
    required this.onTap,
    required this.tooltip,
  });

  final IconData icon;
  final bool enabled;
  final VoidCallback onTap;
  final String tooltip;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;
    final side = profile.safeIconSize;

    // 长按能看提示（低龄学生不一定知道「−」是什么意思）；
    // 语义由外层 Semantics 给，这里不再重复朗读。
    return Tooltip(
      message: tooltip,
      excludeFromSemantics: true,
      child: Semantics(
        button: true,
        enabled: enabled,
        label: tooltip,
        child: SizedBox(
          width: side,
          height: side,
          child: Material(
            color: enabled ? extra.card : extra.divider.withValues(alpha: 0.3),
            borderRadius: BorderRadius.circular(10),
            child: InkWell(
              borderRadius: BorderRadius.circular(10),
              onTap: enabled ? onTap : null,
              child: Icon(
                icon,
                size: side * 0.5,
                color: enabled ? palette.primary : extra.textFaint,
              ),
            ),
          ),
        ),
      ),
    );
  }
}
