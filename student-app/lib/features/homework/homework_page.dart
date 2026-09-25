/// 我的作业页 —— WP-2G（二期契约 §15.2：`GET /api/me/homework`）。
///
/// 渲染**服务端**按班级 + active 过滤后的作业列表，提交状态（pending / done）
/// 由后端推导，客户端只原样呈现，不重算。空列表如实提示「还没有作业」，
/// 不拿假数据凑数。
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/feedback.dart';
import 'models.dart';
import 'repository.dart';

/// 我的作业页。
class HomeworkPage extends ConsumerWidget {
  const HomeworkPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final homework = ref.watch(myHomeworkProvider);
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Scaffold(
      appBar: AppBar(title: Text('我的作业', style: profile.pageTitleStyle)),
      body: homework.when(
        loading: () => const LoadingView(),
        error: (e, _) => ErrorView(
          message: e is ApiException ? e.displayText : '暂时拿不到作业',
          onRetry: () => ref.invalidate(myHomeworkProvider),
        ),
        data: (items) => items.isEmpty
            ? Center(
                child: Text(
                  '还没有作业',
                  style: profile.captionStyle.copyWith(color: extra.textSub),
                ),
              )
            : ListView.separated(
                padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
                itemCount: items.length,
                separatorBuilder: (context, index) => const SizedBox(height: 12),
                itemBuilder: (context, i) => _HomeworkTile(item: items[i]),
              ),
      ),
    );
  }
}

/// 单条作业。
class _HomeworkTile extends StatelessWidget {
  const _HomeworkTile({required this.item});

  final HomeworkItem item;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final done = item.status == HomeworkStatus.done;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(item.title, style: profile.bodyStyle),
                        const SizedBox(height: 4),
                        Row(
                          children: [
                            // 学科独立成文本，便于断言「英语 / 语文」原样呈现
                            Text(
                              item.subject.label,
                              style: profile.captionStyle.copyWith(color: extra.textSub),
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                '截止 ${item.dueText}',
                                style: profile.captionStyle.copyWith(color: extra.textSub),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
              decoration: BoxDecoration(
                color: done ? extra.hint.withOpacity(0.12) : extra.divider,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(
                item.status.label,
                style: profile.captionStyle.copyWith(
                  color: done ? extra.hint : extra.textSub,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
