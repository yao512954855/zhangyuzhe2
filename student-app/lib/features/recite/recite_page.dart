/// 语文背诵页 —— WP-APP-04 F1~F16（UI 规范 §7.7 P8）。
///
/// 整体分工：
/// - **判分/对齐 100% 在服务端**（契约 §13）：本页只消费控制器下发的进度去渲染，
///   只把累计识别文本 POST 给 `/feed`；绝不自己比对。
/// - **语音走 `/ws/asr`（`task_type=recite`）**：录音→推 16k PCM→收 `partial`
///   → 累计成交给 `/feed`（F5）。WS 只是"流式识别通道"，落库靠 HTTP 那几条。
/// - **进入不显示正文**（F1）：只渲染"已背出的字"（CharGrid 只画 matched 前缀），
///   未背到的字永远是空位；屏幕常亮靠 wakelock（F16 可读性 + 共通约束）。
///
/// 麦克风 / WS 只在学生点「开始背诵」后才真正拉起 —— 组件测试 pump 这个页时
/// 不点它，就能在"已创建、未录音"状态下断言网格 / 提示条 / 计时器渲染。
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../../core/audio/pcm_recorder.dart';
import '../../core/net/ws_client.dart';
import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import '../../features/config/practice_session.dart';
import 'recite_controller.dart';
import 'recite_settle_page.dart';
import 'recite_text.dart';
import 'widgets/char_grid.dart';
import 'widgets/hint_bar.dart';
import 'widgets/recite_timer.dart';

/// 静音多少毫秒后自动降一档提示（F7）。
const Duration _silenceThreshold = Duration(seconds: 8);

/// 语文背诵页。
class RecitePage extends ConsumerStatefulWidget {
  const RecitePage({super.key, this.session});

  /// 由 `openPracticeSession` 作为路由参数传入；缺参时如实引导（不崩）。
  final PracticeSession? session;

  @override
  ConsumerState<RecitePage> createState() => _RecitePageState();
}

class _RecitePageState extends ConsumerState<RecitePage> {
  PracticeItem? _item;

  PcmRecorder? _recorder;
  WsClient? _ws;
  StreamSubscription<Map<String, dynamic>>? _wsSub;
  StreamSubscription<Uint8List>? _recSub;

  /// 正在收尾（停录音 / 发 `end`）。收尾期间到达的音频帧一律丢弃，
  /// 避免「`end` 之后还补发音频」这种协议顺序错误。
  bool _tearingDown = false;

  String _transcript = '';
  bool _recording = false;
  bool _paused = false;
  bool _micBlocked = false;

  int _recordStartTs = 0;
  int? _firstVoiceMs;
  Duration _elapsed = Duration.zero;
  Timer? _tickTimer;
  Timer? _silenceTimer;

  @override
  void initState() {
    super.initState();
    _recorder = ref.read(pcmRecorderProvider);
    _pickItem();
    if (_item != null) {
      // 进入即创建一次背诵（F1：创建响应不含正文，所以这一步不会泄漏答案）。
      // 注意：创建会改写 reciteControllerProvider 的状态，必须等首帧构建完成后再做，
      // 否则 riverpod 会报「构建中修改 provider」并让页面崩在启动路径上。
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) unawaited(_createRecitation());
      });
    }
  }

  @override
  void dispose() {
    _teardownRecording();
    _recorder?.dispose();
    unawaited(
      WakelockPlus.disable().catchError((Object _) => null),
    );
    super.dispose();
  }

  // ---------------------------------------------------------------- 选择课文
  void _pickItem() {
    final session = widget.session;
    if (session == null || session.items.isEmpty) {
      _item = null;
      return;
    }
    // 语文场次 items 是课文清单；取第一篇（一期单篇背诵，取首项即可）。
    _item = session.items.firstWhere(
      (e) => e.textId != null,
      orElse: () => session.items.first,
    );
  }

  // ---------------------------------------------------------------- 创建
  Future<void> _createRecitation() async {
    final item = _item;
    final session = widget.session;
    if (item == null || session == null) return;
    try {
      await ref.read(reciteControllerProvider.notifier).start(
            sessionId: session.sessionId,
            textId: item.textId!,
            scope: item.reciteScope,
          );
    } on Object {
      // 控制器内部已把错误写进 state.errorMessage 并切到 failed，UI 直接渲染。
    }
  }

  // ---------------------------------------------------------------- 录音编排
  Future<void> _startRecording() async {
    final create = ref.read(reciteControllerProvider).create;
    if (create == null || _item == null) return;

    final perm = await _recorder!.ensurePermission();
    if (perm == MicPermission.denied) {
      setState(() => _micBlocked = false);
      return;
    }
    if (perm == MicPermission.permanentlyDenied) {
      setState(() => _micBlocked = true);
      return;
    }

    final ws = ref.read(wsClientProvider);
    _ws = ws;
    ws.onDropped = (_) => _onWsDropped();
    await ws.connect();
    if (ws.state != WsState.connected) {
      // 语音通道起不来：不硬开录音（没 partial 就没法推进），明确提示。
      if (mounted) {
        ref.read(reciteControllerProvider.notifier).clearHint();
      }
      return;
    }
    ws.sendStart(
      WsStartOptions(
        sessionId: widget.session!.sessionId,
        taskType: WsTaskType.recite,
        recitationId: create.recitationId,
      ),
    );
    _wsSub = ws.messages.listen(_onWsMessage);

    try {
      final stream = await _recorder!.start();
      _tearingDown = false;
      _recSub = stream.listen(
        (chunk) {
          // 收尾期间到的帧丢掉（`end` 之后不再发音频）。
          if (!_tearingDown) _ws?.sendAudio(chunk);
        },
        onError: (_) {},
        cancelOnError: false,
      );
    } catch (e) {
      // 录音起不来：保持可重试，不把会话打成 failed。
      if (mounted) {
        setState(() => _micBlocked = true);
      }
      return;
    }

    if (_recordStartTs == 0) {
      _recordStartTs = DateTime.now().microsecondsSinceEpoch;
    }
    _tickTimer?.cancel();
    _tickTimer = Timer.periodic(const Duration(seconds: 1), _onTick);
    _resetSilence();
    unawaited(WakelockPlus.enable().catchError((Object _) => null));

    if (mounted) {
      setState(() {
        _recording = true;
        _paused = false;
        _micBlocked = false;
      });
    }
  }

  void _onTick(Timer _) {
    if (!mounted) return;
    setState(() => _elapsed += const Duration(seconds: 1));
    final limit = widget.session?.timeLimitMs ?? 0;
    if (limit > 0 && _elapsed.inMilliseconds >= limit) {
      _onTimeUp();
    }
  }

  void _resetSilence() {
    _silenceTimer?.cancel();
    if (!_recording || _paused) return;
    _silenceTimer = Timer(_silenceThreshold, _onSilence);
  }

  Future<void> _onSilence() async {
    if (!_recording || _paused) return;
    // 静音 8 秒：自动降一档（F7）。同位置是否重复由服务端裁决，客户端不卡刷屏。
    await ref.read(reciteControllerProvider.notifier).requestHint(
          triggerBy: HintTrigger.auto,
          silenceMs: _silenceThreshold.inMilliseconds,
        );
    // 仍卡着就再等一轮（每 8 秒最多降一档）。
    _resetSilence();
  }

  void _onWsMessage(Map<String, dynamic> msg) {
    final type = msg['type']?.toString() ?? '';
    if (type == 'error') {
      unawaited(_onWsError(msg));
      return;
    }
    if (type == 'partial' || type == 'final' || type == 'result') {
      final text = (msg['text'] ?? msg['partial'] ?? msg['result'])?.toString() ?? '';
      if (text.isEmpty) return;
      _transcript = text;
      _resetSilence();
      if (_firstVoiceMs == null && _recordStartTs > 0) {
        // ⚠️ 单位必须换算成**毫秒**：`_recordStartTs` 记的是微秒时间戳，
        //    直接相减得到微秒，却被当作毫秒格式化、上报为 `first_voice_ms`
        //    → 界面上「首次发声延迟」显示成 66:27（真身 3.99 秒），
        //    服务端 `asr`/`recitations` 两处也一起被污染（2026-09-29 真机）。
        _firstVoiceMs =
            (DateTime.now().microsecondsSinceEpoch - _recordStartTs) ~/ 1000;
      }
      // 累计文本交给服务端对齐（契约 §13：客户端不自对齐）。
      unawaited(
        ref.read(reciteControllerProvider.notifier).feed(
              _transcript,
              silenceMs: 0,
            ),
      );
    }
  }

  /// 服务端下发 `error`（2026-10-01 修复：此前被静默吞掉）。
  ///
  /// - `action=abort`（如「背诵已过期」）：停录音停计时，切到 failed 全屏错误态，
  ///   学生走「重试」重新创建背诵 —— 干净的重开路径，不会拿旧 recitation 反复撞墙；
  /// - 其余（retry 类）：提示但不打断会话。
  Future<void> _onWsError(Map<String, dynamic> msg) async {
    final message = msg['message']?.toString() ?? '语音服务出了点问题';
    final abort = msg['action']?.toString() == 'abort';
    if (abort) {
      await _teardownRecording();
    }
    if (!mounted) return;
    if (abort) {
      ref.read(reciteControllerProvider.notifier).failWith(message);
    } else {
      ref.read(reciteControllerProvider.notifier).noteError(message);
    }
    showAppSnack(context, message);
  }

  void _onWsDropped() {
    if (!mounted) return;
    // WS 断开：停止录音与计时，明确提示（F10 不能静默卡死），让学生能继续或重试。
    _teardownRecording();
    setState(() {});
    showAppSnack(context, '语音连接断开了，先暂停一下，好了再继续背');
  }

  // ---------------------------------------------------------------- 控制
  Future<void> _pause() async {
    if (!_recording) return;
    await _recorder?.stop();
    // 暂停后这一路订阅已经没用了，顺手取消（不 await，理由见 _teardownRecording）。
    // 之前不取消，会在「继续背诵」时被新订阅直接覆盖 —— 旧订阅泄漏。
    _tearingDown = true;
    unawaited(_recSub?.cancel());
    _recSub = null;
    _tickTimer?.cancel();
    _silenceTimer?.cancel();
    if (mounted) setState(() => _paused = true);
  }

  Future<void> _resume() async {
    if (!_paused) return;
    final stream = await _recorder!.start();
    _tearingDown = false;
    _listenAudio(stream);
    _tickTimer = Timer.periodic(const Duration(seconds: 1), _onTick);
    _resetSilence();
    if (mounted) setState(() => _paused = false);
  }

  /// 订阅录音流（`_startRecording` / `_resume` 共用）。
  ///
  /// ⚠️ **断流必须如实感知**（2026-10-01 修）：此前 `onError: (_) {}` 静默吞错，
  /// 录音流一断（耳机拔出/底层错误）页面就永远收不到 partial —— 「屏幕不亮、
  /// 按钮像失灵」一起来了。现在断流按「暂停」处理：停表、提示，学生点
  /// 「继续背诵」即可恢复（`_resume` 会重新 `start()` 录音，WS 会话不受影响）。
  void _listenAudio(Stream<Uint8List> stream) {
    _recSub = stream.listen(
      (chunk) {
        // 收尾期间到的帧丢掉（`end` 之后不再发音频）。
        if (!_tearingDown) _ws?.sendAudio(chunk);
      },
      onError: (Object _) => _onAudioBroken('麦克风出了点问题'),
      onDone: () {
        // 主动 stop()/暂停不会走到这（是取消订阅，不是流结束）；
        // onDone 且正在录音 = 流被系统掐断。
        if (!_tearingDown && _recording && !_paused) {
          _onAudioBroken('录音中断了');
        }
      },
      cancelOnError: false,
    );
  }

  /// 录音流断掉：转成「暂停」态（复用 `_pause` 的完整收尾），给学生明确提示。
  void _onAudioBroken(String message) {
    if (!mounted || _tearingDown || !_recording || _paused) return;
    unawaited(_pause());
    showAppSnack(context, '$message，点「继续背诵」恢复');
  }

  Future<void> _confirmRestart() async {
    final confirmed = await showDialog<bool>(
          context: context,
      builder: (dialogContext) {
        final profile = AgeScope.of(dialogContext);
        return AlertDialog(
          title: const Text('重新开始本段？'),
          content: Text(
            '进度会清零，已背的部分要再背一次。',
            style: profile.bodyStyle,
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: const Text('再想想'),
            ),
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              child: const Text('重新开始'),
            ),
          ],
        );
      },
    );
    if (confirmed == true) {
      await _restart();
    }
  }

  Future<void> _restart() async {
    await _teardownRecording();
    _transcript = '';
    _firstVoiceMs = null;
    _recordStartTs = 0;
    _elapsed = Duration.zero;
    await ref.read(reciteControllerProvider.notifier).restart();
    if (mounted) setState(() => _paused = false);
  }

  Future<void> _endAndSettle({FinishReason reason = FinishReason.manual}) async {
    await _teardownRecording();
    final durationMs = _elapsed.inMilliseconds;
    final firstVoiceMs = _firstVoiceMs;
    ReciteSettle? result;
    try {
      result = await ref.read(reciteControllerProvider.notifier).settle(
            transcript: _transcript,
            durationMs: durationMs,
            firstVoiceMs: firstVoiceMs,
            finishReason: reason,
          );
    } on Object catch (e, st) {
      // 结算失败：控制器已记错误，留在背诵页让用户重试。
      //
      // ⚠️ 这里**不能静默 return**：学生点了「结束并结算」却什么都不发生，
      //    既没法用也没法查（本包组件测试一度就被这个静默吞掉了失败原因）。
      //    如实把失败说出来，并留一条日志便于定位。
      debugPrint('[recite] 结束/结算失败：$e\n$st');
      if (mounted) {
        ScaffoldMessenger.maybeOf(context)?.showSnackBar(
          const SnackBar(content: Text('刚才没处理成功，请再试一次')),
        );
      }
      return;
    }
    // `result` 在 try/catch 之后已被提升为非空（catch 分支已 return），
    // 但闭包里不保留提升，所以下面仍写 `result!`。
    if (!mounted) return;
    // 契约 §9.4：结算页的用时 / 首声延迟**以服务端回带为准** —— 它才是与学生报告、
    // 管理端统计同源的那一份；客户端本地计时在切后台 / 杀进程时会失真，只作兜底。
    final settleDurationMs = result.durationMs > 0 ? result.durationMs : durationMs;
    final settleFirstVoiceMs = result.firstVoiceMs ?? firstVoiceMs;

    // 回看要用全文（结算页按「服务端下标」着色，屏幕上第几个字必须与它对齐）。
    // 直接取本页**已加载**的那一份 —— 与网格同源，不再多打一次请求；
    // 万一它没加载成功（`valueOrNull == null`）就传空，结算页退回逐字清单。
    final loaded = ref.read(
      reciteTextProvider(ReciteTextKey(_item!.textId!, _item!.reciteScope)),
    ).asData?.value;

    unawaited(
      Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => ReciteSettlePage(
            settle: result!,
            durationMs: settleDurationMs,
            firstVoiceMs: settleFirstVoiceMs,
            fullChars: loaded?.chars ?? const <String>[],
            paragraphCharCounts: loaded?.paragraphCharCounts ?? const <int>[],
            hasNext: false,
          ),
        ),
      ),
    );
  }

  void _onTimeUp() {
    if (!_recording) return;
    unawaited(_endAndSettle(reason: FinishReason.timeout));
  }

  Future<void> _teardownRecording() async {
    _tickTimer?.cancel();
    _silenceTimer?.cancel();
    _tickTimer = null;
    _silenceTimer = null;
    // ① 先停「源」。这是唯一真正需要等的：停了才不会再产帧。
    await _recorder?.stop();
    // ② 再取消订阅。
    //
    // ⚠️ **不能 `await` 订阅的 `cancel()`**：`StreamSubscription.cancel()` 返回的
    //    Future 并不保证及时完成 —— 实测在 `flutter_test` 的受控时钟下**永不完成**
    //    （`test/test_app04_teardown_cancel_test.dart` 的探针：`Stream.empty()` 与 broadcast
    //    controller 两种订阅都是 `cancel()` 挂住）。一旦挂住，学生点「结束并结算」
    //    就永远等不到结算页，**而且连异常都抛不出来**（await 卡住，不进 catch）。
    //    源已经停了，取消订阅只是记帐，所以这里不 await。
    _tearingDown = true;
    unawaited(_recSub?.cancel());
    _recSub = null;
    unawaited(_wsSub?.cancel());
    _wsSub = null;
    _ws?.sendEnd();
    if (mounted) setState(() => _recording = false);
  }

  // ---------------------------------------------------------------- 渲染
  @override
  Widget build(BuildContext context) {
    // 持有 WS 客户端（autoDispose，build 内 watch 让其在页面存活期间保持连接）。
    final ws = ref.watch(wsClientProvider);
    _ws ??= ws;

    final state = ref.watch(reciteControllerProvider);

    if (_item == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('语文背诵')),
        body: SafeArea(
          child: EmptyView(
            title: '这篇课文还不能直接背',
            hint: '让管理者先在后台准备好课文和背诵范围，就能来背啦。',
            icon: Icons.menu_book_rounded,
          ),
        ),
      );
    }

    final creating = state.phase == RecitePhase.initial || state.phase == RecitePhase.creating;
    if (creating) {
      return Scaffold(
        appBar: AppBar(title: Text(_item!.title ?? '语文背诵')),
        body: const SafeArea(child: LoadingView(message: '准备好了，马上开始…')),
      );
    }

    if (state.phase == RecitePhase.failed) {
      return Scaffold(
        appBar: AppBar(title: Text(_item!.title ?? '语文背诵')),
        body: SafeArea(
          child: ErrorView(
            message: state.errorMessage ?? '出了点问题，再试一次',
            onRetry: () {
              // 页面侧计时/转写一并清零（WS abort 走到这里时，上一场的时间、
              // 识别文本都不该带进新一场）。
              _transcript = '';
              _firstVoiceMs = null;
              _recordStartTs = 0;
              _elapsed = Duration.zero;
              ref.read(reciteControllerProvider.notifier).reset();
              unawaited(_createRecitation());
            },
          ),
        ),
      );
    }

    final textAsync = ref.watch(
      reciteTextProvider(ReciteTextKey(_item!.textId!, _item!.reciteScope)),
    );

    return Scaffold(
      appBar: AppBar(
        title: Text(_item!.title ?? '语文背诵'),
        centerTitle: true,
      ),
      body: SafeArea(
        child: textAsync.when(
          // 正文拉取期间也展示「开始背诵」与空框网格：进入即可开始背（F1），
          // 且正文未到时不泄露任何字（空框本就不显示汉字）。等到正文到达再补形状。
          loading: () =>
              _body(state, const ReciteText(chars: [], paragraphCharCounts: [])),
          error: (_, _) =>
              _body(state, const ReciteText(chars: [], paragraphCharCounts: [])),
          data: (text) => _body(state, text),
        ),
      ),
    );
  }

  Widget _body(ReciteState state, ReciteText text) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final create = state.create;
    final progress = state.progress;

    // 展示正文：优先用真实课文（按背诵范围切片），未加载时用创建响应的段字数保形（F4）。
    final chars = text.chars;
    final paragraphCharCounts = text.paragraphCharCounts.isNotEmpty
        ? text.paragraphCharCounts
        : (create?.charCountPerParagraph ?? const <int>[]);
    final matched = progress?.matchedChars ?? 0;

    final bool started = _recording || _paused;
    final int usedHints = progress?.hintTotal ?? 0;

    return ListView(
      padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
      children: [
        // 计时（F11/F14）
        ReciteTimer(
          elapsed: _elapsed,
          limit: (widget.session?.timeLimitMs ?? 0) > 0
              ? Duration(milliseconds: widget.session!.timeLimitMs)
              : null,
        ),
        const SizedBox(height: AgeProfile.elementGap),

        // 逐字点亮网格（F2/F3/F4/F16）
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: CharGrid(
              chars: chars,
              matched: matched,
              paragraphCharCounts: paragraphCharCounts,
            ),
          ),
        ),

        // 识别失败 / 断线等内联提示（F10：不静默卡死）
        if (state.errorMessage != null && state.phase != RecitePhase.failed)
          Padding(
            padding: const EdgeInsets.only(top: AgeProfile.elementGap),
            child: InlineNotice(
              kind: NoticeKind.error,
              text: state.errorMessage!,
              onClose: () => ref.read(reciteControllerProvider.notifier).clearHint(),
            ),
          ),

        const SizedBox(height: AgeProfile.elementGap),

        // 三档提示条（F6/F7/F13）
        HintBar(
          hint: state.hint,
          usedCount: usedHints,
          onStuck: started
              ? () => unawaited(
                    ref.read(reciteControllerProvider.notifier).requestHint(
                          triggerBy: HintTrigger.manual,
                        ),
                  )
              : null,
          onSettings: _micBlocked ? () => unawaited(_recorder?.openSettings()) : null,
        ),

        const SizedBox(height: AgeProfile.adjacentButtonGap),

        // 主控制：开始 / 暂停 / 继续
        if (!started)
          AppButton(
            label: '开始背诵',
            icon: Icons.mic_rounded,
            loading: state.phase == RecitePhase.settling,
            onPressed: () => unawaited(_startRecording()),
          )
        else
          AppButton(
            label: _paused ? '继续背诵' : '暂停',
            icon: _paused ? Icons.play_arrow_rounded : Icons.pause_rounded,
            onPressed: () => unawaited(_paused ? _resume() : _pause()),
          ),

        const SizedBox(height: AgeProfile.adjacentButtonGap),

        // 次控制：重新开始 / 结束并结算
        AppButtonRow(
          children: [
            AppButton(
              label: '重新开始本段',
              kind: AppButtonKind.secondary,
              icon: Icons.restart_alt_rounded,
              onPressed: started || (progress != null && (matched > 0 || (progress.restartCount) > 0))
                  ? () => unawaited(_confirmRestart())
                  : null,
            ),
            AppButton(
              label: '结束并结算',
              kind: AppButtonKind.secondary,
              onPressed: started || (progress != null && matched > 0)
                  ? () => unawaited(_endAndSettle())
                  : null,
            ),
          ],
        ),

        const SizedBox(height: 12),
        Center(
          child: Text(
            '背到哪里，字就亮到哪里；卡住了点「我卡住了」',
            style: profile.captionStyle.copyWith(color: extra.textFaint),
          ),
        ),
      ],
    );
  }
}
