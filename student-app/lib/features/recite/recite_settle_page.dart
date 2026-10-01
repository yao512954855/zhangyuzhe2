/// 语文背诵结算页 —— WP-APP-04 F13 / F14（UI 规范 §7.8 P9）。
///
/// 展示六项指标 + 评级 + 回看：
/// - 六项（F13）：总字数、已背字数、漏字数、提示次数（分档）、总用时、首次发声延迟；
/// - 评级（F13）：优秀 / 良好 / 合格 / 待加强（文字 + 星星双通道，色盲友好）；
/// - 回看（F14）：**展示全文**，已背出的字 / 没背出来的字用不同颜色区分。
///
/// 结算数字以服务端重算为准（契约 §13：客户端上报的 matched_chars 服务端丢弃），
/// 这里只做"展示"，不再算一遍。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/age_profile.dart';
import '../../../core/theme/app_theme.dart';
import '../../../core/widgets/app_button.dart';
import 'recite_controller.dart';

/// 回看全文的行高。
///
/// 比正文默认的 1.7 略大：漏字要带浅色底纹（`TextStyle.backgroundColor`），
/// 底色画的是**行盒**，行高太小会让相邻两行的底纹贴在一起糊成一片。
const double _reviewLineHeight = 1.9;

/// 结算页。
class ReciteSettlePage extends ConsumerWidget {
  const ReciteSettlePage({
    super.key,
    required this.settle,
    required this.durationMs,
    this.firstVoiceMs,
    this.hasNext = false,
    this.fullChars = const <String>[],
    this.paragraphCharCounts = const <int>[],
  });

  /// 服务端重算结果（六项指标 + 评级 + 漏字）。
  final ReciteSettle settle;

  /// 总用时（毫秒），由背诵页计时得到（服务端 settle 不回带，见 D-APP04-03）。
  final int durationMs;

  /// 首次发声延迟（毫秒，可能为空——没发声过）。
  final int? firstVoiceMs;

  /// 是否还有下一篇（一期单篇，固定 false）。
  final bool hasNext;

  /// 背诵范围内的全文汉字（顺序 == 服务端 `target_chars`）。
  ///
  /// 由背诵页把**已加载**的 [ReciteText] 传进来（同源 `/api/texts/{id}` 切片），
  /// 保证「服务端下标」与「屏幕上第几个字」严格对齐。为空 = 没拿到正文，
  /// 回看退回逐字清单（见 [_ReviewSection]）。
  final List<String> fullChars;

  /// 每段字数（保留段落换行；为空则不分段）。
  final List<int> paragraphCharCounts;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(title: const Text('背完啦')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
          children: [
            _RatingHeader(grade: settle.grade, score: settle.score),
            const SizedBox(height: AgeProfile.elementGap),

            _MetricsGrid(
              totalChars: settle.totalChars,
              matchedChars: settle.matchedChars,
              missedCount: settle.missedCount,
              hintCount: settle.hintCount,
              durationMs: durationMs,
              firstVoiceMs: firstVoiceMs,
              coveredRatio: settle.coveredRatio,
            ),
            const SizedBox(height: AgeProfile.elementGap),

            _ReviewSection(
              settle: settle,
              fullChars: fullChars,
              paragraphCharCounts: paragraphCharCounts,
            ),
            const SizedBox(height: AgeProfile.adjacentButtonGap),

            _LeaveButton(
              label: '返回首页',
              icon: Icons.home_rounded,
              onTap: () => Navigator.of(context).popUntil((r) => r.isFirst),
            ),
            if (hasNext)
              Padding(
                padding: const EdgeInsets.only(top: AgeProfile.adjacentButtonGap),
                child: _LeaveButton(
                  label: '下一篇',
                  kind: AppButtonKind.secondary,
                  onTap: () => Navigator.of(context).pop(),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// 评级头部：文字 + 星星（双通道，色盲也能读懂）。
class _RatingHeader extends StatelessWidget {
  const _RatingHeader({required this.grade, required this.score});

  final String grade;
  final double score;

  int get _stars {
    return switch (grade) {
      '优秀' => 4,
      '良好' => 3,
      '合格' => 2,
      '待加强' => 1,
      _ => 0,
    };
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;
    final filled = _stars > 0 ? extra.success : extra.textFaint;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          children: [
            Text(
              grade.isEmpty ? '已提交' : grade,
              style: profile.pageTitleStyle.copyWith(
                color: palette.primary,
                fontWeight: FontWeight.w500,
              ),
            ),
            const SizedBox(height: 8),
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                for (var i = 0; i < 4; i++)
                  Icon(
                    i < _stars ? Icons.star_rounded : Icons.star_outline_rounded,
                    color: i < _stars ? filled : extra.divider,
                    size: profile.caption + 14,
                  ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              '得分 ${score.toStringAsFixed(0)}',
              style: profile.captionStyle.copyWith(color: extra.textSub),
            ),
          ],
        ),
      ),
    );
  }
}

/// 六项指标网格。
class _MetricsGrid extends StatelessWidget {
  const _MetricsGrid({
    required this.totalChars,
    required this.matchedChars,
    required this.missedCount,
    required this.hintCount,
    required this.durationMs,
    required this.firstVoiceMs,
    required this.coveredRatio,
  });

  final int totalChars;
  final int matchedChars;
  final int missedCount;
  final Map<int, int> hintCount;
  final int durationMs;
  final int? firstVoiceMs;
  final double coveredRatio;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    final h1 = hintCount[1] ?? 0;
    final h2 = hintCount[2] ?? 0;
    final h3 = hintCount[3] ?? 0;
    final hintText = (h1 + h2 + h3) == 0
        ? '未使用'
        : '一档 $h1 · 二档 $h2 · 三档 $h3';

    final tiles = <_MetricTile>[
      _MetricTile(label: '总字数', value: '$totalChars'),
      _MetricTile(label: '已背字数', value: '$matchedChars'),
      _MetricTile(label: '漏字数', value: '$missedCount'),
      _MetricTile(label: '提示次数', value: hintText, small: true),
      _MetricTile(label: '总用时', value: _fmtDuration(durationMs)),
      _MetricTile(
        label: '首次发声延迟',
        value: firstVoiceMs == null ? '未发声' : _fmtDuration(firstVoiceMs!),
      ),
    ];

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('这一遍的成绩', style: profile.cardTitleStyle),
            const SizedBox(height: 12),
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: [
                for (final t in tiles)
                  SizedBox(
                    width: (MediaQuery.of(context).size.width -
                            AgeProfile.screenEdgeInset * 2 -
                            16 * 2 -
                            12) /
                        2,
                    child: t,
                  ),
              ],
            ),
            const SizedBox(height: 14),
            // 覆盖率进度条（颜色表达，不闪烁）。
            Text('覆盖率 ${(coveredRatio * 100).toStringAsFixed(0)}%',
                style: profile.captionStyle.copyWith(color: extra.textSub)),
            const SizedBox(height: 6),
            LinearProgressIndicator(
              value: coveredRatio.clamp(0.0, 1.0),
              minHeight: 8,
              borderRadius: BorderRadius.circular(4),
              color: extra.success,
              backgroundColor: extra.divider,
            ),
          ],
        ),
      ),
    );
  }

  static String _fmtDuration(int ms) {
    final total = (ms / 1000).round();
    final m = total ~/ 60;
    final s = total % 60;
    return '${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }
}

class _MetricTile extends StatelessWidget {
  const _MetricTile({required this.label, required this.value, this.small = false});

  final String label;
  final String value;
  final bool small;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: profile.captionStyle.copyWith(color: extra.textFaint)),
        const SizedBox(height: 4),
        Text(
          value,
          style: (small ? profile.bodyStyle : profile.cardTitleStyle)
              .copyWith(color: Theme.of(context).colorScheme.onSurface),
        ),
      ],
    );
  }
}

/// 回看（F14）—— **全文着色**。
///
/// **产品调整（2026-09-29，姚工）**：原 UI 规范 §7.8 P9 写的是「展开后逐条列出
/// 漏字位置」，但一个字一行的清单看不出上下文 —— 学生知道自己漏了哪些字，
/// 却看不出漏在第几句、前后是什么。改为**展示全文**，把「背出来的字」与
/// 「没背出来的字」用不同颜色标出来，一眼就能定位卡在哪。
///
/// 全文与背诵页同源（`GET /api/texts/{id}` 按背诵范围切片，见 `recite_text.dart`），
/// 逐字对应服务端 `target_chars`。这里有一条**硬护栏**：只有
/// `fullChars.length == settle.totalChars` 时才画全文 —— 不等说明两份文本不同源，
/// 着色会把「服务端第 N 个下标」画到别的字头上，宁可不画。
class _ReviewSection extends StatelessWidget {
  const _ReviewSection({
    required this.settle,
    required this.fullChars,
    required this.paragraphCharCounts,
  });

  final ReciteSettle settle;
  final List<String> fullChars;
  final List<int> paragraphCharCounts;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    final missed = <int>{for (final m in settle.missedDetail) m.pos};
    final hasFull =
        fullChars.isNotEmpty && fullChars.length == settle.totalChars;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('回看没背出来的地方', style: profile.cardTitleStyle),
            const SizedBox(height: 10),
            if (missed.isEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Text(
                  '这一遍没有漏字，真棒！',
                  style: profile.bodyStyle.copyWith(color: extra.success),
                ),
              ),
            if (hasFull) ...[
              if (missed.isNotEmpty) ...[
                const _ReviewLegend(),
                const SizedBox(height: 10),
              ],
              _FullText(
                chars: fullChars,
                missed: missed,
                paragraphCharCounts: paragraphCharCounts,
              ),
            ] else if (missed.isNotEmpty)
              // 兜底：正文没拿到（或与服务端字数不一致）时，退回逐字清单。
              _MissedList(detail: settle.missedDetail),
          ],
        ),
      ),
    );
  }
}

/// 图例 —— 颜色之外再给一次文字说明（不靠单色传达信息）。
class _ReviewLegend extends StatelessWidget {
  const _ReviewLegend();

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    return Wrap(
      spacing: 18,
      runSpacing: 6,
      children: [
        _LegendItem(color: Theme.of(context).colorScheme.onSurface, label: '背出来的字'),
        _LegendItem(color: extra.error, label: '没背出来的字'),
      ],
    );
  }
}

class _LegendItem extends StatelessWidget {
  const _LegendItem({required this.color, required this.label});

  final Color color;
  final String label;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 12,
          height: 12,
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(3),
          ),
        ),
        const SizedBox(width: 6),
        Text(label, style: profile.captionStyle.copyWith(color: extra.textSub)),
      ],
    );
  }
}

/// 全文（按段换行），背出来的字用正文色、没背出来的字用强调色 + 浅底。
class _FullText extends StatelessWidget {
  const _FullText({
    required this.chars,
    required this.missed,
    required this.paragraphCharCounts,
  });

  /// 回看全文的 Key（测试据此取 [TextSpan] 校验着色）。
  static const Key reviewTextKey = Key('reciteReviewText');

  final List<String> chars;
  final Set<int> missed;
  final List<int> paragraphCharCounts;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    final base = profile.bodyStyle.copyWith(height: _reviewLineHeight);
    final matchedStyle = base.copyWith(color: Theme.of(context).colorScheme.onSurface);
    final missedStyle = base.copyWith(
      color: extra.error,
      fontWeight: FontWeight.w600,
      backgroundColor: extra.errorLight,
    );

    return Text.rich(
      TextSpan(style: base, children: _spans(matchedStyle, missedStyle)),
      key: reviewTextKey,
    );
  }

  /// 把逐字状态压成 **同色连排** 的 [TextSpan]，并按段插入换行。
  List<InlineSpan> _spans(TextStyle matchedStyle, TextStyle missedStyle) {
    // 每段起始的字符下标（在这些位置前换行）。
    final breaks = <int>{};
    var acc = 0;
    for (final n in paragraphCharCounts) {
      if (n <= 0) continue;
      acc += n;
      if (acc < chars.length) breaks.add(acc);
    }

    final spans = <InlineSpan>[];
    final buf = StringBuffer();
    bool? current;

    void flush() {
      if (buf.isEmpty) return;
      spans.add(TextSpan(
        text: buf.toString(),
        style: current! ? missedStyle : matchedStyle,
      ));
      buf.clear();
    }

    for (var i = 0; i < chars.length; i++) {
      if (breaks.contains(i)) {
        flush();
        spans.add(const TextSpan(text: '\n'));
      }
      final m = missed.contains(i);
      if (current != null && current != m) flush();
      current = m;
      buf.write(chars[i]);
    }
    flush();
    return spans;
  }
}

/// 兜底：正文拿不到时的逐字清单（UI 规范 §7.8 P9 原形态）。
class _MissedList extends StatelessWidget {
  const _MissedList({required this.detail});

  final List<ReciteMissedChar> detail;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final m in detail)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Row(
              children: [
                Icon(Icons.radio_button_unchecked_rounded,
                    size: profile.caption + 4, color: extra.error),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '第 ${m.pos + 1} 个字：${m.char ?? '？'}',
                    style: profile.bodyStyle
                        .copyWith(color: Theme.of(context).colorScheme.onSurface),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

/// 离开结算页的按钮 —— **点一次即锁**。
///
/// 结算页是无状态组件，幂等标记只能由按钮自己持有：转场期间连点会重复
/// `pop()`，在真机上撞 `navigator._debugLocked` 断言（与 2026-09-28 英语
/// 练习页「跳结算」同一个坑，见 dev/02 §八 #95）。
class _LeaveButton extends StatefulWidget {
  const _LeaveButton({
    required this.label,
    required this.onTap,
    this.kind = AppButtonKind.primary,
    this.icon,
  });

  final String label;
  final VoidCallback onTap;
  final AppButtonKind kind;
  final IconData? icon;

  @override
  State<_LeaveButton> createState() => _LeaveButtonState();
}

class _LeaveButtonState extends State<_LeaveButton> {
  bool _tapped = false;

  @override
  Widget build(BuildContext context) {
    return AppButton(
      label: widget.label,
      kind: widget.kind,
      icon: widget.icon,
      onPressed: _tapped
          ? null
          : () {
              setState(() => _tapped = true);
              widget.onTap();
            },
    );
  }
}
