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
        final verdict = AnswerVerdict.fromJson(msg);
        _controller.submitVoiceVerdict(
          verdict,
          durationMs: _pendingDurationMs ?? 0,
          firstVoiceMs: _pendingFirstVoiceMs ?? 0,
        );
      case 'error':
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
    ws?.sendStart(WsStartOptions(
      sessionId: widget.session.sessionId,
      taskType: _controller.currentTaskType,
      wordId: item.wordId,
      expected: null,
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
    setState(() => _recording = false);
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

  void _maybeNavigateToSettle() {
    final st = ref.read(practiceControllerProvider);
    if (st.phase == PracticePhase.finished && st.settle != null && mounted) {
      Navigator.of(context).pushReplacementNamed(
        PracticeRoutes.summary,
        arguments: SettleArguments(settle: st.settle!, unitName: widget.session.unitName),
      );
    }
  }

  // ----------------------------------------------------------- 构建

  @override
  Widget build(BuildContext context) {
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
              onCancel: () => _controller.useKeyboard(),
            ),
          ] else ...[
            // 录音态 / 待开口
            Center(
              child: _recording
                  ? const RecordingIndicator()
                  : AppButton(
                      label: '按住说话',
                      icon: Icons.mic_rounded,
                      onPressed: _recording ? null : _startRecording,
                    ),
            ),
            const SizedBox(height: AgeProfile.elementGap),
            if (state.stepKind == PracticeStepKind.spell)
              Padding(
                padding: const EdgeInsets.only(bottom: AgeProfile.elementGap),
                child: Text(
                  '轻轻念出每个字母',
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
    final isSpell = state.stepKind == PracticeStepKind.spell;
    final hasDiff = state.lastVerdict?.diff.isNotEmpty ?? false;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        InlineNotice(
          kind: correct ? NoticeKind.success : NoticeKind.error,
          text: correct ? '读对啦，真棒' : (state.errorMessage ?? '再试一次'),
        ),
        if (isSpell && hasDiff) ...[
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
