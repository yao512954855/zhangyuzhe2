/// 用眼休息提醒 —— UI 规范 §6.1 / 30-APP-01 F16（U12）。
///
/// 规则：
/// - **每累计使用 20 分钟**（`eye_rest_minutes` 可配）弹一次休息页；
/// - 休息页给 20 秒倒计时（20-20-20 用眼法则），学生可点「我休息好了」提前结束；
/// - **不锁屏、不强制**（避免引发逆反）；
/// - 可在设置里关闭，默认开启。
///
/// 时间来源用注入的 [clock]，[tick] 供测试直接驱动 —— 不靠 `sleep` 跑测试，
/// 否则一条用例要跑 20 分钟。
library;

import 'dart:async';

import 'package:flutter/foundation.dart';

/// 提醒服务。
class EyeCareService extends ChangeNotifier {
  EyeCareService({
    this.restInterval = const Duration(minutes: 20),
    DateTime Function()? clock,
    bool enabled = true,
  })  : _clock = clock ?? DateTime.now,
        // 命名参数不能以下划线开头，所以没法写 `this._enabled`，只能走初始化列表
        // ignore: prefer_initializing_formals
        _enabled = enabled;

  /// 20-20-20：远眺 20 秒。
  static const int restCountdownSeconds = 20;

  /// 提醒间隔（默认 20 分钟）。
  final Duration restInterval;

  /// 内部计时精度（30 秒一跳，够用且几乎不耗电）。
  static const Duration tickInterval = Duration(seconds: 30);

  final DateTime Function() _clock;

  Timer? _timer;
  Duration _accumulated = Duration.zero;
  DateTime? _lastTickAt;
  bool _enabled;
  bool _restDue = false;

  /// 已累计的使用时长（不含休息时间）。
  Duration get accumulated => _accumulated;

  bool get enabled => _enabled;

  /// 是否到了该休息的时刻（UI 据此弹休息页）。
  bool get restDue => _restDue;

  /// 距离下次提醒还剩多久（设置页展示用）。
  Duration get remaining {
    final left = restInterval - _accumulated;
    return left.isNegative ? Duration.zero : left;
  }

  final _restEvents = StreamController<void>.broadcast();

  /// 「该休息了」事件。
  Stream<void> get onRestDue => _restEvents.stream;

  /// 开关（设置页调）。关闭时清零累计 —— 否则关掉再开就立刻弹。
  void setEnabled(bool value) {
    if (_enabled == value) {
      return;
    }
    _enabled = value;
    if (!value) {
      _accumulated = Duration.zero;
      _restDue = false;
    }
    notifyListeners();
  }

  /// 开始累计（App 进入前台 / 进入练习页）。
  ///
  /// 首页与练习页都生效（F16）——所以由 App 生命周驱动，而不是页面自己开关。
  void startSession() {
    if (!_enabled) {
      return;
    }
    _lastTickAt = _clock();
    _timer?.cancel();
    _timer = Timer.periodic(tickInterval, (_) => _onTick());
  }

  /// 停止累计（App 进入后台）。**不清零**：切出去接个水回来不该重新计时。
  void stopSession() {
    _timer?.cancel();
    _timer = null;
    _lastTickAt = null;
  }

  void _onTick() {
    final now = _clock();
    final last = _lastTickAt ?? now;
    _lastTickAt = now;
    tick(now.difference(last));
  }

  /// 推进计时（测试直接调；正常由 [tickInterval] 定时器驱动）。
  @visibleForTesting
  void tick(Duration delta) {
    if (!_enabled || _restDue || delta <= Duration.zero) {
      return;
    }
    _accumulated += delta;
    if (_accumulated >= restInterval) {
      _restDue = true;
      stopSession();
      notifyListeners();
      if (!_restEvents.isClosed) {
        _restEvents.add(null);
      }
    } else {
      notifyListeners();
    }
  }

  /// 学生点了「我休息好了」（或倒计时结束）→ 清零重新计时。
  void markRested() {
    _accumulated = Duration.zero;
    _restDue = false;
    notifyListeners();
    startSession();
  }

  /// 重置（换账号/登出）。
  void reset() {
    stopSession();
    _accumulated = Duration.zero;
    _restDue = false;
    notifyListeners();
  }

  @override
  void dispose() {
    stopSession();
    _restEvents.close();
    super.dispose();
  }
}
