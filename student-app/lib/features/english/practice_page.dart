/// 英语练习页（模式一 / 模式二 主容器）—— F1 ~ F18。
///
/// 职责：渲染题面与回显、持有麦克风录音 / WS 推流 / 计时，并在关键节点调用
/// [PracticeController] 推进状态机。判分 / 对齐 / 差异全部服务端算，本页只渲染。
///
/// 硬约束落实：
/// - 屏幕常亮（共通约束 #6）：进入 `WakelockPlus.enable()`，退出 `disable()`；
/// - 语音优先（F7）：前 2 次失败只提示「没听清」，第 3 次才自动键盘；
/// - 内联反馈（F9 / 共通约束 #4）：答错用 [InlineNotice]，不弹模态框；
/// - 护眼 / 分龄：字号与触控走 [AgeScope] / [AgeProfile]，配色走 [AppExtraColors]。
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../../core/audio/pcm_recorder.dart';
import '../../core/net/ws_client.dart';
import '../../core/providers.dart';
import '../../core/theme/app_theme.dart';
import '../../core/theme/age_profile.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import '../../core/widgets/mic_permission_view.dart';
import '../../router/routes/practice.dart';
import '../config/practice_session.dart';
import 'practice_controller.dart';
import 'settle_page.dart';
import 'widgets/answer_input_bar.dart';
import 'widgets/hint_button.dart';
import 'widgets/keyboard_input_panel.dart';
import 'widgets/recording_indicator.dart';
import 'widgets/spell_result_card.dart';
import 'widgets/timer_badge.dart';

/// 英语练习主页。
class PracticePage extends ConsumerStatefulWidget {
  const PracticePage({super.key, required this.session});

  final PracticeSession session;

  @override
  ConsumerState<PracticePage> createState() => _PracticePageState();
}

class _PracticePageState extends ConsumerState<PracticePage> {
  final PcmRecorder _recorder = PcmRecorder();
  WsClient? _wsClient;
  StreamSubscription<Uint8List>? _audioSub;
  StreamSubscription<Map<String, dynamic>>? _wsMsgSub;
  StreamSubscription<WsState>? _wsStateSub;

  bool _recording = false;
  MicPermission _permission = MicPermission.granted;
  bool _permissionChecked = false;

  // —— 计时（F13）——
  DateTime? _stepStart;
  int? _firstVoiceMs;
  int? _pendingDurationMs;
  int? _pendingFirstVoiceMs;
  (int, int)? _lastStepKey;
  Timer? _tickTimer;
  Timer? _safetyTimer;

  /// 已停止收音、正在等服务端判分（此间 UI 显示「识别中…」而不是录音按钮，
  /// 否则按钮立刻弹回「按住说话」，学生看起来像「什么都没发生」）。
  bool _awaitingVerdict = false;

  @override
  void initState() {
    super.initState();
    // 注意：不能在 initState 里同步改 provider 状态（riverpod 3.x 会抛
    // "modify a provider while building"），改到首帧之后。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) ref.read(practiceControllerProvider.notifier).start(widget.session);
    });
    _enableWakeLock();
    _wsClient = ref.read(wsClientProvider);
    _connectWs();
    // 每秒刷新一次倒计时徽标（不闪烁，只是数字变化）。
    _tickTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _tickTimer?.cancel();
    _safetyTimer?.cancel();
    _audioSub?.cancel();
    _wsMsgSub?.cancel();
    _wsStateSub?.cancel();
    _recorder.dispose();
    _wsClient?.close();
    _wsClient = null;
    _disableWakeLock();
    super.dispose();
  }

  void _enableWakeLock() {
    // 测试环境（无原生插件）可能抛，吞掉不影响逻辑。
    try {
      WakelockPlus.enable();
    } catch (_) {
      // ignore
    }
  }

  void _disableWakeLock() {
    try {
      WakelockPlus.disable();
    } catch (_) {
      // ignore
    }
  }

  PracticeController get _controller => ref.read(practiceControllerProvider.notifier);

  Future<void> _connectWs() async {
    final ws = _wsClient;
    if (ws == null) return;
    _wsMsgSub = ws.messages.listen(_onWsMessage);
    _wsStateSub = ws.states.listen(_onWsState);
    ws.onDropped = (_) {
      // U13：WS 彻底连不上 → 当前题切键盘，不阻塞整个练习。
      if (mounted) _controller.useKeyboard();
    };
    ws.onUnauthorized = () {
      // token 失效由 api_client 统一处理，这里无需额外动作。
      // 卸载后不再触碰 ref。
    };
    try {
      await ws.connect();
    } catch (_) {
      if (mounted) _controller.useKeyboard();
    }
  }

  void _onWsState(WsState state) {
    // 连接态变化可在此做 UI 调整；本页仅依赖消息推进状态机。
  }

  void _onWsMessage(Map<String, dynamic> msg) {
    final type = msg['type']?.toString();
    switch (type) {
      case 'verdict':
      case 'error':
        if (mounted && _awaitingVerdict) setState(() => _awaitingVerdict = false);
        if (type == 'verdict') {
          final verdict = AnswerVerdict.fromJson(msg);
          _controller.submitVoiceVerdict(
            verdict,
            durationMs: _pendingDurationMs ?? 0,
            firstVoiceMs: _pendingFirstVoiceMs ?? 0,
          );
          return;
        }
        final code = msg['code'];
        final action = msg['action']?.toString() ?? 'retry';
        if (code == 50003) {
          // U8：识别为空，不计错，可重录。
          _controller.submitVoiceVerdict(
            const AnswerVerdict.empty(),
            durationMs: _pendingDurationMs ?? 0,
            firstVoiceMs: _pendingFirstVoiceMs ?? 0,
          );
        } else {
          _controller.onWsError(
            action: action,
            uiHint: msg['ui_hint']?.toString(),
          );
        }
      case 'partial':
      case 'started':
      case 'pong':
        break;
    }
  }

  // ----------------------------------------------------------- 录音

  Future<void> _ensurePermission() async {
    if (_permissionChecked && _permission == MicPermission.granted) return;
    final result = await _recorder.ensurePermission();
    if (!mounted) return;
    setState(() {
      _permission = result;
      _permissionChecked = true;
    });
  }

  Future<void> _startRecording() async {
    await _ensurePermission();
    if (!mounted) return;
    if (_permission != MicPermission.granted) return;

    // 确保 WS 已连，再发 start。
    final ws = _wsClient;
    if (ws == null || !ws.isConnected) {
      try {
        await ws?.connect();
      } catch (_) {
        _controller.useKeyboard();
        return;
      }
    }
    final item = _controller.currentItem;
    if (item == null) return;
    setState(() => _awaitingVerdict = false);
    ws?.sendStart(WsStartOptions(
      sessionId: widget.session.sessionId,
      taskType: _controller.currentTaskType,
      wordId: item.wordId,
      expected: null, // §9.1 场次响应不含答案，英语由服务端按 word_id 自查
      mode: _controller.currentModeWire,
      retryCount: _controller.currentRetryCount,
      hintLevel: _controller.currentHintLevel,
      usedHintCount: _controller.currentUsedHintCount,
    ));

    try {
      final stream = await _recorder.start();
      _audioSub = stream.listen((chunk) {
        _firstVoiceMs ??= _stepStart == null ? 0 : DateTime.now().difference(_stepStart!).inMilliseconds;
        ws?.sendAudio(chunk);
      });
      if (!mounted) return;
      setState(() => _recording = true);
      // 30s 安全自动结束（UI 规范 §6.4）。
      _safetyTimer = Timer(const Duration(seconds: PcmSpec.maxRecordSeconds), _stopRecording);
    } on StateError {
      _controller.useKeyboard();
    }
  }

  Future<void> _stopRecording() async {
    if (!_recording) return;
    _safetyTimer?.cancel();
    await _audioSub?.cancel();
    _audioSub = null;
    await _recorder.stop();
    _wsClient?.sendEnd();
    final duration = _stepStart == null ? 0 : DateTime.now().difference(_stepStart!).inMilliseconds;
    _pendingDurationMs = duration;
    _pendingFirstVoiceMs = _firstVoiceMs ?? duration;
    if (!mounted) return;
    setState(() {
      _awaitingVerdict = true;
      _recording = false;
    });
  }

  // ----------------------------------------------------------- 计时

  void _markStepStartIfNeeded() {
    final st = ref.read(practiceControllerProvider);
    if (st.phase != PracticePhase.asking) return;
    final key = (st.itemIndex, st.step);
    if (_lastStepKey != key) {
      _lastStepKey = key;
      _stepStart = DateTime.now();
      _firstVoiceMs = null;
      _pendingDurationMs = null;
      _pendingFirstVoiceMs = null;
    }
  }

  int get _remainingSeconds {
    final limit = widget.session.timeLimitMs;
    if (limit <= 0) return -1; // 不限时
    final elapsed = _stepStart == null ? 0 : DateTime.now().difference(_stepStart!).inMilliseconds;
    final left = ((limit - elapsed) / 1000).ceil();
    return left < 0 ? 0 : left;
  }

  // ----------------------------------------------------------- 结算导航

  // 2026-09-28 真机修复（二次）：本方法在 build 里被每秒 tick 触发。
  // pushReplacement 的转场动画期间本页仍 mounted、phase 仍是 finished →
  // 每秒都会再 push 一次。此前用「动画 forward/reverse 时跳过」保护，
  // 但动画刚完成的 ticker 回调里 status 已是 completed、导航器锁还没解开，
  // 依旧撞 navigator._debugLocked 断言（21:16 真机复现）。
  // 改为幂等标记：跳结算页只允许 push 一次。
  bool _settlePushed = false;

  void _maybeNavigateToSettle() {
    if (_settlePushed || !mounted) return;
    final st = ref.read(practiceControllerProvider);
    if (st.phase != PracticePhase.finished || st.settle == null) return;
    _settlePushed = true;
    // ⚠️ **不能在 `build()` 里直接导航**（2026-09-29 真机复现，dev/02 §八 #98）：
    // build 期间导航器处于锁定态，`push` 会被推迟到上一个转场 ticker 结束后执行
    // （栈顶就是 `TickerFuture.whenCompleteOrCancel` → `_RouteEntry.handlePush`）。
    // 那一刻若再有转场在跑就撞 `!_debugLocked` 断言，而且**导航器的锁不会复位** ——
    // 结算页虽然显示出来了，但之后任何导航都抛断言：「再练一次」直接抛异常、
    // `_busy` 卡死、两个按钮永久变灰，整页变成死页。
    // 改为**帧后回调**，确保不在 build / 转场期间发起导航。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final cur = ref.read(practiceControllerProvider);
      if (cur.settle == null) return;
      Navigator.of(context).pushReplacementNamed(
        PracticeRoutes.summary,
        arguments: SettleArguments(
          settle: cur.settle!,
          unitName: widget.session.unitName,
          // 「再练一次」要用：原组卷请求体随场次一路带过来（缺了就如实降级）。
          replayRequest: widget.session.replayRequest,
        ),
      );
    });
  }

  // ----------------------------------------------------------- 构建

  @override
  Widget build(BuildContext context) {
    // 保活（2026-09-28「识别中」卡死根因修复）：本页 initState 用 ref.read 拿
    // wsClientProvider，不建立依赖 → autoDispose 在无监听者时把客户端连同
    // 消息流一起 dispose（流关闭但 WS 连接还在）→ verdict 全部被丢弃。
    // 语文页 recite_page 在 build 里 watch 所以一直是好的；这里对齐。
    ref.watch(wsClientProvider);
    _markStepStartIfNeeded();
    _maybeNavigateToSettle();

    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final state = ref.watch(practiceControllerProvider);

    if (state.phase == PracticePhase.error && state.session.isEmpty) {
      return Scaffold(
        appBar: AppBar(title: const Text('英语练习')),
        body: ErrorView(message: state.errorMessage ?? '出了点问题'),
      );
    }

    return PopScope(
      canPop: true,
      onPopInvokedWithResult: (didPop, _) async {
        if (!didPop) return;
        await _controller.abandon();
      },
      child: Scaffold(
        appBar: AppBar(
          title: Text(_controller.progressLabel),
          centerTitle: true,
          actions: [
            Padding(
              padding: const EdgeInsets.only(right: 12),
              child: TimerBadge(
                unlimited: widget.session.timeLimitMs <= 0,
                remainingSeconds: _remainingSeconds < 0 ? 9999 : _remainingSeconds,
              ),
            ),
          ],
        ),
        body: _buildBody(state, profile, extra),
      ),
    );
  }

  Widget _buildBody(PracticeState state, AgeProfile profile, AppExtraColors extra) {
    if (state.phase == PracticePhase.error) {
      return ErrorView(
        message: state.errorMessage ?? '出了点问题',
        onRetry: () => _controller.start(widget.session),
      );
    }

    final item = _controller.currentItem;
    if (item == null) {
      return const Center(child: LoadingView(message: '准备中…'));
    }

    // 麦克风权限未授权：嵌入权限引导（U13 可「先用打字」降级）。
    if (_permissionChecked && _permission != MicPermission.granted) {
      return MicPermissionView(
        permission: _permission,
        onAskAgain: () {
          setState(() => _permissionChecked = false);
          _startRecording();
        },
        onOpenSettings: () => _recorder.openSettings(),
        onSkip: () => _controller.useKeyboard(),
      );
    }

    return SingleChildScrollView(
      padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // 题面（不显示答案）。
          Text(item.prompt ?? '', style: profile.wordStyle, textAlign: TextAlign.center),
          const SizedBox(height: 6),
          Text(
            '${_controller.modeLabel} · ${state.stepKind.label}',
            style: profile.captionStyle.copyWith(color: extra.textSub),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: AgeProfile.elementGap * 1.5),

          if (state.errorMessage != null && state.phase == PracticePhase.asking)
            Padding(
              padding: const EdgeInsets.only(bottom: AgeProfile.elementGap),
              child: InlineNotice(kind: NoticeKind.hint, text: state.errorMessage!),
            ),

          if (state.phase == PracticePhase.result) ...[
            _buildResult(state, profile, extra),
          ] else if (state.keyboardActive) ...[
            KeyboardInputPanel(
              hint: _controller.modeLabel,
              onSubmit: (text) => _controller.submitKeyboard(
                text: text,
                durationMs: _pendingDurationMs ?? 0,
                firstVoiceMs: _pendingFirstVoiceMs ?? 0,
              ),
              // ⚠️ 是「回到语音」（面板注释也这么写）：必须调 retryVoice 把
              // keyboardActive 置回 false。2026-10-01 真机实锤：此前错接成
              // useKeyboard() —— 等于再开一次键盘，按钮点了永远没反应。
              onCancel: () => _controller.retryVoice(),
            ),
          ] else ...[
            // 录音态 / 识别等待 / 待开口
            Center(
              child: _recording
                  ? Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const RecordingIndicator(),
                        const SizedBox(height: AgeProfile.elementGap),
                        AppButton(
                          label: '读完点这里',
                          icon: Icons.check_circle_rounded,
                          onPressed: _stopRecording,
                        ),
                      ],
                    )
                  : _awaitingVerdict
                      ? const Padding(
                          padding: EdgeInsets.symmetric(vertical: 16),
                          child: Text('识别中…'),
                        )
                      : AppButton(
                          label: '按住说话',
                          icon: Icons.mic_rounded,
                          onPressed: _recording ? null : _startRecording,
                        ),
            ),
            const SizedBox(height: AgeProfile.elementGap),
            // 模式一（word 步，单轮双口径）：说整词或拼字母都收，得明确告诉学生。
            if (state.stepKind == PracticeStepKind.word ||
                state.stepKind == PracticeStepKind.spell)
              Padding(
                padding: const EdgeInsets.only(bottom: AgeProfile.elementGap),
                child: Text(
                  '说英文单词，或轻轻念出每个字母',
                  style: profile.captionStyle.copyWith(color: extra.textSub),
                  textAlign: TextAlign.center,
                ),
              ),
            const SizedBox(height: AgeProfile.elementGap),
            HintButton(
              hintLevel: state.hintLevel,
              placeholderCount: _controller.placeholderCount,
              onHint: () => _controller.escalateHint(),
            ),
            const SizedBox(height: AgeProfile.elementGap),
            // 常驻键盘入口（弱化）。
            AnswerInputBar(onUseKeyboard: () => _controller.useKeyboard()),
          ],
        ],
      ),
    );
  }

  Widget _buildResult(PracticeState state, AgeProfile profile, AppExtraColors extra) {
    final correct = state.lastRecord?.isCorrect ?? false;
    // 逐字母回显：服务端**回了 diff 就展示** —— 模式一合并单轮后「念字母」
    // 也走 word 步（服务端回退到字母序列口径），此时同样要逐位指出错在哪。
    final hasDiff = state.lastVerdict?.diff.isNotEmpty ?? false;

    // 判定回显（姚工 2026-09-29）：把「你读的」与「正确」都摆出来。
    // 语音通道 ASR 可能听错（真机实测把 banana 听成「不辣了」），学生必须看到
    // 自己被听成了什么、正确答案是什么，才知道要不要点「我读对了」申诉。
    final heard = state.lastRecord?.asrText?.trim() ?? '';
    final expected = state.lastVerdict?.expected?.trim() ?? '';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        InlineNotice(
          kind: correct ? NoticeKind.success : NoticeKind.error,
          text: correct ? '读对啦，真棒' : (state.errorMessage ?? '再试一次'),
        ),
        if (heard.isNotEmpty || expected.isNotEmpty) ...[
          const SizedBox(height: AgeProfile.elementGap),
          _AnswerEcho(heard: heard, expected: expected, correct: correct),
        ],
        if (hasDiff) ...[
          const SizedBox(height: AgeProfile.elementGap),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: SpellResultCard(
                diff: state.lastVerdict!.diff,
                rawText: state.lastVerdict?.asrText,
              ),
            ),
          ),
        ],
        const SizedBox(height: AgeProfile.elementGap * 1.5),
        if (correct)
          AppButton(label: '继续', onPressed: _controller.next)
        else ...[
          if (state.lastRecord != null && !state.timedOut && state.failuresForStep < 3)
            AppButton(label: '再试一次', onPressed: _controller.retryVoice),
          AppButton(
            label: '继续',
            kind: AppButtonKind.secondary,
            onPressed: _controller.next,
          ),
          const SizedBox(height: AgeProfile.adjacentButtonGap),
          AnswerInputBar(onUseKeyboard: () => _controller.useKeyboard()),
        ],
        if (_controller.canAppeal) ...[
          const SizedBox(height: AgeProfile.adjacentButtonGap),
          AppButton(
            label: '我读对了',
            kind: AppButtonKind.secondary,
            icon: Icons.thumb_up_alt_rounded,
            onPressed: () => _controller.appeal(),
          ),
        ],
      ],
    );
  }
}

/// 判定回显卡：「你读的 / 正确」两行（F9 内联反馈的补充，不弹模态）。
///
/// 姚工 2026-09-29 反馈：答完只看到「读对啦，真棒」，看不到自己**被听成了什么**、
/// **正确答案是什么**。语音判分最大的不确定性来自 ASR 听错，必须让学生看得见，
/// 否则「我读对了」的申诉按钮等于盲点。
class _AnswerEcho extends StatelessWidget {
  const _AnswerEcho({
    required this.heard,
    required this.expected,
    required this.correct,
  });

  /// 识别到 / 键盘输入的文本（可能为空）。
  final String heard;

  /// 标准答案（判定后才由服务端下发，§6.2）。
  final String expected;

  final bool correct;

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    final onSurface = Theme.of(context).colorScheme.onSurface;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (heard.isNotEmpty)
              _EchoRow(
                label: '你读的',
                value: heard,
                valueColor: correct ? extra.success : extra.error,
              ),
            // 姚工 2026-09-29：**「正确答案」始终展示**（此前「与所读一致就省略」），
            // 对了让孩子看到标准写法（大小写/标点归一），错了更要看到该读什么 ——
            // 「错了也知道错在哪」的前提是先看得见正确答案。
            if (expected.isNotEmpty) ...[
              if (heard.isNotEmpty) const SizedBox(height: 8),
              _EchoRow(
                label: '正确答案',
                value: expected,
                valueColor: onSurface,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// 回显卡里的一行：`标签：值`。
class _EchoRow extends StatelessWidget {
  const _EchoRow({
    required this.label,
    required this.value,
    required this.valueColor,
  });

  final String label;
  final String value;
  final Color valueColor;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('$label：', style: profile.captionStyle.copyWith(color: extra.textSub)),
        Expanded(
          child: Text(value, style: profile.bodyStyle.copyWith(color: valueColor)),
        ),
      ],
    );
  }
}
