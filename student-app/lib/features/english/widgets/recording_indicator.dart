/// 录音动效 —— F4。
///
/// **明显的呼吸圆**，让学生知道在听。硬约束：
/// - 周期 ≥ 1.5s（UI 规范 §5.1：禁 >3Hz 闪烁，呼吸动效也要慢）；
/// - 只做**平滑的缩放 + 透明度**过渡，从不明暗突变（不闪）；
/// - 不依赖颜色单独表意（色盲友好），圆本身是形状信号。
library;

import 'package:flutter/material.dart';

import '../../../core/theme/app_theme.dart';
import '../../../core/theme/age_profile.dart';

/// 呼吸圆录音指示。
class RecordingIndicator extends StatefulWidget {
  const RecordingIndicator({super.key, this.label = '正在听…'});

  final String label;

  @override
  State<RecordingIndicator> createState() => _RecordingIndicatorState();
}

class _RecordingIndicatorState extends State<RecordingIndicator>
    with SingleTickerProviderStateMixin {
  // 1.6s 一个来回：远低于 3Hz 闪烁上限，且过渡平滑不刺眼。
  static const Duration _period = Duration(milliseconds: 1600);

  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: _period,
  )..repeat(reverse: true);

  late final Animation<double> _scale = Tween<double>(begin: 0.82, end: 1.08).animate(
    CurvedAnimation(parent: _controller, curve: Curves.easeInOut),
  );
  late final Animation<double> _opacity = Tween<double>(begin: 0.55, end: 1.0).animate(
    CurvedAnimation(parent: _controller, curve: Curves.easeInOut),
  );

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final palette = Theme.of(context).colorScheme;
    final extra = AppExtraColors.of(context);
    final diameter = profile.safeIconSize + 40;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        RepaintBoundary(
          child: AnimatedBuilder(
            animation: _controller,
            builder: (context, _) => Opacity(
              opacity: _opacity.value,
              child: Transform.scale(
                scale: _scale.value,
                child: Container(
                  width: diameter,
                  height: diameter,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: palette.primary.withValues(alpha: 0.18),
                    border: Border.all(color: palette.primary.withValues(alpha: 0.6), width: 2),
                  ),
                  child: Icon(Icons.mic_rounded, size: profile.safeIconSize * 0.6, color: palette.primary),
                ),
              ),
            ),
          ),
        ),
        const SizedBox(height: AgeProfile.elementGap),
        Text(widget.label, style: profile.captionStyle.copyWith(color: extra.textSub)),
      ],
    );
  }
}
