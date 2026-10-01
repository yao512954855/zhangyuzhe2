/// 逐字点亮网格 —— WP-APP-04 F2/F3/F4/F16（UI 规范 §7.7）。
///
/// 渲染规则（与契约严格一致）：
/// - `i < matched`：已背出的字 → **真实汉字**，用主文字色（清晰可辨）；
/// - `i == matched`：当前位置 → **空框**（下一步要背的字），用主色描边区分；
/// - `i > matched`：未背到的字 → **完全不显示**，只保留一点点占位空间
///   （F2「保留占位空间」，但不泄露内容）。
///
/// 标点不进网格（F4）：`ReciteText.chars` 已经只含汉字，所以这里天然不含标点；
/// 段落结构通过 `paragraphCharCounts` 保留换行，让学生对文章形状有空间记忆。
library;

import 'package:flutter/material.dart';

import '../../../core/theme/age_profile.dart';

/// 逐字点亮网格。
class CharGrid extends StatelessWidget {
  const CharGrid({
    super.key,
    required this.chars,
    required this.matched,
    required this.paragraphCharCounts,
  });

  /// 背诵范围内的全部汉字（长度 == `totalChars`）。
  final List<String> chars;

  /// 已背出的字数（`ReciteProgress.matchedChars`）。这是「已显示 / 当前框」的分界。
  final int matched;

  /// 每段字数（用于按段换行）。
  final List<int> paragraphCharCounts;

  /// 单格内文字的行高（em）。
  ///
  /// ⚠️ **不能直接用 `AgeProfile.reciteLineHeight`（2.0）**。
  /// 那个 2.0 是给**多行课文**用的行间距（UI 规范 §3.3 明文），
  /// 而这里是「一个字一个格子」，行间距没有意义，只有副作用：
  ///
  /// 原来的写法是 `SizedBox(height: reciteBig * 1.12)` 里放 `reciteStyle`
  /// （`height: 2.0`，行盒 = 2.0em > 单元 1.12em）。行盒比单元高 →
  /// `RenderParagraph` 收到 `maxHeight = 1.12em` → `size` 被压到 1.12em →
  /// `didOverflowHeight` 成立 → **它自己 `clipRect` 到 1.12em** →
  /// 每个汉字的**下沿约 40% 被裁掉**（「二」只剩一横、「已」只剩一个口）。
  /// 真机 2026-09-29 抓到；`flutter test` / `analyze` **都看不出来**
  /// （纯渲染层，无断言可测）。
  ///
  /// 修法：单元内的行盒压到 1.15em（≥ 汉字墨迹 1.0em + 上下余量），
  /// 并把单元高度同步放大到 `1.25em`（见 `_cellSizeFactor`），
  /// 让行盒**装得进**单元 → 不触发纵向溢出 → 不裁。
  static const double _cellLineHeight = 1.15;

  /// 单元边长 = `reciteBig * _cellSizeFactor`；必须 > `_cellLineHeight`。
  static const double _cellSizeFactor = 1.25;

  /// 「当前位置空框」的 key。
  ///
  /// 供测试/联调精确定位这个框，并对「框内没有任何字符」做断言（见 `build`）。
  /// 规格 F2 / UI §7.7 都写明「当前位置留**空框**」——空框就是空的，
  /// 框里不能有任何字符（连占位用的「字」都不行：那等于提前泄露一个字的信息）。
  static const Key currentCellKey = ValueKey('recite-current-cell');

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final textColor = Theme.of(context).colorScheme.onSurface;
    final boxColor = Theme.of(context).colorScheme.primary;

    // 按段切分，保留段落形状（F4：布局上保留位置感）。
    final paragraphs = <List<String>>[];
    var offset = 0;
    for (final count in paragraphCharCounts) {
      if (count <= 0) continue;
      final end = offset + count;
      if (end > chars.length) break;
      paragraphs.add(chars.sublist(offset, end));
      offset = end;
    }
    if (offset < chars.length) {
      paragraphs.add(chars.sublist(offset));
    }
    if (paragraphs.isEmpty) {
      // 拿不到正文时：仍然显示「当前位置空框」，保证背诵流程不中断。
      paragraphs.add(const []);
    }

    final cellSize = profile.reciteBig * _cellSizeFactor;
    final futureGap = profile.reciteBig * 0.45;

    // 防御：展示正文可能还没加载完（异步），此时不能让 matched 越界访问 chars。
    final safeMatched = matched.clamp(0, chars.length);

    Widget buildCell(int index) {
      if (index < safeMatched) {
        // 已背出的字：真实汉字，主文字色（与当前框用色彩区分，F16）。
        return SizedBox(
          width: cellSize,
          height: cellSize,
          child: Center(
            child: Text(
              chars[index],
              // 行高必须换成单格行高：用课文行高（2.0em）会被单元压矮后裁掉下沿。
              style: profile.reciteStyle.copyWith(
                color: textColor,
                fontWeight: FontWeight.w500,
                height: _cellLineHeight,
              ),
            ),
          ),
        );
      }
      if (index == matched) {
        // 当前位置：**空框**（规格 F2「当前位置留空框」/ UI §7.7 图示 `□`），主色描边
        // （暗色主题下也清晰，F16）。
        //
        // ⚠️ 这里**只画框、不画任何字符**。早期版本在框内画了一个灰色半透明的
        //    `Text('字')` 当占位，被独立复核发现与规格冲突：F2 既然要求「未背到的字
        //    完全不显示」，当前框里冒出一个汉字就等于提前给了一字信息；低龄学生还会
        //    把它误读成「这里要背的字就是『字』」。已按规格回退为纯空框。
        return Container(
          key: currentCellKey,
          width: cellSize,
          height: cellSize,
          decoration: BoxDecoration(
            border: Border.all(color: boxColor, width: 2.5),
            borderRadius: BorderRadius.circular(10),
            color: boxColor.withValues(alpha: 0.08),
          ),
        );
      }
      // 未背到的字：完全不显示，仅保留极小占位空间（F2）。
      return SizedBox(width: futureGap, height: cellSize);
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var p = 0; p < paragraphs.length; p++) ...[
          if (p > 0) SizedBox(height: profile.body * 1.2),
          Wrap(
            spacing: profile.caption * 0.4,
            runSpacing: profile.body * 0.5,
            children: [
              for (var i = 0; i < paragraphs[p].length; i++)
                buildCell(offsetOf(paragraphs, p) + i),
            ],
          ),
        ],
      ],
    );
  }

  int offsetOf(List<List<String>> paragraphs, int p) {
    var off = 0;
    for (var i = 0; i < p; i++) {
      off += paragraphs[i].length;
    }
    return off;
  }
}
