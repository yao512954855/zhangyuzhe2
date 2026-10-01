/// 结算页 —— F16 / P9。
///
/// 渲染 `POST /api/sessions/{id}/finish` 的响应（服务端重算，客户端只渲染）：
/// 正确率、题量、用时、提示次数、错题列表（你的答案 vs 正确答案）。
/// 不弹模态框、配色与字号走护眼 / 分龄规范。
///
/// 「再练一次」= **按原配置真重开**（2026-09-29 姚工拍板）：
/// 用组卷时留下的原请求体重新 `POST /api/sessions`，清掉上一场练习态后直进
/// 练习页；不再像以前那样只 `popUntil` 回首页（那等于「再练一次」没实现）。
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../../core/theme/app_theme.dart';
import '../../core/theme/age_profile.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import '../config/practice_session.dart';
import 'practice_controller.dart';

/// 结算页入参（经路由 `arguments` 传递）。
class SettleArguments {
  const SettleArguments({
    required this.settle,
    this.unitName,
    this.replayRequest,
  });

  /// 兜底空构造（路由未带参数时，避免崩）。
  const SettleArguments.empty()
      : settle = null,
        unitName = null,
        replayRequest = null;

  final PracticeSettle? settle;
  final String? unitName;

  /// 「再练一次」用：原组卷请求体（**本地携带**，不在服务端响应里）。
  /// 为 null 时按钮**如实降级**为「回首页」，不假装能重开。
  final Map<String, dynamic>? replayRequest;
}

/// 英语练习结算页。
class SettlePage extends ConsumerStatefulWidget {
  const SettlePage({super.key, required this.args});

  final SettleArguments args;

  /// 练习页路由（= `PracticeRoutes.practice` 的字面量）。
  ///
  /// ⚠️ 故意**不 import 路由文件**：`router/routes/practice.dart` 为了构造
  /// `SettlePage` 必须 import 本文件，本文件再反向 import 就成循环依赖。
  /// 两者一致性由单测钉住（`test/english_settle_test.dart`）。
  static const practiceRoute = '/practice';

  @override
  ConsumerState<SettlePage> createState() => _SettlePageState();
}

class _SettlePageState extends ConsumerState<SettlePage> {
  /// 重开进行中：**按钮必须失效**，否则连点会重复组卷、重复 push
  /// （真机上会撞 `navigator._debugLocked` 断言）。
  bool _busy = false;

  SettleArguments get _args => widget.args;

  /// 本页**进场转场是否已完成**。
  ///
  /// ⚠️ 原实现直接读 `ModalRoute.of(context)?.animation?.status == forward/reverse`
  /// 当「转场中」闸（2026-09-29 #98 引入）。2026-10-01 真机实锤其残留缺陷：
  /// 本页经 `pushReplacementNamed` 进来，真机上路由动画状态可能**卡在中间态**
  /// （status 永远停在 forward，导航器锁不复位的后遗症）→ 闸永远关死，
  /// 「再练一次」「回到首页」**全部静默 return**（点了没反应、logcat 无异常）。
  ///
  /// 改为状态监听 + 超时兜底：
  /// - 正常：进场动画 completed（约 300ms）→ 解锁；
  /// - 异常卡死：1.5s 后无条件解锁（此后 push 也安全——锁早已不复存在）。
  bool _entered = false;
  Timer? _entryGuard;

  Animation<void>? _routeAnim;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final anim = ModalRoute.of(context)?.animation;
    if (!identical(anim, _routeAnim)) {
      _routeAnim?.removeStatusListener(_onRouteAnimStatus);
      _routeAnim = anim;
      _routeAnim?.addStatusListener(_onRouteAnimStatus);
    }
    // 兜底：万一动画状态永远不 completed（卡死场景），1.5s 后照常解锁。
    // 正常路径 300ms 转场完成即解锁，这个定时器只会空跑一次。
    _entryGuard ??= Timer(const Duration(milliseconds: 1500), _markEntered);
    if (anim?.status == AnimationStatus.completed) {
      _markEntered();
    }
  }

  void _onRouteAnimStatus(AnimationStatus status) {
    if (status == AnimationStatus.completed) _markEntered();
  }

  void _markEntered() {
    if (!mounted || _entered) return;
    setState(() => _entered = true);
  }

  @override
  void dispose() {
    _entryGuard?.cancel();
    _routeAnim?.removeStatusListener(_onRouteAnimStatus);
    super.dispose();
  }

  static String _formatDuration(int ms) {
    final totalSec = (ms / 1000).round();
    final m = totalSec ~/ 60;
    final s = totalSec % 60;
    if (m <= 0) return '$s 秒';
    return '$m 分 ${s.toString().padLeft(2, '0')} 秒';
  }

  /// 「再练一次」：按原配置重新组卷 → 清场 → 直进练习页（真重开）。
  Future<void> _restart() async {
    final request = _args.replayRequest;
    if (_busy || request == null || !_entered) return;
    setState(() => _busy = true);
    // 导航成功（页面即将被替换）时不能复位 `_busy`，否则帧内连点会重复组卷。
    var navigating = false;
    try {
      final data = await ref.read(apiClientProvider).post<Map<String, dynamic>>(
            '/api/sessions',
            body: request,
          );
      final session = PracticeSession.fromJson(data ?? const <String, dynamic>{})
          .withReplay(request);
      if (!mounted) return;
      if (session.isEmpty) {
        showAppSnack(context, '这次没组出题目，换个范围试试', kind: NoticeKind.error);
        return;
      }
      // 必须先清场：`PracticeController.start` 有「已启动就不初始化」的保护，
      // 不清就重开会变成「点了再练一次、题目还是上一场的」假重开。
      ref.read(practiceControllerProvider.notifier)
        ..resetForRestart()
        ..start(session);
      navigating = true;
      Navigator.of(context).pushReplacementNamed(
        SettlePage.practiceRoute,
        arguments: session,
      );
    } on ApiException catch (e) {
      if (!mounted) return;
      showAppSnack(context, e.displayText, kind: NoticeKind.error);
    } finally {
      // ⚠️ **必须兜底复位**（2026-09-29 真机复现）：非 `ApiException` 的异常
      // （典型是 `navigator._debugLocked` 断言）此前会直接把 `_busy` 永久留在
      // true —— 两个按钮全灰、页面变成**死页**，连「回到首页」都点不动。
      // 有新场次进来（navigating）时例外：页面马上被替换，保持锁定防连点。
      if (mounted && !navigating) setState(() => _busy = false);
    }
  }

  /// 「回到首页」：popUntil 回栈底（首页）。
  ///
  /// ⚠️ **不套 `_routeAnimating` 闸**（2026-10-01 真机实锤，与 #98 同源的残留）：
  /// 本页经 `pushReplacementNamed` 进来，真机上路由动画状态可能卡在中间态
  /// （status 停在 forward）→ `_routeAnimating` 恒 true → 本方法**静默 return**，
  /// 按钮点了永远没反应（真机复现：点完界面纹丝不动、logcat 无任何异常）。
  /// 而 popUntil 是幂等的：pop 掉本页后按钮随之销毁，不存在连点重复 push，
  /// 保留 `_busy` 防「重开进行中误回首页」即可。
  void _backHome() {
    if (_busy) return;
    Navigator.of(context).popUntil((r) => r.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final settle = _args.settle;
    if (settle == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('结算')),
        body: const Center(child: Text('这次练习没有结算数据')),
      );
    }

    final wrongCount = settle.totalItems - settle.correctItems;
    final canRestart = _args.replayRequest != null;

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
          if (canRestart) ...[
            AppButton(
              label: '再练一次',
              loading: _busy,
              onPressed: _busy ? null : _restart,
            ),
            const SizedBox(height: AgeProfile.adjacentButtonGap),
          ],
          AppButton(
            label: '回到首页',
            // 拿不到原配置时（如从别处直接落到结算页）这就是唯一主操作，
            // 顶成 primary，避免出现两个「回到首页」。
            kind: canRestart ? AppButtonKind.text : AppButtonKind.primary,
            onPressed: _busy ? null : _backHome,
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
