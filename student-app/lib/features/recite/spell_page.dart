/// 背诵答题页 —— 简化版 WP-C（功能点 5/6，契约 §5/§6.4）。
///
/// 两种模式一个页面：
/// - **spell_zh2en（看中文背英文）**：显示中文+词性 → WS `letter` 逐格填
///   （index 对位 `en` 去空格连字符后的字母序列）→ 填满=对 →
///   `POST /api/practice/answer(correct=true)` → **立即下一题**。
///   同一格 3 次错 → 出「用键盘点字母」入口；累计 3 次连错 → 可跳下一题（判错）。
/// - **spell_en2zh（看英文背中文）**：显示英文 → 学生说中文 → 拿转写文本
///   `POST /api/practice/answer`（比对在服务端 compare_service）→
///   对=立即下一题；错=「再试一次」不中断，可跳过（跳过=错）。
///
/// 判错的题在 settle 前逐个 `POST /api/wrongbook/auto-add`（mode=当次模式）。
/// 全部走完 → `POST /api/practice/settle` → 进结算页。
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/net/practice_ws.dart';
import '../../core/net/ws_client.dart';
import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';
import '../../features/dictation/dictation_tts.dart';
import '../../router/routes/recite.dart';
import 'settle_page.dart';
import 'spell_session.dart';

/// 背诵答题页。
class SpellPage extends ConsumerStatefulWidget {
  const SpellPage({super.key, this.run, this.session});

  /// 路由参数（`ReciteRoutes.spell`）；测试可直接注入。
  final SpellRun? run;

  /// 测试注入口：给定时不从 provider 拉 WS/录音（组件测试不碰真通道）。
  final PracticeWsSession? session;

  @override
  ConsumerState<SpellPage> createState() => _SpellPageState();
}

class _SpellPageState extends ConsumerState<SpellPage> {
  static const _answerPath = '/api/practice/answer';

  PracticeWsSession? _session;

  int _idx = 0;
  final List<SpellItemResult> _results = <SpellItemResult>[];

  // ---- zh2en 答题态 ----
  List<String?> _filled = const <String?>[];
  Map<int, int> _cellErrors = <int, int>{};
  int _falseStreak = 0;
  bool _keyboardOn = false;

  // ---- en2zh 答题态 ----
  String _transcript = '';
  bool _capturing = false;

  /// 本题阶段：en2zh 判错后的「再试一次」态。
  bool _lastWrong = false;

  bool _micBlocked = false;
  bool _disconnected = false;
  bool _busy = false; // answer/settle 等异步按钮防重
  bool _settled = false;

  /// 本轮（当前题）是否已判过：final 自动判与「我说完了」手动判共用，
  /// 防止同一轮双判双上报。
  bool _answeredThisRound = false;
  DateTime? _itemStartAt;

  StreamSubscription<LetterEvent>? _letterSub;
  StreamSubscription<TranscriptEvent>? _transcriptSub;
  StreamSubscription<WsState>? _stateSub;

  SpellRun? _run;

  SpellItem get _item => _run!.items[_idx];

  @override
  void initState() {
    super.initState();
    final session = widget.session ??
        PracticeWsSession(
          wsClient: ref.read(wsClientProvider),
          micRecorder: ref.read(pcmRecorderProvider),
        );
    _session = session;
    _letterSub = session.letters.listen(_onLetter);
    _transcriptSub = session.transcripts.listen(_onTranscript);
    _stateSub = session.states.listen(_onWsState);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_run == null) {
      // 路由参数只能在依赖就绪后取（ModalRoute.of 不能进 initState）。
      final args = ModalRoute.of(context)?.settings.arguments;
      _run = widget.run ?? (args is SpellRun ? args : null);
      _resetItemState();
      _itemStartAt = DateTime.now();
    }
  }

  @override
  void dispose() {
    _letterSub?.cancel();
    _transcriptSub?.cancel();
    _stateSub?.cancel();
    // 会话不是 provider 管的（页面生命周期对象），由页面负责关。
    unawaited(_session?.close());
    super.dispose();
  }

  // ---------------------------------------------------------------- 流事件

  void _onLetter(LetterEvent event) {
    if (!mounted || _settled) {
      return;
    }
    if (event.itemId != _item.id || event.index < 0) {
      return;
    }
    final letters = _item.letters;
    if (event.index >= letters.length) {
      return;
    }
    if (event.correct) {
      setState(() {
        _filled[event.index] = event.letter;
        _falseStreak = 0;
      });
      if (!_filled.contains(null)) {
        unawaited(_completeItemCorrect(assembled: _filled.join()));
      }
      return;
    }
    // 错误：不填格，记连错
    setState(() {
      _cellErrors[event.index] = (_cellErrors[event.index] ?? 0) + 1;
      _falseStreak += 1;
      if ((_cellErrors[event.index] ?? 0) >= 3) {
        _keyboardOn = true; // 同一格 3 次错 → 出键盘入口（§6.4）
      }
    });
  }

  void _onTranscript(TranscriptEvent event) {
    if (!mounted || _settled) {
      return;
    }
    setState(() {
      _transcript = event.text;
    });
    if (event.isFinal) {
      // 唤醒「我说完了」的等待者；本轮未判过则自动判（服务端 final 即本轮收口）。
      for (final waiter in List<Completer<void>>.of(_finalWaiters)) {
        if (!waiter.isCompleted) {
          waiter.complete();
        }
      }
      if (!_answeredThisRound) {
        _answeredThisRound = true;
        setState(() => _capturing = false);
        unawaited(_judgeTranscript());
      }
    }
  }

  void _onWsState(WsState state) {
    if (!mounted || _settled) {
      return;
    }
    // 断线转暂停：不清作答进度（§5）。重连成功也提示学生点「继续」。
    setState(() {
      _disconnected =
          state == WsState.reconnecting || state == WsState.closed;
      if (!_disconnected) {
        _capturing = false;
      }
    });
  }

  // ---------------------------------------------------------------- 作答推进

  void _resetItemState() {
    final run = _run;
    final letters = run == null || run.items.isEmpty
        ? const <String>[]
        : run.items[_idx].letters;
    _filled = List<String?>.filled(letters.length, null);
    _cellErrors = <int, int>{};
    _falseStreak = 0;
    _keyboardOn = false;
    _transcript = '';
    _lastWrong = false;
    _capturing = false;
    _answeredThisRound = false;
  }

  SpellRun? _runOrNull() {
    final run = _run;
    return (run == null || run.items.isEmpty) ? null : run;
  }

  int get _correctCount => _results.where((r) => r.correct).length;

  Future<void> _beginCapture() async {
    if (_busy || _settled) {
      return;
    }
    final session = _session;
    final run = _runOrNull();
    if (session == null || run == null) {
      return;
    }
    setState(() => _busy = true);
    try {
      final ok = await session.begin(
        sessionId: run.sessionId,
        mode: run.mode,
        itemId: _item.id,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        if (ok) {
          _micBlocked = false;
          _disconnected = false;
          _capturing = true;
          _lastWrong = false;
          _answeredThisRound = false;
        } else {
          _micBlocked = true;
          _capturing = false;
        }
      });
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _onSpeakDone() async {
    if (!_capturing || _busy) {
      return;
    }
    _session?.endRound();
    setState(() => _capturing = false);
    // 等 final：通常 sendEnd 后立即到达；final 由 _onTranscript 自动判。
    // 这里兜一层「final 迟迟不来」：窗口过后用已有转写判，避免卡在本题。
    await _waitForFinal();
    if (!mounted || _settled) {
      return;
    }
    if (!_answeredThisRound) {
      if (_transcript.trim().isEmpty) {
        showAppSnack(context, '没听清你说的话，再试一次');
        return;
      }
      _answeredThisRound = true;
      await _judgeTranscript();
    }
  }

  final List<Completer<void>> _finalWaiters = <Completer<void>>[];

  Future<bool> _waitForFinal() async {
    // 简化：final 由 _onTranscript 消费；这里只给事件循环留一拍 +
    // 短暂等待（受控时钟下不能真挂 1.5s，用 Future.delayed 会被 pump 推进）。
    final waiter = Completer<void>();
    _finalWaiters.add(waiter);
    try {
      await waiter.future.timeout(const Duration(milliseconds: 1500));
      return true;
    } on TimeoutException {
      return false;
    } finally {
      _finalWaiters.remove(waiter);
    }
  }

  Future<void> _judgeTranscript() async {
    if (_busy || _settled) {
      return;
    }
    final text = _transcript.trim();
    if (text.isEmpty) {
      setState(() {
        _lastWrong = true;
        _capturing = false;
      });
      return;
    }
    final correct = await _reportAnswer(
      answer: text,
      // en2zh 的判定在服务端 compare_service：correct 以响应为准。
      localCorrect: null,
    );
    if (!mounted) {
      return;
    }
    if (correct) {
      await _advance();
    } else {
      setState(() {
        _lastWrong = true;
        _capturing = false;
      });
    }
  }

  /// 键盘点字母：填进下一个空格；填满后本地判（§6.4，不扣分，同样上报）。
  void _onKeyboardLetter(String letter) {
    if (!_filled.contains(null)) {
      return;
    }
    final nextIndex = _filled.indexOf(null);
    setState(() => _filled[nextIndex] = letter);
    if (!_filled.contains(null)) {
      final assembled = _filled.join();
      final target = _item.letters.join();
      final correct = assembled.toLowerCase() == target.toLowerCase();
      unawaited(_completeItemCorrect(
        assembled: assembled,
        forceCorrect: correct,
      ));
    }
  }

  void _clearKeyboardInput() {
    setState(() {
      for (var i = 0; i < _filled.length; i++) {
        _filled[i] = null;
      }
    });
  }

  /// 本题拼读/键盘填满后的收口：本地判格结果上报 → 对=立即下一题。
  Future<void> _completeItemCorrect({
    required String assembled,
    bool? forceCorrect,
  }) async {
    if (_busy || _settled) {
      return;
    }
    final correct = forceCorrect ?? true;
    final reported = await _reportAnswer(
      answer: assembled,
      localCorrect: correct,
    );
    if (!mounted) {
      return;
    }
    if (reported) {
      await _advance();
      return;
    }
    // 上报失败：留在本题，提示重试
    setState(() {
      _lastWrong = true;
    });
  }

  /// 上报 answer。zh2en 本地判格传 correct；en2zh 传 null（以服务端为准）。
  /// 返回最终判定；请求失败抛异常由调用方感知（这里转成 false + 提示）。
  Future<bool> _reportAnswer({
    required String answer,
    required bool? localCorrect,
  }) async {
    final run = _runOrNull();
    if (run == null) {
      return false;
    }
    final startAt = _itemStartAt;
    final elapsedMs = startAt == null
        ? 0
        : DateTime.now().difference(startAt).inMilliseconds;
    try {
      // §6.4：zh2en 本地判格 → correct 必填；en2zh 判定在服务端 → 不传。
      final data = await ref.read(apiClientProvider).post<Map<String, dynamic>>(
            _answerPath,
            body: <String, dynamic>{
              'session_id': run.sessionId,
              'item_id': _item.id,
              'mode': run.mode,
              'answer': answer,
              'elapsed_ms': elapsedMs,
              'correct': ?localCorrect,
            },
          );
      final correct = localCorrect ?? (data?['correct'] == true);
      if (correct) {
        // 同题先错后对的（键盘重拼 / en2zh 再试一次）：以最终一次为准，
        // 不让旧失败记录把它再记进错题本。
        _results.removeWhere((r) => r.itemId == _item.id);
        // 判对移出错题本（30-规格 WP-E 约定：判对即 DELETE，fire-and-forget，
        // 失败不影响背题主链路——移不干净顶多重做一遍）。
        unawaited(
          ref
              .read(apiClientProvider)
              .delete<dynamic>('/api/wrongbook/${_item.id}/${run.mode}')
              .catchError((Object _) {}),
        );
      }
      _results.add(SpellItemResult(
        itemId: _item.id,
        correct: correct,
        answer: answer,
      ));
      return correct;
    } on ApiException catch (e) {
      if (mounted) {
        showAppSnack(context, e.displayText);
      }
      return false;
    } on Object {
      if (mounted) {
        showAppSnack(context, '网络不太顺，再试一次');
      }
      return false;
    }
  }

  /// 跳过（zh2en 3 连错后 / en2zh 判错后）：本题判错。
  /// 已有本题判定结果（en2zh 服务端已判、键盘重拼已报）就不再重复上报，
  /// 直接推进；否则补一条 correct=false（U8：跳过=错）。
  Future<void> _skipItem() async {
    if (_busy || _settled) {
      return;
    }
    final alreadyJudged = _results.any((r) => r.itemId == _item.id);
    if (!alreadyJudged) {
      await _reportAnswer(answer: '', localCorrect: false);
      if (!mounted) {
        return;
      }
      // ⚠️ 跳过 = 判错：_reportAnswer 返回 false 是「上报成功且判错」，
      // 不是失败。以本题是否落账（结果表有无记录）判断上报成败。
      final nowJudged = _results.any((r) => r.itemId == _item.id);
      if (!nowJudged) {
        return; // 上报失败留在本题
      }
    }
    await _advance();
  }

  Future<void> _advance() async {
    final run = _runOrNull();
    if (run == null || _settled) {
      return;
    }
    if (_idx + 1 >= run.items.length) {
      await _settle(run);
      return;
    }
    setState(() {
      _idx += 1;
      _itemStartAt = DateTime.now();
    });
    _resetItemState();
    // zh2en：换题即开新一轮拼读（录音常驻，只重发 start）；en2zh 等学生点开始说。
    if (run.isZh2En && _session != null && !_disconnected) {
      await _beginCapture();
    }
  }

  Future<void> _settle(SpellRun run) async {
    if (_settled) {
      return;
    }
    setState(() => _busy = true);
    var correct = _correctCount;
    var wrongAdded = 0;
    try {
      final durationMs =
          DateTime.now().difference(run.startedAt).inMilliseconds;
      // 中途退出的题按跳过判错补记，保证 correct+wrong=total（U9 口径）。
      final answeredIds = _results.map((r) => r.itemId).toSet();
      for (final item in run.items) {
        if (!answeredIds.contains(item.id)) {
          _results.add(SpellItemResult(itemId: item.id, correct: false));
        }
      }
      correct = _correctCount;

      try {
        await ref.read(apiClientProvider).post<void>(
              '/api/practice/settle',
              body: <String, dynamic>{
                'session_id': run.sessionId,
                'duration_ms': durationMs,
              },
            );
      } on Object catch (e) {
        debugPrint('[spell] settle 失败（结算页照常展示）：$e');
      }

      // 判错的题逐个记入错题本（mode=当次模式，U10）。
      for (final result in _results) {
        if (result.correct) {
          continue;
        }
        try {
          await ref.read(apiClientProvider).post<void>(
                '/api/wrongbook/auto-add',
                body: <String, dynamic>{
                  'item_id': result.itemId,
                  'mode': run.mode,
                },
              );
          wrongAdded += 1;
        } on Object catch (e) {
          debugPrint('[spell] auto-add item=${result.itemId} 失败：$e');
        }
      }
      _settled = true;
      if (!mounted) {
        return;
      }
      await Navigator.of(context).pushReplacementNamed(
        ReciteRoutes.settle,
        arguments: SettleSummary(
          correct: correct,
          wrong: run.total - correct,
          wrongAdded: wrongAdded,
          mode: run.mode,
          unitName: run.unitName,
          typeName: run.typeName,
          returnRoute: run.returnRoute,
        ),
      );
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  // ---------------------------------------------------------------- 渲染

  @override
  Widget build(BuildContext context) {
    final run = _runOrNull();
    if (run == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('背诵')),
        body: const SafeArea(
          child: EmptyView(
            title: '这场背诵不能直接开始',
            hint: '请从单元列表重新进入。',
          ),
        ),
      );
    }
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Scaffold(
      appBar: AppBar(
        title: Text(run.isZh2En ? '看中文背英文' : '看英文背中文'),
        centerTitle: true,
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                '${_idx + 1} / ${run.total} · ${run.unitName}',
                style: profile.captionStyle.copyWith(color: extra.textFaint),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: AgeProfile.elementGap),
              // 题面区可滚动：字母键盘 + 长短句在小屏也不会溢出（U6 实测
              // 512dp 视口下 Column 溢出 71px）。
              Flexible(
                child: SingleChildScrollView(
                  child: run.isZh2En
                      ? _zh2EnBody(profile, extra)
                      : _en2zhBody(profile, extra),
                ),
              ),
              if (_micBlocked)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: InlineNotice(
                    kind: NoticeKind.error,
                    text: '麦克风没就绪（权限或连接没通），点「继续」再试一次',
                  ),
                ),
              if (_disconnected)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: InlineNotice(
                    kind: NoticeKind.info,
                    text: '连接断开了，先暂停一下；进度没丢，点「继续」接着来',
                  ),
                ),
              _buildControls(run),
            ],
          ),
        ),
      ),
    );
  }

  Widget _zh2EnBody(AgeProfile profile, AppExtraColors extra) {
    final item = _item;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
            color: extra.card,
            borderRadius: BorderRadius.circular(16),
          ),
          child: Column(
            children: [
              Text(item.zh, style: profile.pageTitleStyle, textAlign: TextAlign.center),
              if (item.pos.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(
                  item.pos,
                  style: profile.captionStyle.copyWith(color: extra.textSub),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: AgeProfile.elementGap),
        _LetterGrid(
          cells: _filled,
        ),
        if (_keyboardOn) ...[
          const SizedBox(height: AgeProfile.elementGap),
          InlineNotice(
            kind: NoticeKind.hint,
            text: '拼了几次都没对上，可以用下面的字母键盘点出来（不扣分）',
          ),
          const SizedBox(height: 8),
          _Keyboard(
            onLetter: _onKeyboardLetter,
            onClear: _clearKeyboardInput,
          ),
        ],
      ],
    );
  }

  Widget _en2zhBody(AgeProfile profile, AppExtraColors extra) {
    final item = _item;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
            color: extra.card,
            borderRadius: BorderRadius.circular(16),
          ),
          child: Column(
            children: [
              Text(item.en, style: profile.pageTitleStyle, textAlign: TextAlign.center),
              if (item.pos.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(
                  item.pos,
                  style: profile.captionStyle.copyWith(color: extra.textSub),
                ),
              ],
              const SizedBox(height: 8),
              // 英文发音按钮（en2zh）：flutter_tts（WP-D 引入的依赖）。
              // 播报失败静默忽略——发音是辅助，不该打断背题主链路。
              IconButton(
                tooltip: '朗读英文',
                icon: const Icon(Icons.volume_up_rounded, size: 28),
                onPressed: () {
                  final tts = ref.read(dictationTtsProvider);
                  tts
                      .setLanguage('en-US')
                      .then((_) => tts.speak(item.en))
                      .catchError((Object _) {});
                },
              ),
            ],
          ),
        ),
        const SizedBox(height: AgeProfile.elementGap),
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: extra.card,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: extra.divider, width: 0.5),
          ),
          child: Text(
            _transcript.isEmpty
                ? (_capturing ? '正在听你说…' : '点下面的按钮，说出它的中文意思')
                : _transcript,
            style: profile.bodyStyle,
            textAlign: TextAlign.center,
          ),
        ),
        if (_lastWrong) ...[
          const SizedBox(height: 12),
          InlineNotice(
            kind: NoticeKind.error,
            text: '差点意思，再试一次；实在想不起来就跳过（这题算错）',
          ),
        ],
      ],
    );
  }

  Widget _buildControls(SpellRun run) {
    if (run.isZh2En) {
      final started =
          _capturing || _filled.any((c) => c != null) || _lastWrong;
      final canSkip = _falseStreak >= 3;
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (!started || _disconnected)
            AppButton(
              label: started ? '继续' : '开始拼读',
              icon: Icons.mic_rounded,
              loading: _busy,
              onPressed: _beginCapture,
            )
          else if (canSkip)
            AppButton(
              label: '跳过这题（判错）',
              kind: AppButtonKind.secondary,
              loading: _busy,
              onPressed: _skipItem,
            )
          else
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Text(
                '对着麦克风，一个字母一个字母念出来',
                style: Theme.of(context)
                    .textTheme
                    .bodyMedium
                    ?.copyWith(color: AppExtraColors.of(context).textFaint),
                textAlign: TextAlign.center,
              ),
            ),
          const SizedBox(height: 12),
          AppButton(
            label: '结束并结算',
            kind: AppButtonKind.text,
            loading: _busy,
            onPressed: _results.isEmpty ? null : () => unawaited(_settle(run)),
          ),
        ],
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (_lastWrong) ...[
          AppButton(
            label: '跳过这题（算错）',
            kind: AppButtonKind.secondary,
            loading: _busy,
            onPressed: _skipItem,
          ),
          const SizedBox(height: 12),
        ],
        AppButton(
          label: _capturing ? '我说完了' : (_lastWrong ? '再试一次' : '开始说'),
          icon: _capturing ? Icons.check_circle_rounded : Icons.mic_rounded,
          loading: _busy,
          onPressed: _capturing ? _onSpeakDone : _beginCapture,
        ),
        const SizedBox(height: 12),
        AppButton(
          label: '结束并结算',
          kind: AppButtonKind.text,
          loading: _busy,
          onPressed: _results.isEmpty ? null : () => unawaited(_settle(run)),
        ),
      ],
    );
  }
}

/// 字母方格：填出的格子显示字母，空的显示占位。
class _LetterGrid extends StatelessWidget {
  const _LetterGrid({required this.cells});

  final List<String?> cells;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);
    final palette = Theme.of(context).colorScheme;

    return Wrap(
      spacing: 6,
      runSpacing: 6,
      alignment: WrapAlignment.center,
      children: [
        for (var i = 0; i < cells.length; i++)
          Container(
            width: 38,
            height: 48,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: cells[i] == null ? extra.card : palette.primary.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(
                color: cells[i] == null ? extra.divider : palette.primary,
                width: cells[i] == null ? 0.5 : 1.2,
              ),
            ),
            child: Text(
              cells[i] ?? '',
              style: profile.cardTitleStyle.copyWith(
                color: palette.primary,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
      ],
    );
  }
}

/// 键盘点字母（§6.4：3 连错后的不扣分入口）。
class _Keyboard extends StatelessWidget {
  const _Keyboard({required this.onLetter, required this.onClear});

  static const _rows = <String>['QWERTYUIOP', 'ASDFGHJKL', 'ZXCVBNM'];

  final ValueChanged<String> onLetter;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Column(
      children: [
        for (final row in _rows)
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              for (final letter in row.split(''))
                InkWell(
                  borderRadius: BorderRadius.circular(8),
                  onTap: () => onLetter(letter),
                  child: Container(
                    width: 34,
                    height: AgeProfile.minTouchTarget,
                    alignment: Alignment.center,
                    child: Text(letter, style: profile.bodyStyle),
                  ),
                ),
            ],
          ),
        TextButton(
          onPressed: onClear,
          child: Text(
            '清空重拼',
            style: profile.captionStyle.copyWith(color: extra.textSub),
          ),
        ),
      ],
    );
  }
}
