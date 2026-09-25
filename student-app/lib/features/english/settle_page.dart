/// 结算页 —— F16 / P9。
///
/// 渲染 `POST /api/sessions/{id}/finish` 的响应（服务端重算，客户端只渲染）：
/// 正确率、题量、用时、提示次数、错题列表（你的答案 vs 正确答案）。
/// 不弹模态框、配色与字号走护眼 / 分龄规范。
library;

import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';
import '../../core/theme/age_profile.dart';
import '../../core/widgets/app_button.dart';
import 'practice_controller.dart';

/// 结算页入参（经路由 `arguments` 传递）。
class SettleArguments {
  const SettleArguments({required this.settle, this.unitName});

  /// 兜底空构造（路由未带参数时，避免崩）。
  const SettleArguments.empty()
      : settle = null,
        unitName = null;

  final PracticeSettle? settle;
  final String? unitName;
}

/// 英语练习结算页。
class SettlePage extends StatelessWidget {
  const SettlePage({super.key, required this.args});

  final SettleArguments args;

  static String _formatDuration(int ms) {
    final totalSec = (ms / 1000).round();
    final m = totalSec ~/ 60;
    final s = totalSec % 60;
    if (m <= 0) return '$s 秒';
    return '$m 分 ${s.toString().padLeft(2, '0')} 秒';
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final settle = args.settle;
    if (settle == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('结算')),
        body: const Center(child: Text('这次练习没有结算数据')),
      );
    }

    final wrongCount = settle.totalItems - settle.correctItems;

    return Scaffold(
      appBar: AppBar(title: const Text('结算'), centerTitle: true),
      body: ListView(
        padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
        children: [
          Text('完成啦！', style: profile.pageTitleStyle, textAlign: TextAlign.center),
          const SizedBox(height: 8),
          Text('${settle.grade} · ${settle.score.round()} 分',
              style: profile.wordStyle
                  .copyWith(color: Theme.of(context).colorScheme.onSurface),
              textAlign: TextAlign.center),
          const SizedBox(height: AgeProfile.elementGap * 1.5),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: [
                  _StatRow(label: '题量', value: '${settle.correctItems} / ${settle.totalItems}'),
                  _StatRow(label: '正确率', value: '${(settle.accuracy * 100).round()}%'),
                  _StatRow(label: '用时', value: _formatDuration(settle.durationMs)),
                  _StatRow(label: '用了提示', value: '${settle.hintCount} 次'),
                  _StatRow(label: '卡住', value: '$wrongCount 处'),
                ],
              ),
            ),
          ),
          const SizedBox(height: AgeProfile.elementGap),
          if (wrongCount > 0) ...[
            Text('回看没读出来的地方', style: profile.cardTitleStyle),
            const SizedBox(height: 8),
            ...settle.wrongItems.map((w) => _WrongTile(item: w)),
          ],
          const SizedBox(height: AgeProfile.elementGap * 1.5),
          AppButton(
            label: '再练一次',
            onPressed: () => Navigator.of(context)
              ..popUntil((r) => r.isFirst),
          ),
          const SizedBox(height: AgeProfile.adjacentButtonGap),
          AppButton(
            label: '回到首页',
            kind: AppButtonKind.text,
            onPressed: () => Navigator.of(context)
              ..popUntil((r) => r.isFirst),
          ),
        ],
      ),
    );
  }
}

class _StatRow extends StatelessWidget {
  const _StatRow({required this.label, required this.value});
  final String label;
  final String value;
  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: profile.bodyStyle.copyWith(color: extra.textSub)),
          Text(value,
              style: profile.bodyStyle
                  .copyWith(color: Theme.of(context).colorScheme.onSurface)),
        ],
      ),
    );
  }
}

class _WrongTile extends StatelessWidget {
  const _WrongTile({required this.item});
  final WrongItem item;
  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('${item.cn}（${item.en}）',
                style: profile.bodyStyle
                    .copyWith(color: Theme.of(context).colorScheme.onSurface)),
            const SizedBox(height: 4),
            Text('你读的：${item.yourAnswer.isEmpty ? '（空）' : item.yourAnswer}',
                style: profile.captionStyle.copyWith(color: extra.error)),
          ],
        ),
      ),
    );
  }
}
