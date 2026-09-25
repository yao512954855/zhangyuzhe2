/// 录音权限引导 —— 30-APP-01 F2 / U11。
///
/// **关键约束：被拒后不能反复弹系统窗**。系统权限框弹第二次时
/// Android 会直接静默拒绝，学生只会觉得"点了没反应"。
/// 所以这里只做两件事：
/// ① 本次可再问 → 给一个明确按钮（学生主动点才请求）；
/// ② 已被永久拒绝 → 直接引导去系统设置，不再尝试 `requestPermission()`。
///
/// 练习页/背诵页（APP-03 / APP-04）遇到权限不足时直接嵌入本组件即可。
library;

import 'package:flutter/material.dart';

import '../audio/pcm_recorder.dart';
import '../theme/age_profile.dart';
import '../theme/app_theme.dart';
import 'app_button.dart';

/// 权限不足时的引导视图。
class MicPermissionView extends StatelessWidget {
  const MicPermissionView({
    super.key,
    required this.permission,
    required this.onAskAgain,
    required this.onOpenSettings,
    this.onSkip,
  });

  final MicPermission permission;

  /// 再请求一次（会弹系统框）。
  final VoidCallback onAskAgain;

  /// 打开系统设置页。
  final Future<void> Function() onOpenSettings;

  /// 可选：先用键盘继续（**降级兜底，不主动引导**）。
  final VoidCallback? onSkip;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final permanently = permission == MicPermission.permanentlyDenied;

    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AgeProfile.screenEdgeInset * 2),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Icon(Icons.mic_off_rounded, size: 56, color: extra.textFaint),
            const SizedBox(height: AgeProfile.elementGap),
            Text(
              permanently ? '麦克风被关掉了' : '还差一个麦克风',
              textAlign: TextAlign.center,
              style: profile.cardTitleStyle,
            ),
            const SizedBox(height: 8),
            Text(
              permanently
                  ? '去手机的设置里把麦克风打开，就能继续练习啦'
                  : '要用麦克风读单词才能练习哦，点下面的按钮允许一下',
              textAlign: TextAlign.center,
              style: profile.captionStyle.copyWith(color: extra.textSub),
            ),
            const SizedBox(height: 24),
            if (!permanently)
              AppButton(label: '允许使用麦克风', onPressed: onAskAgain)
            else
              AppButton(
                label: '去设置里打开',
                icon: Icons.settings_rounded,
                onPressed: () => onOpenSettings(),
              ),
            if (onSkip != null) ...[
              const SizedBox(height: AgeProfile.adjacentButtonGap),
              AppButton(
                label: '先用打字的方式',
                kind: AppButtonKind.text,
                onPressed: onSkip,
              ),
            ],
          ],
        ),
      ),
    );
  }
}
