/// 背诵总结算页 —— 简化版 WP-C（功能点 7）。
///
/// 只显示三个数：**正确 X · 错误 Y · 已记入错题 Z**（无分数无评级）。
/// 判错题的 `auto-add` 与 `settle` 上报在答题页 `_settle` 里完成后才进来，
/// 这里只负责展示与 **停 3 秒自动回单元列表**
/// （`pushNamedAndRemoveUntil` 清栈；Timer 在 dispose 里取消防泄漏）。
library;

import 'dart:async';

import 'package:flutter/material.dart';

import '../../router/routes/home.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';

/// 结算数据（答题页整理好后经路由参数传入）。
class SettleSummary {
  const SettleSummary({
    required this.correct,
    required this.wrong,
    required this.wrongAdded,
    required this.mode,
    required this.unitName,
    required this.typeName,
    this.returnRoute,
  });

  final int correct;
  final int wrong;

  /// 成功记入错题本的条数（auto-add 失败的不计）。
  final int wrongAdded;
  final String mode;
  final String unitName;
  final String typeName;

  /// 结算后回跳的路由名（J3：错题本重做传错题本路由；空则回单元列表）。
  final String? returnRoute;
}

/// 总结算页。
class SettlePage extends StatefulWidget {
  const SettlePage({super.key, this.summary});

  final SettleSummary? summary;

  @override
  State<SettlePage> createState() => _SettlePageState();
}

class _SettlePageState extends State<SettlePage> {
  static const _backDelay = Duration(seconds: 3);
  Timer? _backTimer;

  SettleSummary? _summary;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_summary == null) {
      final args = ModalRoute.of(context)?.settings.arguments;
      _summary = widget.summary ?? (args is SettleSummary ? args : null);
      if (_summary != null) {
        // 停 3 秒自动回单元列表（保留栈里的首页）。
        _backTimer = Timer(_backDelay, _backToUnitList);
      }
    }
  }

  @override
  void dispose() {
    _backTimer?.cancel();
    _backTimer = null;
    super.dispose();
  }

  void _backToUnitList() {
    if (!mounted) {
      return;
    }
    // 清栈规范（§9）：pushNamedAndRemoveUntil；只清到首页之上。
    // J3：错题本重做的场次回错题本（returnRoute 由 SpellRun 透传），
    // 普通练习回单元列表重新选内容。
    final target = widget.summary?.returnRoute ?? HomeRoutes.unitList;
    Navigator.of(context).pushNamedAndRemoveUntil(
      target,
      ModalRoute.withName(HomeRoutes.home),
    );
  }

  @override
  Widget build(BuildContext context) {
    final summary = _summary;
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Scaffold(
      appBar: AppBar(
        title: const Text('背完啦'),
        centerTitle: true,
        automaticallyImplyLeading: false,
      ),
      body: SafeArea(
        child: summary == null
            ? Center(
                child: Text(
                  '请从背诵页完成一次背诵再来',
                  style: profile.bodyStyle.copyWith(color: extra.textSub),
                ),
              )
            : Padding(
                padding: const EdgeInsets.all(AgeProfile.screenEdgeInset * 2),
                child: Column(
                  children: [
                    const Spacer(),
                    Icon(
                      Icons.emoji_events_rounded,
                      size: 64,
                      color: Theme.of(context).colorScheme.primary,
                    ),
                    const SizedBox(height: AgeProfile.elementGap),
                    Text(
                      '${summary.unitName} · 背诵完成',
                      style: profile.cardTitleStyle,
                    ),
                    const SizedBox(height: AgeProfile.elementGap * 2),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        _NumberBlock(label: '正确', value: summary.correct),
                        _dot(extra),
                        _NumberBlock(label: '错误', value: summary.wrong),
                        _dot(extra),
                        _NumberBlock(label: '已记入错题', value: summary.wrongAdded),
                      ],
                    ),
                    const Spacer(),
                    Text(
                      '马上回到单元列表…',
                      style: profile.captionStyle.copyWith(color: extra.textFaint),
                    ),
                    TextButton(
                      onPressed: _backToUnitList,
                      child: const Text('不等了，现在回去'),
                    ),
                  ],
                ),
              ),
      ),
    );
  }

  Widget _dot(AppExtraColors extra) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12),
        child: Text('·', style: TextStyle(color: extra.textFaint, fontSize: 24)),
      );
}

class _NumberBlock extends StatelessWidget {
  const _NumberBlock({required this.label, required this.value});

  final String label;
  final int value;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    return Column(
      children: [
        Text('$value', style: profile.pageTitleStyle),
        const SizedBox(height: 4),
        Text(
          label,
          style: profile.captionStyle.copyWith(color: extra.textSub),
        ),
      ],
    );
  }
}
