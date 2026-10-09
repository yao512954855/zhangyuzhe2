/// 背诵模式选择页 —— 简化版 WP-C（功能点 4）。
///
/// 从单元列表带 `PracticeSelection` 进来，两张模式卡：
/// **看中文背英文**（spell_zh2en，逐字母拼读）与
/// **看英文背中文**（spell_en2zh，说中文）。选中即组卷进答题页
/// （`launchSpellRun` 内发 `POST /api/practice/start`）。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../home/practice_entry.dart';

/// 背诵模式选择页。
class SpellConfigPage extends ConsumerStatefulWidget {
  const SpellConfigPage({super.key});

  @override
  ConsumerState<SpellConfigPage> createState() => _SpellConfigPageState();
}

class _SpellConfigPageState extends ConsumerState<SpellConfigPage> {
  bool _busy = false;

  PracticeSelection? _selection() {
    final args = ModalRoute.of(context)?.settings.arguments;
    return args is PracticeSelection ? args : null;
  }

  Future<void> _start(String mode) async {
    if (_busy) {
      return;
    }
    final selection = _selection();
    if (selection == null) {
      ScaffoldMessenger.maybeOf(context)?.showSnackBar(
        const SnackBar(
          content: Text('请从单元列表重新进入'),
          behavior: SnackBarBehavior.floating,
        ),
      );
      return;
    }
    setState(() => _busy = true);
    try {
      await launchSpellRun(
        context,
        api: ref.read(apiClientProvider),
        selection: selection,
        mode: mode,
      );
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final selection = _selection();

    return Scaffold(
      appBar: AppBar(
        title: Text(selection == null
            ? '选个背法'
            : '${selection.unitName} · 背诵'),
      ),
      body: SafeArea(
        child: selection == null
            ? Center(
                child: Text(
                  '请从单元列表重新进入',
                  style: profile.bodyStyle.copyWith(color: extra.textSub),
                ),
              )
            : ListView(
                padding: const EdgeInsets.all(AgeProfile.screenEdgeInset * 2),
                children: [
                  _ModeCard(
                    icon: Icons.abc_rounded,
                    title: '看中文背英文',
                    hint: '看着中文，一个字母一个字母拼出来',
                    loading: _busy,
                    onTap: () => _start('spell_zh2en'),
                  ),
                  const SizedBox(height: AgeProfile.adjacentButtonGap * 1.5),
                  _ModeCard(
                    icon: Icons.record_voice_over_rounded,
                    title: '看英文背中文',
                    hint: '看着英文，大声说出它的意思',
                    loading: _busy,
                    onTap: () => _start('spell_en2zh'),
                  ),
                ],
              ),
      ),
    );
  }
}

class _ModeCard extends StatelessWidget {
  const _ModeCard({
    required this.icon,
    required this.title,
    required this.hint,
    required this.loading,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String hint;
  final bool loading;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;

    return Material(
      color: extra.card,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: loading ? null : onTap,
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Row(
            children: [
              Icon(icon, size: 44, color: palette.primary),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: profile.cardTitleStyle),
                    const SizedBox(height: 4),
                    Text(
                      hint,
                      style: profile.captionStyle.copyWith(color: extra.textSub),
                    ),
                  ],
                ),
              ),
              if (loading)
                const SizedBox(
                  width: 22,
                  height: 22,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              else
                Icon(Icons.chevron_right_rounded, color: extra.textFaint),
            ],
          ),
        ),
      ),
    );
  }
}
