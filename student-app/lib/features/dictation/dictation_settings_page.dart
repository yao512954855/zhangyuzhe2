/// 听写设置页 —— WP-D D-2 第 1 项；v1.3（todo11 A3）改造。
///
/// - 报中文 / 报英文（单选，本地选择：决定 TTS 朗读中文释义还是英文单词）；
/// - 隔多久报下一个：**只读展示服务端设置**（`GET /api/settings` 的
///   `dictation_countdown_s`，1~10 秒）—— 二轮决策 #13：倒计时在管理端
///   「练习设置」调整，学生端不再本地可调（旧 5~30s 步进器是死 UI，已删）。
///   拉取失败显示「--」并提示以管理端设置为准，不阻塞开始听写。
///
/// 入口：单元列表页选【听】后进来（路由注册是接力项 J1，归 WP-C）。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme/age_profile.dart';
import '../../core/widgets/app_button.dart';
import '../../core/providers.dart';
import 'dictation_models.dart';
import 'dictation_play_page.dart';

/// 听写设置页。
class DictationSettingsPage extends ConsumerStatefulWidget {
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
  ConsumerState<DictationSettingsPage> createState() =>
      _DictationSettingsPageState();
}

class _DictationSettingsPageState extends ConsumerState<DictationSettingsPage> {
  late DictationSettings _settings = widget.initialSettings;

  /// 服务端报词间隔（dictation_countdown_s，1~10）；null = 拉取中/失败。
  int? _remoteCountdown;
  bool _remoteFailed = false;

  @override
  void initState() {
    super.initState();
    _loadRemoteCountdown();
  }

  /// 拉服务端报词间隔（失败不阻塞：显示「--」，开始听写照常）。
  Future<void> _loadRemoteCountdown() async {
    try {
      final data = await ref
          .read(apiClientProvider)
          .get<Map<String, dynamic>>('/api/settings');
      final seconds =
          (data?['dictation_countdown_s'] as num?)?.toInt() ?? 3;
      if (mounted) {
        setState(() => _remoteCountdown = seconds.clamp(1, 10));
      }
    } on Object {
      if (mounted) {
        setState(() => _remoteFailed = true);
      }
    }
  }

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
                  _IntervalInfo(
                    seconds: _remoteCountdown,
                    failed: _remoteFailed,
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

/// 报词间隔（只读）：显示服务端 `dictation_countdown_s`。
class _IntervalInfo extends StatelessWidget {
  const _IntervalInfo({required this.seconds, required this.failed});

  /// 服务端值；null = 拉取中或失败。
  final int? seconds;
  final bool failed;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final text = seconds != null ? '$seconds 秒' : (failed ? '--' : '…');
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Column(
          children: [
            Text(text, style: profile.cardTitleStyle),
            const SizedBox(height: 4),
            Text(
              failed
                  ? '每一题报完等这么久再报下一题（连接服务器失败，以管理端设置为准）'
                  : '每一题报完等这么久再报下一题 · 在管理端「练习设置」调整',
              style: profile.captionStyle,
              textAlign: TextAlign.center,
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
