/// 听写结果页 —— WP-D D-2 第 4 项 + APP-13 F17-3（契约 v1.3）。
///
/// 对 X · 错 Y + 逐题对错清单（错题显示 recognized 实际识别值；
/// uncertain 标「拿不准」）；错题逐个 `POST /api/wrongbook/auto-add`
/// （mode=listen，契约 §7）；**手动退出** —— 批改完成后结果页常驻，
/// 学生点「完成」才跳回单元列表（todo11 A4：单词多时来得及慢慢看）。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/widgets/app_button.dart';
import 'dictation_models.dart';

/// 听写结果页。
class DictationResultPage extends ConsumerStatefulWidget {
  const DictationResultPage({
    super.key,
    required this.report,
    required this.items,
    this.unitName = '',
    this.returnRoute,
    /// 测试注入错题入本的假实现（避免真网络）；为空则走真实 API。
    this.wrongAdder,
  });

  final DictationGradeReport report;

  /// 本次听写的题目（错题按 en 找 item_id 入错题本）。
  final List<DictationItem> items;
  final String unitName;

  /// 完成后跳回的路由名（单元列表，J1 接线时由 WP-C 传入）；空则退回栈底。
  final String? returnRoute;

  /// 测试注入：错题入本 `(item_id, mode)`。
  final Future<void> Function(int itemId, String mode)? wrongAdder;

  @override
  ConsumerState<DictationResultPage> createState() =>
      _DictationResultPageState();
}

class _DictationResultPageState extends ConsumerState<DictationResultPage> {
  int _wrongAdded = 0;
  int _wrongAddFailed = 0;
  bool _wrongBookSettled = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        _addWrongItems();
      }
    });
  }

  /// 错题逐个入错题本（mode=listen）。失败不阻塞退出，只计数提示。
  Future<void> _addWrongItems() async {
    if (_wrongBookSettled) {
      return;
    }
    _wrongBookSettled = true;
    final itemsByEn = {
      for (final it in widget.items) it.en.toLowerCase(): it,
    };
    for (final result in widget.report.results) {
      if (result.correct) {
        continue;
      }
      final item = itemsByEn[result.word.toLowerCase()];
      if (item == null) {
        _wrongAddFailed++;
        continue;
      }
      try {
        final adder = widget.wrongAdder;
        if (adder != null) {
          await adder(item.id, 'listen');
        } else {
          await ref.read(apiClientProvider).post<dynamic>(
                '/api/wrongbook/auto-add',
                body: {'item_id': item.id, 'mode': 'listen'},
              );
        }
        _wrongAdded++;
      } catch (_) {
        _wrongAddFailed++;
      }
      if (mounted) {
        setState(() {});
      }
    }
  }

  /// 「完成」手动退出（F17-3：移除自动跳转）。
  void _finish() {
    if (!mounted) {
      return;
    }
    final returnRoute = widget.returnRoute;
    if (returnRoute != null && returnRoute.isNotEmpty) {
      // 清栈回单元列表（契约 §9：清栈一律 pushNamedAndRemoveUntil）。
      Navigator.of(context)
          .pushNamedAndRemoveUntil(returnRoute, (route) => false);
    } else {
      // 未配置回跳路由（直接驱动页面的场景）：退到栈底。
      Navigator.of(context).popUntil((route) => route.isFirst);
    }
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final report = widget.report;
    return PopScope(
      canPop: true,
      child: Scaffold(
        appBar: AppBar(
          title: Text(
            widget.unitName.isEmpty ? '听写结果' : '听写结果 · ${widget.unitName}',
          ),
        ),
        body: SafeArea(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
                child: Column(
                  children: [
                    Text(
                      '对 ${report.correctCount} · 错 ${report.wrongCount}',
                      style: profile.pageTitleStyle,
                    ),
                    const SizedBox(height: AgeProfile.elementGap / 2),
                    Text(
                      _wrongAddFailed == 0
                          ? '错题已记入错题本 $_wrongAdded 个，看完点「完成」'
                          : '错题记入 $_wrongAdded 个、没记上 $_wrongAddFailed 个，看完点「完成」',
                      style: profile.captionStyle,
                    ),
                  ],
                ),
              ),
              Expanded(
                child: ListView.separated(
                  padding: const EdgeInsets.symmetric(
                    horizontal: AgeProfile.screenEdgeInset,
                  ),
                  itemCount: report.results.length,
                  separatorBuilder: (_, _) =>
                      const SizedBox(height: AgeProfile.elementGap / 2),
                  itemBuilder: (context, index) {
                    final r = report.results[index];
                    return _ResultCard(result: r);
                  },
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(
                  AgeProfile.screenEdgeInset,
                  0,
                  AgeProfile.screenEdgeInset,
                  AgeProfile.elementGap,
                ),
                child: AppButton(
                  label: '完成',
                  onPressed: _finish,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 单词结果卡。
class _ResultCard extends StatelessWidget {
  const _ResultCard({required this.result});

  final GradeWordResult result;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    return Card(
      margin: EdgeInsets.zero,
      child: ListTile(
        leading: Icon(
          result.correct
              ? Icons.check_circle_rounded
              : Icons.cancel_rounded,
          color: result.correct ? null : Theme.of(context).colorScheme.error,
        ),
        title: Text(result.word, style: profile.cardTitleStyle),
        subtitle: result.correct
            ? null
            : Text(
                result.recognized.isEmpty
                    ? '没认出你写的字'
                    : '认成：${result.recognized}',
                style: profile.captionStyle,
              ),
        trailing: result.uncertain
            ? Tooltip(
                message: '机器对这一格拿不准，请自己再核对一下',
                child: Chip(
                  label: Text('拿不准', style: profile.captionStyle),
                  visualDensity: VisualDensity.compact,
                ),
              )
            : null,
      ),
    );
  }
}
