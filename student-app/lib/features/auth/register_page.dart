/// 注册页 —— UI 规范 §7.3 / 30-APP-01 F15（U6）。
///
/// **学生自主注册即可用，无需审核**：注册成功直接进首页。
///
/// ⚠️ **契约缺口（已上报）**：UI 规范 §7.3 要求「班级选择用卡片列表」，
/// 但后端 `GET /api/classes` 是 `require_admin`（BE-02 实现），
/// **学生端拿不到班级列表**，因此本页暂时只能提供「先不选」。
/// 待后端补一个学生可访问的班级清单接口后，把下面的 `_ClassSection` 接上即可。
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

/// 注册页。
class RegisterPage extends ConsumerStatefulWidget {
  const RegisterPage({super.key});

  @override
  ConsumerState<RegisterPage> createState() => _RegisterPageState();
}

class _RegisterPageState extends ConsumerState<RegisterPage> {
  final _username = TextEditingController();
  final _realName = TextEditingController();
  final _password = TextEditingController();
  bool _obscure = true;
  String? _error;
  bool _joinedClass = false;

  @override
  void dispose() {
    _username.dispose();
    _realName.dispose();
    _password.dispose();
    super.dispose();
  }

  /// 与后端 `RegisterIn` 的校验保持一致（提前拦掉，省一次往返）。
  String? _validate() {
    final username = _username.text.trim();
    final realName = _realName.text.trim();
    final password = _password.text;

    if (!RegExp(r'^[0-9A-Za-z]{4,20}$').hasMatch(username)) {
      return '学号要 4 到 20 位，只能用字母或数字';
    }
    if (realName.isEmpty) {
      return '写上你的名字吧';
    }
    if (realName.characters.length > 20) {
      return '名字最多 20 个字';
    }
    if (password.length < 6 || password.length > 20) {
      return '密码要 6 到 20 位';
    }
    return null;
  }

  Future<void> _submit() async {
    final problem = _validate();
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }

    setState(() => _error = null);
    try {
      await ref.read(authControllerProvider.notifier).register(
            username: _username.text.trim(),
            realName: _realName.text.trim(),
            password: _password.text,
          );
      if (!mounted) {
        return;
      }
      // 注册成功直接进首页（无需审核）
      Navigator.of(context)
        ..popUntil((route) => route.isFirst)
        ..pushReplacementNamed(AppRouter.homeRoute);
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
      appBar: AppBar(title: const Text('注册')),
      body: SafeArea(
        child: GestureDetector(
          onTap: () => FocusScope.of(context).unfocus(),
          child: ListView(
            padding: const EdgeInsets.symmetric(
              horizontal: AgeProfile.screenEdgeInset * 1.5,
            ),
            children: [
              const SizedBox(height: AgeProfile.elementGap),
              Text(
                '填好这些就能开始啦',
                style: profile.cardTitleStyle,
              ),
              const SizedBox(height: 6),
              Text(
                '不用等老师审核，填完就能用',
                style: profile.captionStyle.copyWith(color: extra.textSub),
              ),
              const SizedBox(height: 24),
              TextField(
                controller: _username,
                keyboardType: TextInputType.text,
                textInputAction: TextInputAction.next,
                inputFormatters: [LengthLimitingTextInputFormatter(20)],
                style: profile.bodyStyle,
                decoration: const InputDecoration(hintText: '我的学号（4-20 位）'),
              ),
              const SizedBox(height: AgeProfile.elementGap),
              TextField(
                controller: _realName,
                textInputAction: TextInputAction.next,
                inputFormatters: [LengthLimitingTextInputFormatter(20)],
                style: profile.bodyStyle,
                decoration: const InputDecoration(hintText: '我的名字'),
              ),
              const SizedBox(height: AgeProfile.elementGap),
              TextField(
                controller: _password,
                obscureText: _obscure,
                textInputAction: TextInputAction.done,
                inputFormatters: [LengthLimitingTextInputFormatter(20)],
                style: profile.bodyStyle,
                decoration: InputDecoration(
                  hintText: '设一个密码（6-20 位）',
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
              const SizedBox(height: 24),
              Text('我在哪个班？', style: profile.cardTitleStyle),
              const SizedBox(height: 8),
              _ClassSection(
                joined: _joinedClass,
                onChoose: (v) => setState(() => _joinedClass = v),
              ),
              if (_error != null) ...[
                const SizedBox(height: AgeProfile.elementGap),
                InlineNotice(text: _error!, kind: NoticeKind.error),
              ],
              const SizedBox(height: AgeProfile.elementGap * 1.5),
              AppButton(label: '注册并开始', loading: busy, onPressed: _submit),
              const SizedBox(height: 32),
            ],
          ),
        ),
      ),
    );
  }
}

/// 班级选择区。
///
/// 后端暂未提供学生可访问的班级清单，因此这里只呈现「先不选」这一个选项
/// ——**不伪造一个假的班级列表**：填了不存在的班级只会让学生以为注册成功了。
class _ClassSection extends StatelessWidget {
  const _ClassSection({required this.joined, required this.onChoose});

  final bool joined;
  final ValueChanged<bool> onChoose;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SizedBox(
          height: profile.listItemHeight,
          child: Material(
            color: !joined
                ? Theme.of(context).colorScheme.primaryContainer
                : extra.card,
            borderRadius: BorderRadius.circular(12),
            child: InkWell(
              borderRadius: BorderRadius.circular(12),
              onTap: () => onChoose(false),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 14),
                child: Row(
                  children: [
                    Expanded(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('先不选', style: profile.bodyStyle),
                          Text(
                            '之后让老师帮你加入班级',
                            style: profile.captionStyle.copyWith(color: extra.textFaint),
                          ),
                        ],
                      ),
                    ),
                    if (!joined)
                      Icon(
                        Icons.check_circle_rounded,
                        color: Theme.of(context).colorScheme.primary,
                        size: 22,
                      ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }
}
