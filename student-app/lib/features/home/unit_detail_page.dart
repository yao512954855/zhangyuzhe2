/// 单元详情 —— F14「单元与词条两级都要有状态标记；进入单元可看词条列表与各自状态」。
///
/// 英语看到的是**词条**（中文 + 英文 + 掌握状态），
/// 语文看到的是**课文**（标题 + 作者 + 背诵字数 + 上次评级）。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import '../../router/routes/home.dart';
import '../config/practice_config_page.dart';
import '../config/practice_config_controller.dart';
import '../config/practice_launcher.dart';
import '../config/practice_strategy.dart';
import 'home_controller.dart';
import 'mastery_badge.dart';
import 'models.dart';

class UnitDetailPage extends ConsumerStatefulWidget {
  const UnitDetailPage({super.key, this.unit});

  final UnitItem? unit;

  @override
  ConsumerState<UnitDetailPage> createState() => _UnitDetailPageState();
}

class _UnitDetailPageState extends ConsumerState<UnitDetailPage> {
  /// 英语：词条；语文：课文。
  Future<Object>? _future;

  /// 正在快捷开背的课文 id（防连点：一次点击会发一次创建场次的请求）。
  int? _startingTextId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    final unit = widget.unit;
    if (unit == null) {
      return;
    }
    final repo = ref.read(homeRepositoryProvider);
    setState(() {
      // 不传 mode：详情页要**完整看到两侧**（en 与 cn），
      // 而 `mode=cn2en` 会把 `en` 隐藏（那是练习页需要的裁剪）
      _future = unit.subject == Subject.english
          ? repo.fetchWords(unit.unitId)
          : repo.fetchTexts(unit.unitId);
    });
  }

  @override
  Widget build(BuildContext context) {
    final unit = widget.unit;
    if (unit == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('单元')),
        body: const EmptyView(title: '没有找到这个单元', hint: '回首页再点一次试试'),
      );
    }

    return Scaffold(
      appBar: AppBar(title: Text(unit.name)),
      body: SafeArea(
        child: FutureBuilder<Object>(
          future: _future,
          builder: (context, snapshot) {
            if (snapshot.connectionState == ConnectionState.waiting) {
              return const LoadingView();
            }
            if (snapshot.hasError) {
              final error = snapshot.error;
              return ErrorView.fromException(
                error is ApiException ? error : ApiException.server(error),
                onRetry: _load,
              );
            }
            return _list(unit, snapshot.data);
          },
        ),
      ),
      // 看完词条就能直接去练（省一次返回首页再找卡片）
      bottomNavigationBar: SafeArea(
        child: Container(
          padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
          color: Theme.of(context).scaffoldBackgroundColor,
          child: AppButton(
            label: '设置练法',
            icon: Icons.tune_rounded,
            onPressed: () => Navigator.of(context).pushNamed(
              HomeRoutes.practiceConfig,
              arguments: PracticeConfigArgs(unit: unit),
            ),
          ),
        ),
      ),
    );
  }

  /// 语文快捷开背：点课文行 → 按「选课文 + 只选这一篇」直接创建场次进背诵页。
  ///
  /// 请求体与配置页「选课文」勾一篇完全一致（`scope=lesson` + `text_ids=[id]`），
  /// 只是替学生把 5 步点选压成 1 步；「设置练法」按钮保留，想细选的照旧走配置页。
  Future<void> _quickRecite(TextBrief text) async {
    final unit = widget.unit;
    if (unit == null || _startingTextId != null) {
      return;
    }
    setState(() => _startingTextId = text.id);
    final notifier = ref.read(practiceConfigProvider.notifier);
    try {
      // start() 会重置 provider 并拉课文列表；随后只勾这一篇。
      await notifier.start(unit, scope: PracticeScope.lesson);
      notifier.setTextSelection(<int>{text.id});
      final session = await notifier.submit();
      if (!mounted) {
        return;
      }
      if (session == null) {
        final message = ref.read(practiceConfigProvider).errorText ?? '这篇暂时背不了，再试一次';
        showAppSnack(context, message);
        return;
      }
      openPracticeSession(context, session);
    } finally {
      if (mounted) {
        setState(() => _startingTextId = null);
      }
    }
  }

  Widget _list(UnitItem unit, Object? data) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    final words = data is List<WordItem> ? data : const <WordItem>[];
    final texts = data is List<TextBrief> ? data : const <TextBrief>[];
    final isEmpty = unit.subject == Subject.english ? words.isEmpty : texts.isEmpty;

    return Column(
      children: [
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(unit.progressText, style: profile.pageTitleStyle),
                  ),
                  MasteryBadge(status: unit.status, showLabel: true),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                unit.countLabel,
                style: profile.captionStyle.copyWith(color: extra.textSub),
              ),
              // 语文加一句操作指引：课文行可点直接背（点得出来才叫友好）。
              if (unit.subject == Subject.chinese) ...[
                const SizedBox(height: 4),
                Text(
                  '点任意课文直接开背；想细选练法用下面的按钮',
                  style: profile.captionStyle.copyWith(color: extra.textFaint),
                ),
              ],
            ],
          ),
        ),
        Divider(color: extra.divider, height: 1),
        Expanded(
          child: isEmpty
              ? const EmptyView(title: '这个单元还没有内容', hint: '让管理者先导入内容')
              : ListView.separated(
                  padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
                  itemCount: unit.subject == Subject.english ? words.length : texts.length,
                  separatorBuilder: (_, _) => const SizedBox(height: 10),
                  itemBuilder: (context, index) => unit.subject == Subject.english
                      ? _WordRow(index: index, word: words[index])
                      : _TextRow(
                          index: index,
                          text: texts[index],
                          starting: _startingTextId == texts[index].id,
                          onTap: () => _quickRecite(texts[index]),
                        ),
                ),
        ),
      ],
    );
  }
}

/// 一行词条：中文大字 + 英文 + 状态标记。
class _WordRow extends StatelessWidget {
  const _WordRow({required this.index, required this.word});

  final int index;
  final WordItem word;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    final secondLine = [
      if (word.en != null && word.en!.isNotEmpty) word.en!,
      if (word.phonetic != null && word.phonetic!.isNotEmpty) word.phonetic!,
      if (word.pos != null && word.pos!.isNotEmpty) word.pos!,
    ].join('  ');

    return Container(
      constraints: BoxConstraints(minHeight: profile.listItemHeight),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: extra.card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: extra.divider, width: 0.5),
      ),
      child: Row(
        children: [
          SizedBox(
            width: 28,
            child: Text(
              '${index + 1}',
              style: profile.captionStyle.copyWith(color: extra.textFaint),
            ),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(word.displayTitle, style: profile.bodyStyle),
                if (secondLine.isNotEmpty) ...[
                  const SizedBox(height: 2),
                  Text(
                    secondLine,
                    style: profile.captionStyle.copyWith(color: extra.textSub),
                    maxLines: 2,
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(width: 8),
          MasteryBadge(status: word.masteryStatus),
        ],
      ),
    );
  }
}

/// 一行课文。**整行可点 = 直接背这一篇**（2026-10-01：原先只是死卡片，
/// 想背第二课要「设置练法 → 选课文 → 勾选 → 开始」点 5 下，太绕）。
class _TextRow extends StatelessWidget {
  const _TextRow({
    required this.index,
    required this.text,
    this.onTap,
    this.starting = false,
  });

  final int index;
  final TextBrief text;

  /// 点击整行 → 快捷单篇背诵；`null` 时退化为不可点的展示行（测试兜底）。
  final VoidCallback? onTap;

  /// 这一篇正在创建场次（防连点 + 转圈反馈）。
  final bool starting;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;

    final subtitle = [
      if (text.author != null && text.author!.isNotEmpty) text.author!,
      if (text.reciteScopeChars > 0) '背诵 ${text.reciteScopeChars} 字',
      if (text.hasRecited) '上次 ${text.lastGrade}' else '还没背过',
    ].join(' · ');

    return Material(
      color: extra.card,
      borderRadius: BorderRadius.circular(12),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        child: Container(
          constraints: BoxConstraints(minHeight: profile.listItemHeight),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: extra.divider, width: 0.5),
          ),
          child: Row(
            children: [
              SizedBox(
                width: 28,
                child: Text(
                  '${index + 1}',
                  style: profile.captionStyle.copyWith(color: extra.textFaint),
                ),
              ),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(text.title, style: profile.bodyStyle),
                    const SizedBox(height: 2),
                    Text(
                      subtitle,
                      style: profile.captionStyle.copyWith(color: extra.textSub),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              // 语文没有词条级的掌握度（契约 §7.2 的 `word_mastery` 是词条表），
              // 所以课文状态只区分"背过 / 没背过"，具体表现是评级文字。
              MasteryBadge(
                status: text.hasRecited ? MasteryStatus.practicedOk : MasteryStatus.notStarted,
              ),
              const SizedBox(width: 6),
              // 「点我能背」的可见反馈：麦克风图标 + 转圈态。
              starting
                  ? SizedBox(
                      width: profile.caption + 8,
                      height: profile.caption + 8,
                      child: const CircularProgressIndicator(strokeWidth: 2),
                    )
                  : Icon(
                      Icons.mic_rounded,
                      size: profile.caption + 8,
                      color: palette.primary.withValues(alpha: 0.7),
                    ),
            ],
          ),
        ),
      ),
    );
  }
}
