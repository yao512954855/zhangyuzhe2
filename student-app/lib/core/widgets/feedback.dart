/// 反馈组件 —— UI 规范 §5.2 / §8.4 + F19。
///
/// 三条硬约束：
/// 1. **不出现红屏/英文异常**（F19）——所有错误都收敛到 [ErrorView] 的中文文案；
/// 2. **答错不弹模态框**，用 [InlineNotice] 内联反馈（§5.2）；
/// 3. **颜色 + 图标双通道**：红绿同时大面积使用会让色盲学生分不清（§2.5），
///    所以对/错一律带图标，不靠颜色单独表意。
library;

import 'package:flutter/material.dart';

import '../net/api_exception.dart';
import '../theme/age_profile.dart';
import '../theme/app_theme.dart';
import 'app_button.dart';

/// 加载中：文案要口语化，别写「加载中…」这种成人词。
class LoadingView extends StatelessWidget {
  const LoadingView({super.key, this.message = '马上就好…'});

  final String message;

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    final profile = AgeScope.of(context);
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(
            width: 36,
            height: 36,
            child: CircularProgressIndicator(
              strokeWidth: 3,
              color: Theme.of(context).colorScheme.primary,
            ),
          ),
          const SizedBox(height: AgeProfile.elementGap),
          Text(message, style: profile.captionStyle.copyWith(color: extra.textSub)),
        ],
      ),
    );
  }
}

/// 错误页：中文提示 + 重试。
class ErrorView extends StatelessWidget {
  const ErrorView({
    super.key,
    required this.message,
    this.onRetry,
    this.retryLabel = '再试一次',
    this.icon = Icons.cloud_off_rounded,
  });

  /// 直接用 [ApiException] 构造（带 trace_id 的编号一并上屏）。
  ErrorView.fromException(
    ApiException exception, {
    super.key,
    this.onRetry,
    this.retryLabel = '再试一次',
    this.icon = Icons.cloud_off_rounded,
  }) : message = exception.displayText;

  final String message;
  final VoidCallback? onRetry;
  final String retryLabel;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    final profile = AgeScope.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AgeProfile.screenEdgeInset * 2,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 48, color: extra.textFaint),
            const SizedBox(height: AgeProfile.elementGap),
            Text(
              message,
              textAlign: TextAlign.center,
              style: profile.bodyStyle.copyWith(color: extra.textSub),
            ),
            if (onRetry != null) ...[
              const SizedBox(height: AgeProfile.elementGap * 1.5),
              SizedBox(
                width: 200,
                child: AppButton(
                  label: retryLabel,
                  onPressed: onRetry,
                  kind: AppButtonKind.secondary,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// 空状态（还没有内容、还没有记录）。
class EmptyView extends StatelessWidget {
  const EmptyView({
    super.key,
    required this.title,
    this.hint,
    this.action,
    this.icon = Icons.inbox_rounded,
  });

  final String title;
  final String? hint;
  final Widget? action;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    final profile = AgeScope.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AgeProfile.screenEdgeInset * 2),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 48, color: extra.textFaint),
            const SizedBox(height: AgeProfile.elementGap),
            Text(
              title,
              textAlign: TextAlign.center,
              style: profile.cardTitleStyle.copyWith(color: extra.textSub),
            ),
            if (hint != null) ...[
              const SizedBox(height: 8),
              Text(
                hint!,
                textAlign: TextAlign.center,
                style: profile.captionStyle.copyWith(color: extra.textFaint),
              ),
            ],
            if (action != null) ...[
              const SizedBox(height: AgeProfile.elementGap * 1.5),
              action!,
            ],
          ],
        ),
      ),
    );
  }
}

/// 内联提示的语义。
enum NoticeKind { success, error, hint, info }

/// 内联提示：**不弹模态框**，就地出现在内容流里（UI 规范 §5.2）。
class InlineNotice extends StatelessWidget {
  const InlineNotice({
    super.key,
    required this.text,
    this.kind = NoticeKind.info,
    this.onClose,
  });

  final String text;
  final NoticeKind kind;
  final VoidCallback? onClose;

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    final profile = AgeScope.of(context);

    final (Color fg, Color bg, IconData icon) = switch (kind) {
      NoticeKind.success => (extra.success, extra.successLight, Icons.check_circle_rounded),
      NoticeKind.error => (extra.error, extra.errorLight, Icons.info_rounded),
      NoticeKind.hint => (extra.hint, extra.hintLight, Icons.lightbulb_rounded),
      NoticeKind.info => (extra.textSub, extra.card, Icons.info_outline_rounded),
    };

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: fg.withValues(alpha: 0.35), width: 0.5),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 图标是"双通道"里的第二通道：只给颜色的话色盲学生分不出对错
          Icon(icon, size: profile.caption + 6, color: fg),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              text,
              style: profile.captionStyle.copyWith(color: fg),
            ),
          ),
          if (onClose != null)
            IconButton(
              onPressed: onClose,
              icon: Icon(Icons.close_rounded, size: 18, color: fg),
              tooltip: '知道了',
              constraints: const BoxConstraints(
                minWidth: AgeProfile.minTouchTarget,
                minHeight: AgeProfile.minTouchTarget,
              ),
            ),
        ],
      ),
    );
  }
}

/// 顶部轻提示（**不用 SnackBar 承载错误**：它会盖住底部主按钮，
/// 而主操作按钮按规范必须放在屏幕下 1/3，两者会打架）。
void showAppSnack(BuildContext context, String message, {NoticeKind kind = NoticeKind.info}) {
  final messenger = ScaffoldMessenger.maybeOf(context);
  if (messenger == null) {
    return;
  }
  messenger
    ..clearSnackBars()
    ..showSnackBar(
      SnackBar(
        content: Text(message),
        duration: const Duration(seconds: 3),
        behavior: SnackBarBehavior.floating,
      ),
    );
}
