/// 听写设置页 —— WP-D D-2 第 1 项。
///
/// 只有两个设置：报中文 / 报英文（单选）+ 播报间隔（默认 10s，范围 5~30，
/// 步进 1）。选项按 UI 规范用卡片/选择块，不用下拉框。
///
/// 入口：单元列表页选【听】后进来（路由注册是接力项 J1，归 WP-C）。
library;

import 'package:flutter/material.dart';

import '../../core/theme/age_profile.dart';
import '../../core/widgets/app_button.dart';
import 'dictation_models.dart';
import 'dictation_play_page.dart';

/// 听写设置页。
class DictationSettingsPage extends StatefulWidget {
  const DictationSettingsPage({
    super.key,
    required this.items,
    this.unitName = '',
    this.returnRoute,
    this.initialSettings = const DictationSettings(),
  });

  /// 本次听写的题目（上游 practice/start 下发）。
  final List<DictationItem> items;
  final String unitName;

  /// 结算后跳回的路由名（传给后续页面，最终由 J1 接线）。
  final String? returnRoute;
  final DictationSettings initialSettings;

  @override
  State<DictationSettingsPage> createState() => _DictationSettingsPageState();
}

class _DictationSettingsPageState extends State<DictationSettingsPage> {
  late DictationSettings _settings = widget.initialSettings;

  void _start() {
    if (widget.items.isEmpty || !mounted) {
      return;
    }
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => DictationPlayPage(
          items: widget.items,
          unitName: widget.unitName,
          returnRoute: widget.returnRoute,
          settings: _settings,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.unitName.isEmpty ? '听写' : '听写 · ${widget.unitName}'),
      ),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(
                  AgeProfile.screenEdgeInset,
                  AgeProfile.elementGap,
                  AgeProfile.screenEdgeInset,
                  AgeProfile.elementGap,
                ),
                children: [
                  Text('怎么报', style: profile.pageTitleStyle),
                  const SizedBox(height: AgeProfile.elementGap),
                  RadioGroup<bool>(
                    groupValue: _settings.readChinese,
                    onChanged: (v) {
                      if (v != null) {
                        setState(() => _settings = _settings.copyWith(readChinese: v));
                      }
                    },
                    child: Column(
                      children: [
                        _ModeCard(
                          title: '报中文',
                          subtitle: '读中文意思，你在纸上写英文',
                          value: true,
                        ),
                        const SizedBox(height: AgeProfile.elementGap / 2),
                        _ModeCard(
                          title: '报英文',
                          subtitle: '读英文单词，你在纸上写英文',
                          value: false,
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: AgeProfile.elementGap * 1.5),
                  Text('隔多久报下一个', style: profile.pageTitleStyle),
                  const SizedBox(height: AgeProfile.elementGap),
                  _IntervalStepper(
                    seconds: _settings.intervalSeconds,
                    onChanged: (s) =>
                        setState(() => _settings = _settings.copyWith(intervalSeconds: s)),
                  ),
                  const SizedBox(height: AgeProfile.elementGap),
                  if (widget.items.isEmpty)
                    const Padding(
                      padding: EdgeInsets.only(top: AgeProfile.elementGap),
                      child: _EmptyHint(),
                    ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AgeProfile.screenEdgeInset, 0,
                AgeProfile.screenEdgeInset, AgeProfile.elementGap,
              ),
              child: AppButton(
                label: '开始听写（共 ${widget.items.length} 题）',
                onPressed: widget.items.isEmpty ? null : _start,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 单选卡片（报中文 / 报英文）。组值由外层 `RadioGroup` 管理。
class _ModeCard extends StatelessWidget {
  const _ModeCard({
    required this.title,
    required this.subtitle,
    required this.value,
  });

  final String title;
  final String subtitle;
  final bool value;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    return Card(
      margin: EdgeInsets.zero,
      child: RadioListTile<bool>(
        value: value,
        title: Text(title, style: profile.cardTitleStyle),
        subtitle: Text(subtitle, style: profile.captionStyle),
        controlAffinity: ListTileControlAffinity.trailing,
      ),
    );
  }
}

/// 间隔步进器（5~30，步进 1）。
class _IntervalStepper extends StatelessWidget {
  const _IntervalStepper({required this.seconds, required this.onChanged});

  final int seconds;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final atMin = seconds <= DictationSettings.minIntervalSeconds;
    final atMax = seconds >= DictationSettings.maxIntervalSeconds;
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        child: Row(
          children: [
            IconButton(
              onPressed: atMin ? null : () => onChanged(seconds - 1),
              icon: const Icon(Icons.remove_circle_outline_rounded),
              tooltip: '减 1 秒',
            ),
            Expanded(
              child: Column(
                children: [
                  Text('$seconds 秒', style: profile.cardTitleStyle),
                  Text(
                    '每一题报完等这么久再报下一题',
                    style: profile.captionStyle,
                    textAlign: TextAlign.center,
                  ),
                ],
              ),
            ),
            IconButton(
              onPressed: atMax ? null : () => onChanged(seconds + 1),
              icon: const Icon(Icons.add_circle_outline_rounded),
              tooltip: '加 1 秒',
            ),
          ],
        ),
      ),
    );
  }
}

/// 无题提示。
class _EmptyHint extends StatelessWidget {
  const _EmptyHint();

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AgeProfile.elementGap),
        child: Text('这个单元没有可听写的内容', style: profile.bodyStyle),
      ),
    );
  }
}
