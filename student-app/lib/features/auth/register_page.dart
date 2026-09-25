/// 注册页 —— UI 规范 §7.3 / 30-APP-01 F15（U6）。
///
/// **学生自主注册即可用，无需审核**：注册成功直接进首页。
///
/// 班级选择走**免鉴权**的 `GET /api/classes/options`（契约 §4.1 补充，见
/// `class_options.dart`）—— 注册发生在登录之前，这条接口要 token 就等于注册页
/// 永远拉不到班级。**拉不到就退回「先不选」**，不阻断注册（`class_id` 在
/// 契约 §2.2 本就是可选字段）。
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
import 'class_options.dart';

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

  /// 选中的班级 id；`null` = 「先不选」（契约 §2.2 里 `class_id` 是可选字段）。
  int? _classId;

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
            classId: _classId,
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
    final optionsAsync = ref.watch(classOptionsProvider);

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
                '不用等管理者审核，填完就能用',
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
                options: optionsAsync.asData?.value ?? const <ClassOption>[],
                selectedId: _classId,
                loading: optionsAsync.isLoading,
                onSelect: (id) => setState(() => _classId = id),
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

/// 班级选择区 —— 卡片列表（UI 规范 §7.3 明确「不是下拉框」），最后一张是「先不选」。
///
/// 数据来自免鉴权的 `GET /api/classes/options`（契约 §4.1 补充）。
/// **拉不到（网络 / 服务端异常）时不报错、也不伪造班级** —— 只剩「先不选」，
/// 学生照样能注册，之后由管理者归班。
class _ClassSection extends StatelessWidget {
  const _ClassSection({
    required this.options,
    required this.selectedId,
    required this.loading,
    required this.onSelect,
  });

  final List<ClassOption> options;

  /// 当前选中的班级 id；`null` = 「先不选」。
  final int? selectedId;

  final bool loading;

  /// 选中回调；`null` 表示选了「先不选」。
  final ValueChanged<int?> onSelect;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (loading)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(
              '正在看看有哪些班…',
              style: profile.captionStyle.copyWith(color: extra.textFaint),
            ),
          ),
        for (final option in options) ...[
          _ClassCard(
            title: option.name,
            subtitle: option.subtitle,
            selected: selectedId == option.id,
            onTap: () => onSelect(option.id),
          ),
          const SizedBox(height: 8),
        ],
        _ClassCard(
          title: '先不选',
          subtitle: '之后让管理者帮你加入班级',
          selected: selectedId == null,
          onTap: () => onSelect(null),
        ),
      ],
    );
  }
}

/// 单个班级卡片（选中态 = 主色底 + 右侧勾）。
class _ClassCard extends StatelessWidget {
  const _ClassCard({
    required this.title,
    required this.subtitle,
    required this.selected,
    required this.onTap,
  });

  final String title;
  final String subtitle;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final scheme = Theme.of(context).colorScheme;

    return SizedBox(
      height: profile.listItemHeight,
      child: Material(
        color: selected ? scheme.primaryContainer : extra.card,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(title, style: profile.bodyStyle),
                      Text(
                        subtitle,
                        style: profile.captionStyle.copyWith(color: extra.textFaint),
                      ),
                    ],
                  ),
                ),
                if (selected)
                  Icon(
                    Icons.check_circle_rounded,
                    color: scheme.primary,
                    size: 22,
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
