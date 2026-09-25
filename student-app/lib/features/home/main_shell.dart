/// 主壳（底部导航）—— UI 规范 P4 的 `[练习] [错题本] [我的]`。
///
/// **归属与衔接**：本文件归 WP-APP-02（首页所在的工作包），
/// 因为底部导航是 P4 首页的一部分。但「错题本 / 我的」两个 Tab 的内容
/// 属于 WP-APP-05 —— 所以这里**不 import 它的任何页面**，
/// 只在运行时检查 `MistakesRoutes.routes` 里有没有对应路由：
/// - 注册了 → 直接用它的 builder；
/// - 没注册 → 显示一张"还在做"的占位卡（不会崩、也不会假装能用）。
///
/// 这样 APP-05 落地时**只需要填自己的 `mistakes.dart`**，
/// 不用回来改本文件（契约「不修改他人文件」）。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme/age_profile.dart';
import '../../core/widgets/feedback.dart';
import '../../router/routes/mistakes.dart';
import 'home_page.dart';

class MainShell extends ConsumerStatefulWidget {
  const MainShell({super.key, this.initialIndex = 0});

  final int initialIndex;

  @override
  ConsumerState<MainShell> createState() => _MainShellState();
}

class _MainShellState extends ConsumerState<MainShell> {
  late int _index = widget.initialIndex;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);

    return Scaffold(
      // IndexedStack：切回来时**保留**滚动位置与已加载的数据
      //（学生切去看一眼错题本再回来，不该重新转圈）
      body: IndexedStack(
        index: _index,
        children: [
          const HomePage(),
          _registeredPage(MistakesRoutes.mistakes) ??
              const _ComingSoonPage(
                title: '错题本',
                hint: '练错过的词以后都会收在这里',
                icon: Icons.error_outline_rounded,
              ),
          _registeredPage(MistakesRoutes.records) ??
              const _ComingSoonPage(
                title: '我的',
                hint: '这里会放你的练习记录和设置',
                icon: Icons.person_outline_rounded,
              ),
        ],
      ),
      bottomNavigationBar: NavigationBarTheme(
        // 局部覆盖标签字号，让它跟分龄档位走（导航标签也是正文级别的信息，
        // 低年级学生要看得清）；**不动全局主题**，那是 APP-01 的文件。
        data: NavigationBarThemeData(
          labelTextStyle: WidgetStatePropertyAll(profile.captionStyle),
        ),
        child: NavigationBar(
          selectedIndex: _index,
          onDestinationSelected: (value) => setState(() => _index = value),
          destinations: const [
            NavigationDestination(
              icon: Icon(Icons.menu_book_outlined),
              selectedIcon: Icon(Icons.menu_book_rounded),
              label: '练习',
            ),
            NavigationDestination(
              icon: Icon(Icons.error_outline_rounded),
              selectedIcon: Icon(Icons.error_rounded),
              label: '错题本',
            ),
            NavigationDestination(
              icon: Icon(Icons.person_outline_rounded),
              selectedIcon: Icon(Icons.person_rounded),
              label: '我的',
            ),
          ],
        ),
      ),
    );
  }

  /// 若 APP-05 已注册该路由，就构造它的页面；否则返回 `null`。
  Widget? _registeredPage(String route) {
    final builder = MistakesRoutes.routes[route];
    return builder?.call(context);
  }
}

/// 还没落地的 Tab 的占位页。
class _ComingSoonPage extends StatelessWidget {
  const _ComingSoonPage({required this.title, required this.hint, required this.icon});

  final String title;
  final String hint;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: EmptyView(title: '$title还在做', hint: hint, icon: icon),
    );
  }
}
