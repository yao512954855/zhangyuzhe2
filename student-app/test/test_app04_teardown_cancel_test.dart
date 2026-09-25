/// WP-APP-04 收尾路径的**特征化测试**：为什么 `_teardownRecording()` 里
/// **不能 `await` 订阅的 `cancel()`**。
///
/// 【背景】整页链路测试原先在「结束并结算」后卡死：点完按钮，结算页永远不出现。
/// 逐行埋点定位到卡点是 `await _recSub.cancel()`（`_endAndSettle` 里连
/// `await _teardownRecording()` 都回不来）。
///
/// 【实测结论】`StreamSubscription.cancel()` 返回的 Future **在 `tester.pump()`
/// 的受控时钟下不会完成** —— 下面两条断言把这件事钉住。生产代码因此把订阅取消
/// 改成「不 await」（源已经 `stop()`，取消只是记帐）。
///
/// 【为什么值得留一条测试】这不是「测试写法问题」，而是**生产也必须遵守的约束**：
/// `cancel()` 的 Future 不保证及时完成（真实设备上底层流不响应时同样会挂），
/// 一旦把它挂在「结束并结算」的关键路径上，学生点了没反应、而且**异常都抛不出来**。
/// 哪天 SDK 行为变了（这两条红色），说明可以重新评估这个决定。
library;

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('受控时钟下 Stream.empty().listen().cancel() 不在帧内完成（APP-04 卡死根因）',
      (tester) async {
    var done = false;
    unawaited(
      const Stream<Uint8List>.empty().listen((_) {}).cancel().then((_) => done = true),
    );

    await tester.pump(const Duration(milliseconds: 300));
    debugPrint('[probe] Stream.empty().cancel() 帧内完成？ done=$done');

    expect(
      done,
      isFalse,
      reason: '若这里变红：说明 cancel() 已能在帧内完成，`_teardownRecording()` 可以改回 await。',
    );
  });

  testWidgets('受控时钟下 broadcast controller 的订阅 cancel() 同样不在帧内完成',
      (tester) async {
    var done = false;
    final ctl = StreamController<Uint8List>.broadcast();
    unawaited(ctl.stream.listen((_) {}).cancel().then((_) => done = true));

    await tester.pump(const Duration(milliseconds: 300));
    debugPrint('[probe] broadcast cancel() 帧内完成？ done=$done');

    expect(
      done,
      isFalse,
      reason: '若这里变红：说明 cancel() 已能在帧内完成，可以重新评估「不 await」的决定。',
    );

    await ctl.close();
  });
}
