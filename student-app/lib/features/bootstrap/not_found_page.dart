/// 找不到页面 —— F19「不出现红屏」。
library;

import 'package:flutter/material.dart';

import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';

/// 未知路由兜底页。
class NotFoundPage extends StatelessWidget {
  const NotFoundPage({super.key, this.routeName});

  final String? routeName;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Scaffold(
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(AgeProfile.screenEdgeInset * 2),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.explore_off_rounded, size: 56, color: extra.textFaint),
                const SizedBox(height: AgeProfile.elementGap),
                Text('走错路啦', style: profile.pageTitleStyle),
                const SizedBox(height: 8),
                Text(
                  '这个页面找不到了，我们回上一步吧',
                  textAlign: TextAlign.center,
                  style: profile.captionStyle.copyWith(color: extra.textSub),
                ),
                // 路由名只给开发者看，学生界面上不出现技术串
                if (routeName != null && const bool.fromEnvironment('dart.vm.product') == false)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                      routeName!,
                      style: profile.captionStyle.copyWith(color: extra.textFaint),
                    ),
                  ),
                const SizedBox(height: 24),
                SizedBox(
                  width: 220,
                  child: AppButton(
                    label: '返回',
                    onPressed: () {
                      final navigator = Navigator.of(context);
                      if (navigator.canPop()) {
                        navigator.pop();
                      } else {
                        navigator.pushReplacementNamed('/');
                      }
                    },
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
