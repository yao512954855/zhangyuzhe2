/// 启动引导 —— 30-APP-01 F5（U1 / U7 / U8）。
///
/// 四步：① 读本地地址 → ② 探活 `/api/ping` → ③ 有 token 校验 `/api/auth/me`
/// → ④ 进首页。**任一步失败都要给出明确的下一步**，
/// 不允许出现白屏、卡住、或"转圈转到天荒地老"。
///
/// 这里只做一次**极快的历史地址试连**（3 秒）；
/// 失败就整包交给 P1 服务器发现页（它会试全部历史地址 + UDP 广播），
/// 避免两处各写一遍发现逻辑。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/router/app_router.dart';
import '../../core/theme/age_profile.dart';
import '../../core/widgets/feedback.dart';
import '../auth/auth_controller.dart';

/// 启动页。
class BootstrapPage extends ConsumerStatefulWidget {
  const BootstrapPage({super.key});

  @override
  ConsumerState<BootstrapPage> createState() => _BootstrapPageState();
}

class _BootstrapPageState extends ConsumerState<BootstrapPage> {
  String _message = '正在连接…';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _boot());
  }

  Future<void> _boot() async {
    final serverStore = ref.read(serverStoreProvider);
    final api = ref.read(apiClientProvider);

    // ① + ② 有地址就探活
    if (!serverStore.hasServer) {
      _go(AppRouter.discoveryRoute);
      return;
    }

    if (mounted) {
      setState(() => _message = '正在连接…');
    }
    final info = await api.ping(timeout: const Duration(seconds: 3));
    if (!mounted) {
      return;
    }
    if (info == null) {
      // 连不上（换了网络 / 服务器 IP 变了）→ 交给发现页默默找回
      _go(AppRouter.discoveryRoute);
      return;
    }

    // ③ 有 token 才校验；没有就直接去登录页
    if (serverStore.hasServer && ref.read(localStoreProvider).token != null) {
      setState(() => _message = '正在打开…');
      final ok = await ref.read(authControllerProvider.notifier).restore();
      if (!mounted) {
        return;
      }
      _go(ok ? AppRouter.homeRoute : AppRouter.loginRoute);
      return;
    }

    _go(AppRouter.loginRoute);
  }

  void _go(String route) {
    if (!mounted) {
      return;
    }
    Navigator.of(context).pushReplacementNamed(route);
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);

    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            const Spacer(flex: 3),
            Icon(
              Icons.menu_book_rounded,
              size: 64,
              color: Theme.of(context).colorScheme.primary,
            ),
            const SizedBox(height: AgeProfile.elementGap),
            Text('背记系统', style: profile.pageTitleStyle),
            const Spacer(),
            LoadingView(message: _message),
            const Spacer(flex: 3),
          ],
        ),
      ),
    );
  }
}
