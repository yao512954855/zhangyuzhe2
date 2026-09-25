/// P2 登录页 —— UI 规范 §7.3（U6 / U7 / U8）。
///
/// 文案要求（照抄规范要点）：
/// - 不用「用户名」，用**「我的学号」**这类儿童能懂的说法；
/// - 密码框**提供「显示密码」的眼睛图标** —— 小学生打错密码很常见，可见比隐藏更实用；
/// - 学号框唤起数字键盘，但**允许输入字母**（部分学校学号含字母）。
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/router/app_router.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import 'auth_controller.dart';
import 'register_page.dart';

/// 登录页。
class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage> {
  final _username = TextEditingController();
  final _password = TextEditingController();
  final _passwordFocus = FocusNode();
  bool _obscure = true;
  String? _error;

  @override
  void dispose() {
    _username.dispose();
    _password.dispose();
    _passwordFocus.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final username = _username.text.trim();
    final password = _password.text;
    if (username.isEmpty) {
      setState(() => _error = '先写上你的学号吧');
      return;
    }
    if (password.isEmpty) {
      setState(() => _error = '密码还没填哦');
      return;
    }

    setState(() => _error = null);
    try {
      await ref.read(authControllerProvider.notifier).login(
            username: username,
            password: password,
          );
      if (!mounted) {
        return;
      }
      Navigator.of(context).pushReplacementNamed(AppRouter.homeRoute);
    } on ApiException catch (e) {
      if (!mounted) {
        return;
      }
      setState(() => _error = e.displayText);
    }
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final busy = ref.watch(authControllerProvider).busy;

    return Scaffold(
      body: SafeArea(
        child: GestureDetector(
          onTap: () => FocusScope.of(context).unfocus(),
          child: ListView(
            padding: const EdgeInsets.symmetric(
              horizontal: AgeProfile.screenEdgeInset * 1.5,
            ),
            children: [
              const SizedBox(height: 48),
              Icon(
                Icons.menu_book_rounded,
                size: 56,
                color: Theme.of(context).colorScheme.primary,
              ),
              const SizedBox(height: AgeProfile.elementGap),
              Text('背记系统', textAlign: TextAlign.center, style: profile.pageTitleStyle),
              const SizedBox(height: 40),
              TextField(
                controller: _username,
                keyboardType: TextInputType.text,
                textInputAction: TextInputAction.next,
                inputFormatters: [LengthLimitingTextInputFormatter(32)],
                style: profile.bodyStyle,
                decoration: const InputDecoration(hintText: '我的学号'),
                onSubmitted: (_) => _passwordFocus.requestFocus(),
              ),
              const SizedBox(height: AgeProfile.elementGap),
              TextField(
                controller: _password,
                focusNode: _passwordFocus,
                obscureText: _obscure,
                textInputAction: TextInputAction.done,
                style: profile.bodyStyle,
                decoration: InputDecoration(
                  hintText: '我的密码',
                  suffixIcon: IconButton(
                    tooltip: _obscure ? '看看密码' : '藏起来',
                    icon: Icon(
                      _obscure ? Icons.visibility_off_rounded : Icons.visibility_rounded,
                      color: extra.textFaint,
                    ),
                    onPressed: () => setState(() => _obscure = !_obscure),
                  ),
                ),
                onSubmitted: (_) => _submit(),
              ),
              if (_error != null) ...[
                const SizedBox(height: AgeProfile.elementGap),
                // 就地提示，不弹模态框（UI 规范 §5.2）
                InlineNotice(text: _error!, kind: NoticeKind.error),
              ],
              const SizedBox(height: AgeProfile.elementGap * 1.5),
              AppButton(
                label: '登 录',
                loading: busy,
                onPressed: _submit,
              ),
              const SizedBox(height: AgeProfile.adjacentButtonGap),
              AppButton(
                label: '第一次用？去注册 →',
                kind: AppButtonKind.text,
                onPressed: busy
                    ? null
                    : () => Navigator.of(context).push(
                          MaterialPageRoute<void>(
                            builder: (_) => const RegisterPage(),
                          ),
                        ),
              ),
              const SizedBox(height: 40),
            ],
          ),
        ),
      ),
    );
  }
}
