/// 背诵事件回放页 —— WP-2E（`GET /api/me/recitations/{id}/events`）。

/// 把一期**只写不读**的 `recitation_events` 读出来做回放：按 `seq` 升序展示
/// 学生背诵过程中发生的每一个事件（点亮 / 漏背 / 提示 / 重背 / 环境声）及其位置、
/// 发生时刻。

/// ⚠️ **入口说明（受独占文件约束）**：本页是独立、可由路由接入的回放页，
/// 但从「我的记录」里已结束的语文背诵场次**点进来**的入口按钮写在 APP-05 的
/// `my_records_page.dart` / `session_records.dart` 里（独占文件，本次不动），
/// 命名路由也归 `router/routes/mistakes.dart`（APP-01 占位）所有。
/// 因此本包只交付**完整可用的页面 + 组件测试**，并请对应包的所有者补一个
/// 「查看回放」入口 + 一条 `/mistakes/recite-replay` 路由（详见交付报告）。

library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/feedback.dart';

/// 单条回放事件（`recitation_events` 读出的核心字段，契约 §15.2）。
class ReciteReplayEvent {
  const ReciteReplayEvent({
    required this.seq,
    required this.pos,
    required this.eventType,
    this.atMs,
  });

  factory ReciteReplayEvent.fromJson(Map<String, dynamic> json) => ReciteReplayEvent(
        seq: json['seq'] as int? ?? 0,
        pos: json['pos'] as int? ?? 0,
        eventType: (json['event_type'] as String?) ?? 'noise',
        atMs: json['at_ms'] as int?,
      );

  final int seq;
  final int pos;
  final String eventType;

  /// 事件发生时的毫秒偏移（`elapsed_ms`）；一期引擎多为 `null`。
  final int? atMs;
}

/// `event_type` → 中文标签（不暴露内部枚举名给学生）。
const Map<String, String> _eventLabels = {
  'matched': '点亮',
  'missed': '漏背',
  'hint': '提示',
  'restart': '重背',
  'noise': '环境声',
};

/// `event_type` → 强调色（用主题 `hint` 派生，避免硬编码）。
String eventLabel(String type) => _eventLabels[type] ?? '其他';

/// 拉取一次背诵的全部事件（契约 §15.2，按 `seq` 升序）。
///
/// 用 `Notifier<AsyncValue>` 而非裸 `FutureProvider` / `AsyncNotifier`：
/// riverpod 对「reject 的 future」会在被重读（页面任意一次重建）时重新执行
/// `create`，导致 10004 这类错误态被无限重发、UI 抖动。这里 `build` 同步返回
/// 初始态、异步拉取结果写进 `state`，**future 本身不 reject**，状态稳定停留；
/// 重试调用 notifier 的 [ReciteReplayController.retry]。
final reciteReplayProvider = NotifierProvider.family<
    ReciteReplayController,
    AsyncValue<Map<String, dynamic>>,
    int>(ReciteReplayController.new);

class ReciteReplayController
    extends Notifier<AsyncValue<Map<String, dynamic>>> {
  ReciteReplayController(this.id);

  /// 家族参数：要回放的背诵记录 id。
  final int id;

  @override
  AsyncValue<Map<String, dynamic>> build() {
    // 初始触发一次拉取；build 同步返回初始态，不抛出，故不会触发重拉循环。
    _fetch();
    return const AsyncLoading();
  }

  Future<void> _fetch() async {
    state = const AsyncLoading();
    try {
      // 用 `read` 而非 `watch` 取 client：client 身份在单页生命周期内稳定。
      final data = await ref.read(apiClientProvider).get<Map<String, dynamic>>(
        '/api/me/recitations/$id/events',
      );
      state = AsyncValue.data(data ?? const <String, dynamic>{});
    } on ApiException catch (e, st) {
      state = AsyncValue.error(e, st);
    }
  }

  /// 重试：重新拉取。
  Future<void> retry() => _fetch();
}

/// 背诵回放页。
///
/// - [recitationId]：要回放的背诵记录 id（必填）；
/// - [title]：可选课文标题，仅用于 AppBar 展示。
class ReciteReplayPage extends ConsumerWidget {
  const ReciteReplayPage({
    required this.recitationId,
    this.title,
    super.key,
  });

  final int recitationId;
  final String? title;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final state = ref.watch(reciteReplayProvider(recitationId));

    final String appBarTitle =
        title != null ? '背诵回放 · $title' : '背诵回放';

    return Scaffold(
      appBar: AppBar(title: Text(appBarTitle, style: profile.pageTitleStyle)),
      body: state.when(
        loading: () => const LoadingView(),
        error: (e, _) => ErrorView(
          message: e is ApiException ? e.displayText : '回放暂时打不开',
          onRetry: () =>
              ref.read(reciteReplayProvider(recitationId).notifier).retry(),
        ),
        data: (data) => _Body(
          data: data,
          profile: profile,
          extra: extra,
        ),
      ),
    );
  }
}

class _Body extends StatelessWidget {
  const _Body({
    required this.data,
    required this.profile,
    required this.extra,
  });

  final Map<String, dynamic> data;
  final AgeProfile profile;
  final AppExtraColors extra;

  List<ReciteReplayEvent> get _events {
    final raw = data['events'];
    if (raw is! List) {
      return const [];
    }
    return (raw as List)
        .map((e) => ReciteReplayEvent.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    final events = _events;

    if (events.isEmpty) {
      return Padding(
        padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
        child: Center(
          child: Text(
            '这次背诵没有可回放的事件',
            style: profile.bodyStyle.copyWith(color: extra.textSub),
          ),
        ),
      );
    }

    return ListView(
      padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
      children: events
          .map((e) => _EventRow(event: e, profile: profile, extra: extra))
          .toList(),
    );
  }
}

/// 单条事件行。
class _EventRow extends StatelessWidget {
  const _EventRow({
    required this.event,
    required this.profile,
    required this.extra,
  });

  final ReciteReplayEvent event;
  final AgeProfile profile;
  final AppExtraColors extra;

  String get _timeLabel {
    final ms = event.atMs;
    if (ms == null) {
      return '—';
    }
    return '${(ms / 1000).toStringAsFixed(1)}s';
  }

  @override
  Widget build(BuildContext context) => Card(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Row(
            children: [
              _TypeDot(type: event.eventType, extra: extra),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(eventLabel(event.eventType), style: profile.bodyStyle),
                    const SizedBox(height: 2),
                    Text(
                      '第 ${event.pos} 字',
                      style: profile.captionStyle.copyWith(color: extra.textSub),
                    ),
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text('#${event.seq}', style: profile.captionStyle.copyWith(color: extra.textFaint)),
                  Text(_timeLabel, style: profile.captionStyle.copyWith(color: extra.textSub)),
                ],
              ),
            ],
          ),
        ),
      );
}

/// 事件类型小圆点（颜色用主题 token，不写死）。
class _TypeDot extends StatelessWidget {
  const _TypeDot({required this.type, required this.extra});

  final String type;
  final AppExtraColors extra;

  @override
  Widget build(BuildContext context) {
    // 用 `hint` 派生色统一表现，避免引入与主题无关的高饱和色（护眼硬性要求）。
    final color = switch (type) {
      'matched' => extra.hint,
      'missed' => extra.error,
      'hint' => extra.hint,
      'restart' => extra.textSub,
      _ => extra.textFaint,
    };
    return Container(
      width: 12,
      height: 12,
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.circular(6),
      ),
    );
  }
}
