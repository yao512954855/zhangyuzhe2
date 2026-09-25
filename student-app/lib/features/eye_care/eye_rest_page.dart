/// 用眼休息提醒页 —— UI 规范 §6.1（U12）。
///
/// 硬要求逐条落地：
/// - 全屏**柔和背景**，不用红色警告样式；
/// - 文案温和，不训人；
/// - 20 秒倒计时（20-20-20 法则），配**缓慢的呼吸圆**；
/// - 学生可点「我休息好了」**提前结束**，**不强制锁定**（避免引发逆反）；
/// - 附带「坐直一点哦」的温和提示（§6.4）。
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/eye_care/eye_care_service.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';

/// 弹出休息提醒（到点由 `EyeCareService` 触发）。
Future<void> showEyeRest(BuildContext context) {
  return showDialog<void>(
    context: context,
    // 不锁屏、不禁止返回：规范明确"不强制"
    barrierDismissible: true,
    builder: (_) => const EyeRestPage(),
  );
}

/// 休息提醒内容。
class EyeRestPage extends StatefulWidget {
  const EyeRestPage({super.key, this.restSeconds = EyeCareService.restCountdownSeconds});

  final int restSeconds;

  @override
  State<EyeRestPage> createState() => _EyeRestPageState();
}

class _EyeRestPageState extends State<EyeRestPage>
    with SingleTickerProviderStateMixin {
  late int _left = widget.restSeconds;
  Timer? _timer;

  late final AnimationController _breath = AnimationController(
    vsync: this,
    // 4 秒一次呼吸：慢到不会引起不适，远低于 §5.1 的 3Hz 闪烁红线
    duration: const Duration(seconds: 4),
  )..repeat(reverse: true);

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) {
        return;
      }
      setState(() => _left -= 1);
      if (_left <= 0) {
        _done();
      }
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    _breath.dispose();
    super.dispose();
  }

  void _done() {
    _timer?.cancel();
    if (mounted) {
      Navigator.of(context).maybePop();
    }
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final primary = Theme.of(context).colorScheme.primary;

    return Dialog(
      insetPadding: const EdgeInsets.all(20),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            AnimatedBuilder(
              animation: _breath,
              builder: (context, _) {
                final size = 96 + _breath.value * 28;
                return Container(
                  width: size,
                  height: size,
                  decoration: BoxDecoration(
                    color: primary.withValues(alpha: 0.14),
                    shape: BoxShape.circle,
                  ),
                  child: Center(
                    child: Icon(Icons.remove_red_eye_outlined, size: 40, color: primary),
                  ),
                );
              },
            ),
            const SizedBox(height: 22),
            Text(
              '已经练了一会啦',
              style: profile.cardTitleStyle,
            ),
            const SizedBox(height: 8),
            Text(
              '看看窗外，让眼睛歇一歇吧',
              textAlign: TextAlign.center,
              style: profile.bodyStyle.copyWith(color: extra.textSub),
            ),
            const SizedBox(height: 18),
            Text(
              '$_left',
              style: profile.reciteStyle.copyWith(
                color: primary,
                fontFeatures: const [FontFeature.tabularFigures()],
              ),
            ),
            const SizedBox(height: 6),
            Text(
              '慢慢数到 0 就好',
              style: profile.captionStyle.copyWith(color: extra.textFaint),
            ),
            const SizedBox(height: 20),
            // 坐姿提醒（§6.4）：温和的一句话，不做弹窗级别的强调
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.accessibility_new_rounded, size: 18, color: extra.textFaint),
                const SizedBox(width: 6),
                Text(
                  '顺便坐直一点哦',
                  style: profile.captionStyle.copyWith(color: extra.textFaint),
                ),
              ],
            ),
            const SizedBox(height: 22),
            AppButton(
              label: '我休息好了',
              onPressed: _done,
            ),
          ],
        ),
      ),
    );
  }
}
