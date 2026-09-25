/// 间隔重复复习页 —— WP-2F（二期契约 §15.2：`GET /api/me/srs/due` /
/// `POST /api/me/srs/review`）。
///
/// 渲染**服务端**算好的到期复习列表，学生逐条点「记得 / 忘了」，客户端只把
/// 质量分（grade 0–5）回传，**SM-2 推进完全由后端完成**（契约禁止客户端判分/算差异）。
/// 空列表如实提示「今天没有要复习的」——绝不拿假数据凑个数。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/feedback.dart';
import 'srs_models.dart';
import 'srs_repository.dart';

/// 复习页（到期列表 + 逐条复习）。
class SrsPage extends ConsumerWidget {
  const SrsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final due = ref.watch(srsDueProvider);
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Scaffold(
      appBar: AppBar(title: Text('复习', style: profile.pageTitleStyle)),
      body: due.when(
        loading: () => const LoadingView(),
        error: (e, _) => ErrorView(
          message: e is ApiException ? e.displayText : '暂时拿不到复习计划',
          onRetry: () => ref.invalidate(srsDueProvider),
        ),
        data: (page) => page.items.isEmpty
            ? Center(
                child: Text(
                  '今天没有要复习的',
                  style: profile.captionStyle.copyWith(color: extra.textSub),
                ),
              )
            : ListView.separated(
                padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
                itemCount: page.items.length,
                separatorBuilder: (context, index) => const SizedBox(height: 12),
                itemBuilder: (context, i) => _SrsTile(item: page.items[i]),
              ),
      ),
    );
  }
}

/// 单条复习项：展示词条 + 「记得 / 忘了」两档质量分。
class _SrsTile extends ConsumerStatefulWidget {
  const _SrsTile({required this.item});

  final SrsItem item;

  @override
  ConsumerState<_SrsTile> createState() => _SrsTileState();
}

class _SrsTileState extends ConsumerState<_SrsTile> {
  bool _busy = false;

  Future<void> _review(int grade) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await ref
          .read(srsRepositoryProvider)
          .review(wordId: widget.item.wordId, grade: grade);
      // 复习成功：让到期列表重新拉取（SM-2 推进后的下一条/下一波由后端决定）。
      if (mounted) ref.invalidate(srsDueProvider);
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(e.displayText)),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final item = widget.item;
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(item.en ?? '（缺少英文）', style: profile.wordStyle),
                ),
                if (item.unitName != null)
                  Padding(
                    padding: const EdgeInsets.only(left: 8),
                    child: Text(
                      item.unitName!,
                      style: profile.captionStyle.copyWith(color: extra.textSub),
                    ),
                  ),
              ],
            ),
            if (item.cn != null) ...[
              const SizedBox(height: 4),
              Text(item.cn!, style: profile.bodyStyle),
            ],
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: ElevatedButton(
                    onPressed: _busy ? null : () => _review(5),
                    child: _busy
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Text('记得'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton(
                    onPressed: _busy ? null : () => _review(0),
                    child: const Text('忘了'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
