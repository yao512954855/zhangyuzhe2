/// 单次场次详情 —— WP-APP-05 F2（U3）「可查看单次详情」。
///
/// 数据来自 `GET /api/sessions/{id}`（**契约 §14.1 缺口 7** → §9.5.2）。
/// 服务端只对**已结束**的场次下发逐题明细：进行中的场次 `expected` 就是答案，
/// 中途返回等于泄题，所以那类场次在列表里就没有入口（见 [SessionRecord.canOpenDetail]）。
///
/// 英语给逐项（一个词条一行），语文给按篇（每篇含漏字）。两种都**只渲染服务端给的**，
/// 客户端不算正确率、不猜漏字。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/util/format.dart';
import '../../core/widgets/feedback.dart';
import '../home/time_format.dart';
import 'session_records.dart';

/// 单次练习详情页。
class SessionDetailPage extends ConsumerWidget {
  const SessionDetailPage({super.key, required this.record});

  /// 列表里点进来的那一条 —— 用来**立刻**渲染头部（不用等第二次请求）。
  final SessionRecord record;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final detail = ref.watch(sessionDetailProvider(record.sessionId));

    return Scaffold(
      appBar: AppBar(title: Text('这次练习', style: profile.pageTitleStyle)),
      body: ListView(
        padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
        children: [
          _HeadlineCard(record: record, profile: profile, extra: extra),
          const SizedBox(height: AgeProfile.elementGap),
          detail.when(
            loading: () => const Padding(
              padding: EdgeInsets.symmetric(vertical: 32),
              child: LoadingView(),
            ),
            error: (e, _) => ErrorView(
              message: e is ApiException ? e.displayText : '暂时拿不到这次练习的明细',
              onRetry: () =>
                  ref.invalidate(sessionDetailProvider(record.sessionId)),
            ),
            data: (d) => d.summary.isChinese
                ? _ChineseBody(detail: d, profile: profile, extra: extra)
                : _EnglishBody(detail: d, profile: profile, extra: extra),
          ),
        ],
      ),
    );
  }
}

/// 概览：正确率 + 单元 + 时间。
class _HeadlineCard extends StatelessWidget {
  const _HeadlineCard({
    required this.record,
    required this.profile,
    required this.extra,
  });

  final SessionRecord record;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) {
    final when = formatLastPractice(record.startedAt);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(record.title, style: profile.cardTitleStyle),
            if (when != null) ...[
              const SizedBox(height: 4),
              Text(
                when,
                style: profile.captionStyle.copyWith(color: extra.textSub),
              ),
            ],
            const SizedBox(height: 14),
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(
                  formatPercent(record.accuracy),
                  style: profile.wordStyle.copyWith(
                    fontSize: profile.wordBig + 8,
                    color: extra.hint,
                  ),
                ),
                const SizedBox(width: 8),
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Text(
                    record.isChinese ? '背出比例' : '正确率',
                    style: profile.captionStyle.copyWith(color: extra.textSub),
                  ),
                ),
                const Spacer(),
                _StatusChip(record: record, profile: profile, extra: extra),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// 状态标签。进行中 / 中途退出要如实说出来，不能都显示成「已完成」。
class _StatusChip extends StatelessWidget {
  const _StatusChip({
    required this.record,
    required this.profile,
    required this.extra,
  });

  final SessionRecord record;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) {
    final color = switch (record.status) {
      'ongoing' => extra.hint,
      'abandoned' => extra.textSub,
      _ => extra.success,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        sessionStatusLabel(record.status),
        style: profile.captionStyle.copyWith(color: color),
      ),
    );
  }
}

/// 英语场次：逐项明细（一个词条一行）。
class _EnglishBody extends StatelessWidget {
  const _EnglishBody({
    required this.detail,
    required this.profile,
    required this.extra,
  });

  final SessionDetail detail;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) {
    final summary = detail.summary;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _MetricGrid(
          metrics: [
            ('总用时', formatDurationMs(summary.durationMs)),
            ('用了提示', '${summary.hintCount} 次'),
            ('答对', '${summary.correctItems}/${summary.totalItems} 个词'),
          ],
          profile: profile,
          extra: extra,
        ),
        const SizedBox(height: AgeProfile.elementGap),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('每个词的情况', style: profile.cardTitleStyle),
                const SizedBox(height: 10),
                if (detail.items.isEmpty)
                  Text(
                    '这次没有可复盘的词条。',
                    style: profile.bodyStyle.copyWith(color: extra.textSub),
                  )
                else
                  for (final item in detail.items)
                    _ItemRow(item: item, profile: profile, extra: extra),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

/// 一个词条一行：错在哪、原词是什么。
class _ItemRow extends StatelessWidget {
  const _ItemRow({
    required this.item,
    required this.profile,
    required this.extra,
  });

  final SessionItem item;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) {
    final (icon, color, label) = switch (item.state) {
      'correct' => (Icons.check_circle_rounded, extra.success, '答对'),
      'empty' => (Icons.remove_circle_outline_rounded, extra.textFaint, '没作答'),
      _ => (Icons.cancel_rounded, extra.error, '答错'),
    };

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: color, size: 22),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        item.en ?? '（词条已删除）',
                        style: profile.bodyStyle,
                      ),
                    ),
                    Text(
                      label,
                      style: profile.captionStyle.copyWith(color: color),
                    ),
                  ],
                ),
                if (item.cn != null)
                  Text(
                    item.cn!,
                    style: profile.captionStyle.copyWith(color: extra.textSub),
                  ),
                if (!item.isCorrect && !item.isEmpty)
                  Text(
                    '你答的是「${item.yourAnswer ?? '（没听清）'}」，'
                    '应该是「${item.expected ?? '—'}」',
                    style: profile.captionStyle.copyWith(color: extra.error),
                  ),
                if (item.hintLevel > 0)
                  Text(
                    '用了第 ${item.hintLevel} 档提示',
                    style: profile.captionStyle.copyWith(color: extra.textFaint),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 语文场次：按篇明细（每篇含漏字）。
class _ChineseBody extends StatelessWidget {
  const _ChineseBody({
    required this.detail,
    required this.profile,
    required this.extra,
  });

  final SessionDetail detail;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) {
    final summary = detail.summary;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _MetricGrid(
          metrics: [
            ('总用时', formatDurationMs(summary.durationMs)),
            ('用了提示', '${summary.hintCount} 次'),
            ('背出', '${summary.correctItems}/${summary.totalItems} 个字'),
          ],
          profile: profile,
          extra: extra,
        ),
        const SizedBox(height: AgeProfile.elementGap),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('每一篇的情况', style: profile.cardTitleStyle),
                const SizedBox(height: 10),
                if (detail.recitations.isEmpty)
                  Text(
                    '这次没有可复盘的课文。',
                    style: profile.bodyStyle.copyWith(color: extra.textSub),
                  )
                else
                  for (final rec in detail.recitations)
                    _RecitationRow(rec: rec, profile: profile, extra: extra),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

/// 一篇课文一行：覆盖比例 + 等级 + 漏字。
class _RecitationRow extends StatelessWidget {
  const _RecitationRow({
    required this.rec,
    required this.profile,
    required this.extra,
  });

  final RecitationRecord rec;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) {
    final missing = rec.missedDetail.map((m) => m.char ?? '□').join(' ');
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(rec.title ?? '（课文已删除）', style: profile.bodyStyle),
              ),
              Text(
                formatPercent(rec.coveredRatio),
                style: profile.bodyStyle.copyWith(color: extra.hint),
              ),
            ],
          ),
          const SizedBox(height: 2),
          Text(
            '背出 ${rec.matchedChars}/${rec.totalChars} 个字'
            '${rec.grade == null ? '' : ' · ${rec.grade}'}'
            ' · ${formatDurationMs(rec.durationMs)}'
            '${rec.restartCount > 0 ? ' · 重来 ${rec.restartCount} 次' : ''}',
            style: profile.captionStyle.copyWith(color: extra.textSub),
          ),
          if (rec.missedDetail.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '漏了 ${rec.missedDetail.length} 个字：$missing',
                style: profile.captionStyle.copyWith(color: extra.error),
              ),
            ),
        ],
      ),
    );
  }
}

/// 三项指标横排。
class _MetricGrid extends StatelessWidget {
  const _MetricGrid({
    required this.metrics,
    required this.profile,
    required this.extra,
  });

  final List<(String, String)> metrics;
  final AgeProfile profile;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              for (final (label, value) in metrics)
                Expanded(
                  child: Column(
                    children: [
                      Text(
                        value,
                        textAlign: TextAlign.center,
                        style: profile.bodyStyle.copyWith(color: extra.hint),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        label,
                        style: profile.captionStyle.copyWith(color: extra.textSub),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
      );
}
