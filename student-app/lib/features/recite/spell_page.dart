/// 背诵答题页 —— 简化版 WP-C（功能点 5/6，契约 §5/§6.4，**v1.3 交互重构**）。
///
/// 两种模式一个页面：
/// - **spell_zh2en（看中文背英文）**：显示中文+词性 → **按住说话**（press-and-hold,
///   仿微信语音）——按下大圆钮发 `start` 并推流，**松开发 `end`**；按住中识别
///   内容**逐格填色**（方案 B：识别到一个字母填一格，不判红绿）。判定在**服务端**
///   （§5 `verdict` 整词判定）：对=全格绿+👍 → 倒计时；错=按编辑距离逐格着色
///   （对=绿/错=红/缺=红空格）→ 倒计时（走完先 auto-add 再推进）。语音/键盘随时
///   互切（不清内容）；键盘=系统键盘优先（禁联想），回退自绘+大间距（常量开关）；键盘填满本地等值比对（忽略
///   大小写）→ `answer(correct 必填)`。判错/跳过 → auto-add；**判对不再 DELETE
///   错题本**（v1.3 废除，服务端在 answer correct=true 时自动写 mastered_at）。
/// - **spell_en2zh（看英文背中文）**：显示英文 → 学生说中文 → 拿转写文本
///   `POST /api/practice/answer`（比对在服务端 compare_service）→
///   对=立即下一题；错=「再试一次」不中断。交互保留（不产生 verdict）。
///
/// **全场推进（决策 #14）**：「结束并结算」按钮已删除；跳过常驻（=判错，立即
/// 下一题）；最后一题倒计时走完自动 `POST /api/practice/settle` → 结算页。
/// 判错的题 `POST /api/wrongbook/auto-add`（mode=当次模式）。
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
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

/// 本题作答阶段（zh2en）。
enum _Phase {
  /// 未开始/可重新按住。
  idle,

  /// 按住中：推流 + 实时回显。
  holding,

  /// 已松开，等服务端 verdict。
  waiting,

  /// 判对（绿 + 👍），倒计时中。
  correct,

  /// 判错（红），倒计时中（走完先 auto-add 再推进）。
  wrong,
}

/// 拼读格状态（方案 B：填框即时、判红兜底，2026-10-16 姚工拍板）。
enum _CellStatus {
  /// 进行态：顺序填格，不判红绿（判定收敛在松开后的整词 verdict）。
  pending,

  /// 判错态：该目标位与听到的字母对齐且相同。
  match,

  /// 判错态：该目标位对齐到了不同的字母。
  wrong,

  /// 判错态：该目标位没听到字母。
  missing,
}

/// 一个拼读格：`heard` 为该格显示的字母（null = 空格）。
@immutable
class _SpellCell {
  const _SpellCell({this.heard, required this.status});

  final String? heard;
  final _CellStatus status;
}

/// 把「听到的字母序列」对齐到「目标字母序列」—— Needleman-Wunsch 编辑距离
/// （相同=0，替换/插入/删除=1），用于判错后的逐格着色（方案 B 兜底半）：
/// - 目标每格 → match / wrong（对齐到不同字母）/ missing（没听到）；
/// - 听到多出来的字母 → 附加红格排在末尾（extra）。
///
/// 纯函数、无 Flutter 依赖，单测直接覆盖（test/test_spell_align_test.dart）。
List<_SpellCell> alignSpellCells(List<String> target, List<String> heard) {
  final n = heard.length;
  final m = target.length;
  // dp[i][j] = heard[:i] 对齐 target[:j] 的最小代价
  final dp = List.generate(
    n + 1,
    (i) => List.generate(m + 1, (j) {
      if (i == 0) {
        return j; // 全靠删除目标字母
      }
      if (j == 0) {
        return i; // 全是多余字母
      }
      return 0;
    }),
  );
  for (var i = 1; i <= n; i++) {
    for (var j = 1; j <= m; j++) {
      final sub = dp[i - 1][j - 1] + (heard[i - 1] == target[j - 1] ? 0 : 1);
      final del = dp[i - 1][j] + 1; // heard 多一个
      final ins = dp[i][j - 1] + 1; // target 缺一个
      dp[i][j] = sub < del ? (sub < ins ? sub : ins) : (del < ins ? del : ins);
    }
  }
  // 回溯产出对齐动作（反向收集再翻转）。优先级：全对(match) >
  // 多读(extra) > 缺读(missing) > 替换(wrong) —— 保证 bananas 尾部的
  // S 显示为「多读红格」而不是抢走最后一位 A 的对齐。
  final cells = List<_SpellCell?>.filled(m, null);
  final extras = <String>[];
  var i = n;
  var j = m;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && heard[i - 1] == target[j - 1] &&
        dp[i][j] == dp[i - 1][j - 1]) {
      cells[j - 1] = _SpellCell(heard: heard[i - 1], status: _CellStatus.match);
      i -= 1;
      j -= 1;
      continue;
    }
    if (i > 0 && dp[i][j] == dp[i - 1][j] + 1) {
      extras.add(heard[i - 1]); // 多读的字母
      i -= 1;
      continue;
    }
    if (j > 0 && dp[i][j] == dp[i][j - 1] + 1) {
      // 目标缺一个字母（没听到）
      cells[j - 1] = const _SpellCell(heard: null, status: _CellStatus.missing);
      j -= 1;
      continue;
    }
    // 替换：该目标位对齐到了不同的字母
    cells[j - 1] = _SpellCell(heard: heard[i - 1], status: _CellStatus.wrong);
    i -= 1;
    j -= 1;
  }
  return <_SpellCell>[
    for (final c in cells)
      c ?? _SpellCell(heard: null, status: _CellStatus.missing),
    for (final e in extras.reversed)
      _SpellCell(heard: e, status: _CellStatus.wrong),
  ];
}

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

  /// F16-6 键盘回退开关：系统键盘优先（禁联想/自动纠错）。真机若不可控
  /// （如弹中文输入法）→ 改 `false` 回退自绘键盘 + 调大字母间距。
  static const bool kUseSystemKeyboard = true;

  PracticeWsSession? _session;

  int _idx = 0;
  final List<SpellItemResult> _results = <SpellItemResult>[];

  /// 已成功 auto-add 的错题 id（判错推进时记；settle 兜底只补漏）。
  final Set<int> _wrongAddedIds = <int>{};

  // ---- zh2en 答题态（v1.3：识别内容区，无字母格） ----
  _Phase _phase = _Phase.idle;

  /// 已定版「听到」的内容（utterance 累积 / verdict 定版 / 键盘互切带入）。
  String _uttered = '';

  /// 按住中的实时 partial（未定版，单独拼在末尾显示）。
  String _partial = '';

  /// 本题是否已上报 answer（verdict / 键盘判定 / 跳过任一已落账）。
  bool _answeredThisRound = false;

  /// 语音 ⇄ 键盘（A6：随时互切，不清内容）。
  bool _keyboardMode = false;
  final TextEditingController _typeCtl = TextEditingController();
  final FocusNode _inputFocus = FocusNode();

  // ---- 倒计时推进（F16-4）----
  /// 判定后倒计时秒数（settings.spell_verdict_countdown_s，进页拉一次，
  /// 失败回退 3）。
  int _countdownSecs = 3;
  bool _countdownLoaded = false;
  Timer? _countdownTimer;
  int? _countdownLeft;

  /// verdict 等待兜底（F16-9）：松开后 1.5s 无 verdict → 按未判定处理。
  Timer? _verdictTimer;

  // ---- en2zh 答题态 ----
  String _transcript = '';
  bool _capturing = false;

  /// 本题阶段：en2zh 判错后的「再试一次」态。
  bool _lastWrong = false;

  bool _micBlocked = false;
  bool _disconnected = false;
  bool _busy = false; // answer/settle 等异步按钮防重
  bool _settled = false;
  DateTime? _itemStartAt;

  StreamSubscription<VerdictEvent>? _verdictSub;
  StreamSubscription<TranscriptEvent>? _transcriptSub;
  StreamSubscription<UtteranceEvent>? _utteranceSub;
  StreamSubscription<WsState>? _stateSub;
  StreamSubscription<AsrErrorEvent>? _errorSub;
  StreamSubscription<double>? _levelSub;

  /// 麦克风实时音量（0~1）：录音动效用；录音结束后归零。
  double _micLevel = 0;

  /// 服务端报错（start 被拒 / 没听到声音）：展示给用户并恢复重试按钮。
  String? _asrError;

  SpellRun? _run;

  SpellItem get _item => _run!.items[_idx];

  /// 识别内容区展示文本（语音模式）：已定版累积 + 实时 partial。
  /// 例：念完 A（定版）正在念 P → "A P"。空白时返回空串（显示引导语）。
  String get _recognizedDisplay {
    final live = _partial.trim().toUpperCase();
    final parts = <String>[
      if (_uttered.isNotEmpty) _uttered,
      if (live.isNotEmpty) live,
    ];
    return parts.join(' ');
  }

  /// 当前已填内容的纯字母序列（去空格/连字符），键盘判定与互切带入用。
  static String _normLetters(String s) =>
      s.replaceAll(RegExp(r'[\s\-]'), '').toUpperCase();

  @override
  void initState() {
    super.initState();
    final session = widget.session ??
        PracticeWsSession(
          // ⛔ 不能用 wsClientProvider：它是 autoDispose，页面只 read 不 watch，
          // riverpod 随时回收 → client.dispose() 关掉消息/状态流，页面变聋
          // （2026-10-11 真机「没听清/连接断开」根因）。会话与页面同生命周期，
          // 自建自有客户端。
          wsClient: WsClient(
            serverStore: ref.read(serverStoreProvider),
            localStore: ref.read(localStoreProvider),
          ),
          micRecorder: ref.read(pcmRecorderProvider),
        );
    _session = session;
    _verdictSub = session.verdicts.listen(_onVerdict);
    _transcriptSub = session.transcripts.listen(_onTranscript);
    _utteranceSub = session.utterances.listen(_onUtterance);
    _stateSub = session.states.listen(_onWsState);
    _errorSub = session.errors.listen(_onAsrError);
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
    if (!_countdownLoaded) {
      _countdownLoaded = true;
      unawaited(_loadCountdown());
    }
  }

  /// F16-4：进页拉一次 `spell_verdict_countdown_s`（契约 §4.1 settings），
  /// 拉取失败/缺字段回退 3；值域夹到 1~10（§4.1 服务端同口径）。
  Future<void> _loadCountdown() async {
    var secs = 3;
    try {
      final data =
          await ref.read(apiClientProvider).get<Map<String, dynamic>>(
                '/api/settings',
              );
      final v = (data?['spell_verdict_countdown_s'] as num?)?.toInt();
      if (v != null && v >= 1) {
        secs = v.clamp(1, 10);
      }
    } on Object {
      // 网络不通/旧后端没有该接口：回退默认 3 秒
    }
    if (!mounted) {
      return;
    }
    setState(() => _countdownSecs = secs);
  }

  @override
  void dispose() {
    _verdictSub?.cancel();
    _transcriptSub?.cancel();
    _utteranceSub?.cancel();
    _stateSub?.cancel();
    _errorSub?.cancel();
    _levelSub?.cancel();
    _countdownTimer?.cancel();
    _verdictTimer?.cancel();
    _typeCtl.dispose();
    _inputFocus.dispose();
    // 会话不是 provider 管的（页面生命周期对象），由页面负责关。
    unawaited(_session?.close());
    super.dispose();
  }

  // ---------------------------------------------------------------- 流事件

  /// 服务端 verdict（§5 v1.3）：整词判定唯一来源。
  void _onVerdict(VerdictEvent event) {
    if (!mounted || _settled) {
      return;
    }
    if (event.itemId != _item.id) {
      return; // 换题后迟到的旧题判定直接丢
    }
    if (_phase != _Phase.waiting && _phase != _Phase.holding) {
      return;
    }
    _verdictTimer?.cancel();
    _verdictTimer = null;
    final run = _runOrNull();
    if (run == null) {
      return;
    }
    setState(() {
      // 判定文本定版上屏（替代逐格 letter，v1.3）
      _uttered = _normLetters(event.text);
      _partial = '';
      _phase = event.correct ? _Phase.correct : _Phase.wrong;
    });
    if (!event.correct) {
      // §5 判错抑制：服务端忽略后续帧，App 同时停推流（双保险）
      _session?.stopPush();
    }
    // §4.1：App 收 verdict 后上报留痕（correct 必填，判定已在服务端）
    unawaited(_afterJudged(correct: event.correct, answer: event.text));
  }

  void _onTranscript(TranscriptEvent event) {
    if (!mounted || _settled) {
      return;
    }
    final run = _runOrNull();
    if (run == null) {
      return;
    }
    if (run.isZh2En) {
      // 按住中实时回显（决策 #11）；判定已出后到达的 partial 不再上屏（U16-3）
      if (_phase == _Phase.correct || _phase == _Phase.wrong) {
        return;
      }
      setState(() => _partial = event.text);
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

  void _onUtterance(UtteranceEvent event) {
    if (!mounted || _settled) {
      return;
    }
    final run = _runOrNull();
    if (run == null || !run.isZh2En) {
      return; // utterance 仅 zh2en 回显用（en2zh 走 transcripts）
    }
    // 换题后迟到的旧题消息直接丢。
    if (event.itemId != _item.id) {
      return;
    }
    // 判定已出（对/错）后到达的 utterance 不再上屏（U16-3）
    if (_phase == _Phase.correct || _phase == _Phase.wrong) {
      return;
    }
    final text = event.text.trim();
    if (text.isEmpty) {
      return;
    }
    setState(() {
      // 这句已定版：收进「听到」累积，并清掉实时 partial ——
      // 否则旧 partial（如 "a"）会和新定版文本重复显示成 "A A"。
      _uttered = (_uttered.isEmpty ? text : '$_uttered $text').toUpperCase();
      _partial = '';
    });
  }

  void _onWsState(WsState state) {
    if (!mounted || _settled) {
      return;
    }
    // 断线转暂停：不清作答进度（§5）。重连成功也提示学生点「继续」。
    setState(() {
      _disconnected =
          state == WsState.reconnecting || state == WsState.closed;
      if (_disconnected && (_phase == _Phase.holding || _phase == _Phase.waiting)) {
        _phase = _Phase.idle;
      }
      if (!_disconnected) {
        _capturing = false;
      }
    });
  }

  void _onAsrError(AsrErrorEvent event) {
    if (!mounted || _settled) {
      return;
    }
    // 服务端拒了这一轮（start 非法 / 没听到声音）：停推流、退出「正在听」、
    // 把按钮还给用户。不弹出退栈，学生可直接重试（2026-10-11 真机死页教训）。
    unawaited(_session?.stopCapture());
    setState(() {
      if (_phase == _Phase.holding || _phase == _Phase.waiting) {
        _phase = _Phase.idle;
      }
      _capturing = false;
      _busy = false;
      _asrError = event.message;
    });
  }

  // ---------------------------------------------------------------- 作答推进

  void _resetItemState() {
    _phase = _Phase.idle;
    _uttered = '';
    _partial = '';
    _answeredThisRound = false;
    _lastWrong = false;
    _capturing = false;
    _cancelCountdown();
    _verdictTimer?.cancel();
    _verdictTimer = null;
    _typeCtl.clear();
  }

  SpellRun? _runOrNull() {
    final run = _run;
    return (run == null || run.items.isEmpty) ? null : run;
  }

  int get _correctCount => _results.where((r) => r.correct).length;

  // ---- 按住说话（F16-1）----

  /// 按下大圆钮：清空识别区（再来一次语义）→ start + 推流。
  Future<void> _onHoldStart() async {
    if (_settled || _busy) {
      return;
    }
    if (_phase == _Phase.holding || _phase == _Phase.waiting) {
      return; // 等判定中不接受新一轮，避免 start/end 乱序
    }
    final session = _session;
    final run = _runOrNull();
    if (session == null || run == null) {
      return;
    }
    _cancelCountdown();
    setState(() {
      _phase = _Phase.holding;
      _uttered = '';
      _partial = '';
      _typeCtl.clear();
      _answeredThisRound = false;
      _asrError = null;
      _micBlocked = false;
    });
    final ok = await session.begin(
      sessionId: run.sessionId,
      mode: run.mode,
      itemId: _item.id,
    );
    if (!mounted) {
      return;
    }
    if (!ok) {
      setState(() {
        _phase = _Phase.idle;
        _micBlocked = true;
      });
      return;
    }
    // 录音动效：订阅麦克风电平（broadcast 流，页面生命周期内只订一次）
    _levelSub ??= session.micLevels.listen((v) {
      if (mounted) {
        setState(() => _micLevel = v);
      }
    });
  }

  /// 松开大圆钮：停推流 + 发 `end` → 等服务端 verdict（1.5s 兜底）。
  void _onHoldEnd() {
    if (_phase != _Phase.holding) {
      return;
    }
    _session?.endRound();
    setState(() => _phase = _Phase.waiting);
    // F16-9：1.5s 无 verdict → 按「本轮未判定」处理，可再来一次，不死锁。
    _verdictTimer?.cancel();
    _verdictTimer = Timer(const Duration(milliseconds: 1500), () {
      if (!mounted || _settled || _phase != _Phase.waiting) {
        return;
      }
      setState(() => _phase = _Phase.idle);
      showAppSnack(context, '没等到判定结果，再试一次');
    });
  }

  // ---- 判定后的共同收口（verdict / 键盘填满）----

  /// 判定落账 → 启动倒计时（F16-4：对错均适用）。上报失败按未判定处理，
  /// 回到 idle 可再来一次（不死锁）。
  Future<void> _afterJudged({
    required bool correct,
    required String answer,
  }) async {
    final run = _runOrNull();
    if (run == null || _settled) {
      return;
    }
    final reported = await _reportAnswer(
      answer: answer,
      localCorrect: correct,
    );
    if (!mounted) {
      return;
    }
    if (reported == null) {
      setState(() {
        _phase = _Phase.idle;
        _answeredThisRound = false;
      });
      return;
    }
    _answeredThisRound = true;
    _startCountdown();
  }

  // ---- 倒计时（F16-4）----

  void _startCountdown() {
    _cancelCountdown();
    var left = _countdownSecs;
    _countdownLeft = left;
    _countdownTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      left -= 1;
      if (!mounted) {
        timer.cancel();
        return;
      }
      if (left <= 0) {
        timer.cancel();
        _countdownTimer = null;
        setState(() => _countdownLeft = null);
        unawaited(_onCountdownExpired());
      } else {
        setState(() => _countdownLeft = left);
      }
    });
  }

  void _cancelCountdown() {
    _countdownTimer?.cancel();
    _countdownTimer = null;
    _countdownLeft = null;
  }

  /// 倒计时走完无操作：错=先 auto-add 再推进；对=直接推进。
  Future<void> _onCountdownExpired() async {
    final run = _runOrNull();
    if (run == null || _settled) {
      return;
    }
    if (_phase == _Phase.wrong) {
      await _autoAddItem(run, _item.id);
    }
    await _advance();
  }

  /// 「再来一次」：清空识别区 + 取消倒计时，重新按住（不限次）。
  void _onRetry() {
    _cancelCountdown();
    _verdictTimer?.cancel();
    _verdictTimer = null;
    setState(() {
      _phase = _Phase.idle;
      _uttered = '';
      _partial = '';
      _typeCtl.clear();
      _answeredThisRound = false;
    });
  }

  // ---- 键盘（F16-6）----

  void _switchToKeyboard() {
    if (_phase == _Phase.holding || _phase == _Phase.waiting) {
      return; // 按住/等判定中不切，先松开
    }
    setState(() {
      _keyboardMode = true;
      // A6：互切不清空已填内容 —— 语音听到的内容带进输入框继续拼
      final seed = _normLetters(_recognizedDisplay);
      _uttered = seed;
      _partial = '';
      _typeCtl.text = seed;
    });
    _inputFocus.requestFocus();
  }

  void _switchToVoice() {
    if (_phase == _Phase.holding || _phase == _Phase.waiting) {
      return;
    }
    setState(() {
      _keyboardMode = false;
      // A6：键盘已拼内容带回识别区，不清空
      _uttered = _normLetters(_typeCtl.text);
      _partial = '';
    });
  }

  void _onTypedChanged() {
    if (_settled || _busy) {
      return;
    }
    if (_answeredThisRound) {
      // 判定后继续编辑 = 新一轮作答（等同「再来一次」先清判定态）
      _cancelCountdown();
      setState(() {
        _answeredThisRound = false;
        _phase = _Phase.idle;
      });
    }
    _maybeKeyboardSubmit();
  }

  /// 自绘键盘（回退方案）：追加一个字母（填满后不再收）。
  void _appendLetter(String letter) {
    final targetLen = _item.letters.length;
    if (_typeCtl.text.length >= targetLen) {
      return;
    }
    setState(() => _typeCtl.text += letter);
    _onTypedChanged();
  }

  /// ⌫ 逐字母回退。
  void _backspace() {
    if (_typeCtl.text.isEmpty) {
      return;
    }
    setState(
        () => _typeCtl.text = _typeCtl.text.substring(0, _typeCtl.text.length - 1));
    _onTypedChanged();
  }

  /// 填满 → 本地等值比对（忽略大小写/空格/连字符，§6.4）→ 上报（correct
  /// 必填）→ 倒计时（同 F16-4）。
  void _maybeKeyboardSubmit() {
    if (_phase == _Phase.holding || _phase == _Phase.waiting) {
      return;
    }
    if (_answeredThisRound || _busy || _settled) {
      return;
    }
    final run = _runOrNull();
    if (run == null) {
      return;
    }
    final target = _item.letters.join();
    final typed = _normLetters(_typeCtl.text);
    if (typed.length < target.length) {
      return;
    }
    final correct = typed.toLowerCase() == target.toLowerCase();
    setState(() => _phase = correct ? _Phase.correct : _Phase.wrong);
    unawaited(_afterJudged(correct: correct, answer: typed));
  }

  // ---- en2zh（交互保留）----

  Future<void> _beginSpeak() async {
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
          _asrError = null;
          _capturing = true;
          _lastWrong = false;
          _answeredThisRound = false;
        } else {
          _micBlocked = true;
          _capturing = false;
        }
      });
      if (ok) {
        _levelSub ??= session.micLevels.listen((v) {
          if (mounted) {
            setState(() => _micLevel = v);
          }
        });
      }
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

  /// en2zh：转写交服务端比对（correct 以响应为准）。对=立即下一题；错=再试一次。
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
    final reported = await _reportAnswer(
      answer: text,
      // en2zh 的判定在服务端 compare_service：correct 以响应为准。
      localCorrect: null,
    );
    if (!mounted) {
      return;
    }
    if (reported == true) {
      await _advance();
    } else {
      setState(() {
        _lastWrong = true;
        _capturing = false;
      });
    }
  }

  // ---- 上报 / 跳过 / 推进 ----

  /// 上报 answer。zh2en（verdict / 键盘本地判定）传 correct；en2zh 传 null
  /// （以服务端为准）。返回 null = 上报失败（未落账）。
  ///
  /// v1.3：判对**不再 DELETE wrongbook**（服务端在 correct=true 时自动写
  /// mastered_at，契约 §4.1/§6.4）。
  Future<bool?> _reportAnswer({
    required String answer,
    required bool? localCorrect,
  }) async {
    final run = _runOrNull();
    if (run == null) {
      return null;
    }
    final startAt = _itemStartAt;
    final elapsedMs = startAt == null
        ? 0
        : DateTime.now().difference(startAt).inMilliseconds;
    try {
      // §6.4：zh2en correct 必填；en2zh 判定在服务端 → 不传。
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
      // 同题多轮（先错后对 / 反复重试）：以最终一次为准
      _results.removeWhere((r) => r.itemId == _item.id);
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
      return null;
    } on Object {
      if (mounted) {
        showAppSnack(context, '网络不太顺，再试一次');
      }
      return null;
    }
  }

  /// 单题判错 auto-add（F16-8：判错/跳过 → auto-add；成功记入
  /// [_wrongAddedIds]，settle 兜底只补漏不重复）。
  Future<void> _autoAddItem(SpellRun run, int itemId) async {
    if (_wrongAddedIds.contains(itemId)) {
      return;
    }
    try {
      await ref.read(apiClientProvider).post<void>(
            '/api/wrongbook/auto-add',
            body: <String, dynamic>{
              'item_id': itemId,
              'mode': run.mode,
            },
          );
      _wrongAddedIds.add(itemId);
    } on Object catch (e) {
      debugPrint('[spell] auto-add item=$itemId 失败（settle 兜底会再补）：$e');
    }
  }

  /// 跳过（F16-5：两屏常驻）= 判错：answer(correct=false) + auto-add →
  /// 立即下一题。已判过的题不重复上报，只补 auto-add。
  Future<void> _skipItem() async {
    if (_busy || _settled) {
      return;
    }
    final run = _runOrNull();
    if (run == null) {
      return;
    }
    _cancelCountdown();
    _verdictTimer?.cancel();
    _session?.stopPush();
    setState(() => _busy = true);
    try {
      final alreadyJudged = _results.any((r) => r.itemId == _item.id);
      if (!alreadyJudged) {
        await _reportAnswer(answer: '', localCorrect: false);
        if (!mounted) {
          return;
        }
        // 上报失败（无落账）留在本题
        if (!_results.any((r) => r.itemId == _item.id)) {
          return;
        }
      }
      await _autoAddItem(run, _item.id);
      await _advance();
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
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
      _resetItemState();
    });
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
      // 中途未作答的题按跳过判错补记，保证 correct+wrong=total（U9 口径）。
      final answeredIds = _results.map((r) => r.itemId).toSet();
      for (final item in run.items) {
        if (!answeredIds.contains(item.id)) {
          _results.add(SpellItemResult(itemId: item.id, correct: false));
        }
      }
      correct = _correctCount;

      // F16-7：最后一题倒计时走完自动 settle。
      // unit_ids/types 传本轮快照（SpellRun 由 startSpellRun 解析注入，
      // 契约 §4.1；为空 = 单数入口解析不到，缺省让服务端回退 start 快照）。
      try {
        await ref.read(apiClientProvider).post<void>(
              '/api/practice/settle',
              body: <String, dynamic>{
                'session_id': run.sessionId,
                'duration_ms': durationMs,
                if (run.unitIds.isNotEmpty) 'unit_ids': run.unitIds,
                if (run.types.isNotEmpty) 'types': run.types,
              },
            );
      } on Object catch (e) {
        debugPrint('[spell] settle 失败（结算页照常展示）：$e');
      }

      // 判错未入错题本的逐个补记（mode=当次模式，U10）。
      for (final result in _results) {
        if (result.correct || _wrongAddedIds.contains(result.itemId)) {
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
          _wrongAddedIds.add(result.itemId);
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
              // 题面区可滚动：键盘 + 长短句在小屏也不会溢出。
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
                    text: '麦克风没就绪（权限或连接没通），再按一次试试',
                  ),
                ),
              if (_disconnected)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: InlineNotice(
                    kind: NoticeKind.info,
                    text: '连接断开了，先暂停一下；进度没丢，再按住圆钮接着来',
                  ),
                ),
              if (_asrError != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: InlineNotice(
                    kind: NoticeKind.error,
                    text: _asrError!,
                  ),
                ),
              _buildControls(run),
            ],
          ),
        ),
      ),
    );
  }

  /// 当前逐格状态（方案 B：填框即时、判红兜底）。
  /// - idle/holding/waiting：顺序填格（听到的字母进格，不判红绿）；
  /// - correct：全格绿；
  /// - wrong：NW 对齐 → 对=绿 / 错=红 / 缺=红空格，多读的字母附红格在后。
  List<_SpellCell> _buildCells() {
    final target = _item.letters;
    final heard = _normLetters(_recognizedDisplay).split('');
    switch (_phase) {
      case _Phase.correct:
        return <_SpellCell>[
          for (final l in target)
            _SpellCell(heard: l.toUpperCase(), status: _CellStatus.match),
        ];
      case _Phase.wrong:
        // target 是 en 原文（小写），heard 已大写 → 先统一再对齐
        return alignSpellCells(
          <String>[for (final l in target) l.toUpperCase()],
          heard,
        );
      case _Phase.idle:
      case _Phase.holding:
      case _Phase.waiting:
        final cells = <_SpellCell>[
          for (var i = 0; i < target.length; i++)
            _SpellCell(
              heard: i < heard.length ? heard[i] : null,
              status: _CellStatus.pending,
            ),
        ];
        // 超读的字母也如实显示（pending 态不标红，判定交给服务端）
        for (var i = target.length; i < heard.length; i++) {
          cells.add(_SpellCell(heard: heard[i], status: _CellStatus.pending));
        }
        return cells;
    }
  }

  /// 识别内容区（F16-2，方案 B 替代 v1.3 整段文本）：
  /// 逐字母方格 —— 按住中识别到一个填一格（主色）；判对全绿 + 👍；
  /// 判错按编辑距离逐格着色（对=绿/错=红/缺=红空格，多读=尾部红格）。
  Widget _recognitionArea(AgeProfile profile, AppExtraColors extra) {
    final palette = Theme.of(context).colorScheme;
    final String? hint = switch (_phase) {
      _Phase.holding => '正在听你说…',
      _Phase.waiting => '松开啦，等判定…',
      _Phase.idle => '按住下面的圆钮，把这个单词拼出来',
      _Phase.correct || _Phase.wrong => null,
    };
    final cells = _buildCells();
    return Container(
      width: double.infinity,
      constraints: const BoxConstraints(minHeight: 88),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: _phase == _Phase.correct
            ? extra.successLight
            : _phase == _Phase.wrong
                ? extra.errorLight
                : extra.card,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: _phase == _Phase.correct
              ? extra.success
              : _phase == _Phase.wrong
                  ? extra.error
                  : extra.divider,
          width: _phase == _Phase.correct || _phase == _Phase.wrong ? 1.2 : 0.5,
        ),
      ),
      child: Column(
        children: [
          if (_phase == _Phase.holding) ...[
            _MicWave(level: _micLevel),
            const SizedBox(height: 6),
          ],
          Wrap(
            alignment: WrapAlignment.center,
            spacing: 6,
            runSpacing: 6,
            children: <Widget>[
              for (final cell in cells) _spellBox(cell, profile, extra, palette),
            ],
          ),
          if (_phase == _Phase.correct) ...[
            const SizedBox(height: 6),
            Text(
              '👍',
              style: profile.cardTitleStyle.copyWith(color: extra.success),
            ),
          ],
          if (hint != null) ...[
            const SizedBox(height: 6),
            Text(
              hint,
              style: profile.captionStyle.copyWith(
                color: _phase == _Phase.holding
                    ? palette.primary
                    : extra.textFaint,
              ),
              textAlign: TextAlign.center,
            ),
          ],
        ],
      ),
    );
  }

  /// 单个拼读格。进行态：有字母=主色描边、空=淡描边；判错态按 match/wrong/
  /// missing 着绿/红（missing 显示红空框，不剧透答案）。
  Widget _spellBox(
    _SpellCell cell,
    AgeProfile profile,
    AppExtraColors extra,
    ColorScheme palette,
  ) {
    final (Color border, Color bg, Color fg) = switch (cell.status) {
      _CellStatus.match => (extra.success, extra.successLight, extra.success),
      _CellStatus.wrong => (extra.error, extra.errorLight, extra.error),
      _CellStatus.missing => (extra.error, extra.errorLight, extra.error),
      _CellStatus.pending => (
          cell.heard != null ? palette.primary : extra.divider,
          extra.card,
          cell.heard != null ? palette.primary : extra.textFaint,
        ),
    };
    return Container(
      width: 38,
      height: 46,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: border, width: cell.heard != null ? 1.4 : 0.8),
      ),
      child: Text(
        cell.heard ?? '',
        style: profile.cardTitleStyle.copyWith(
          color: fg,
          fontWeight: FontWeight.w700,
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
        // F16-6：键盘模式 → 识别内容区变成可编辑输入框（系统键盘优先）；
        // 互切不清内容（A6）。
        if (_keyboardMode)
          TextField(
            key: const ValueKey('spell-kbd-input'),
            controller: _typeCtl,
            focusNode: _inputFocus,
            keyboardType: TextInputType.visiblePassword,
            enableSuggestions: false,
            autocorrect: false,
            textCapitalization: TextCapitalization.characters,
            maxLength: item.letters.length,
            inputFormatters: [
              FilteringTextInputFormatter.allow(RegExp('[a-zA-Z]')),
            ],
            textAlign: TextAlign.center,
            style: profile.cardTitleStyle.copyWith(
              letterSpacing: 4,
              fontWeight: FontWeight.w700,
            ),
            decoration: InputDecoration(
              counterText: '',
              hintText: '用键盘把这个单词拼出来',
              filled: true,
              fillColor: extra.card,
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(12),
              ),
            ),
            onChanged: (_) => _onTypedChanged(),
          )
        else
          _recognitionArea(profile, extra),
        if (_keyboardMode && !kUseSystemKeyboard) ...[
          const SizedBox(height: 12),
          _FallbackKeyboard(
            onLetter: _appendLetter,
            onBackspace: _backspace,
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
          child: Column(
            children: [
              if (_capturing) ...[
                _MicWave(level: _micLevel),
                const SizedBox(height: 8),
              ],
              Text(
                _transcript.isEmpty
                    ? (_capturing ? '正在听你说…' : '点下面的按钮，说出它的中文意思')
                    : _transcript,
                style: profile.bodyStyle,
                textAlign: TextAlign.center,
              ),
            ],
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
      final judged =
          _phase == _Phase.correct || _phase == _Phase.wrong;
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // A6：语音 ⇄ 键盘随时互切（原型图底部两图标）
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconButton(
                tooltip: '语音拼读',
                icon: Icon(
                  Icons.mic_rounded,
                  size: 30,
                  color: !_keyboardMode
                      ? Theme.of(context).colorScheme.primary
                      : AppExtraColors.of(context).textFaint,
                ),
                onPressed: _switchToVoice,
              ),
              const SizedBox(width: 24),
              IconButton(
                tooltip: '键盘拼',
                icon: Icon(
                  Icons.keyboard_rounded,
                  size: 30,
                  color: _keyboardMode
                      ? Theme.of(context).colorScheme.primary
                      : AppExtraColors.of(context).textFaint,
                ),
                onPressed: _switchToKeyboard,
              ),
            ],
          ),
          const SizedBox(height: 8),
          if (!_keyboardMode)
            // F16-1：按住说话大圆钮（press-and-hold）
            Center(
              child: GestureDetector(
                key: const ValueKey('spell-hold'),
                onTapDown: (_) => unawaited(_onHoldStart()),
                onTapUp: (_) => _onHoldEnd(),
                onTapCancel: _onHoldEnd,
                child: Container(
                  width: 112,
                  height: 112,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: _phase == _Phase.holding
                        ? Theme.of(context).colorScheme.primary
                        : Theme.of(context)
                            .colorScheme
                            .primary
                            .withValues(alpha: 0.12),
                    border: Border.all(
                      color: Theme.of(context).colorScheme.primary,
                      width: 1.5,
                    ),
                  ),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(
                        Icons.mic_rounded,
                        size: 36,
                        color: _phase == _Phase.holding
                            ? Colors.white
                            : Theme.of(context).colorScheme.primary,
                      ),
                      const SizedBox(height: 4),
                      Text(
                        _phase == _Phase.holding ? '松开结束' : '按住拼读',
                        style: AgeScope.of(context).captionStyle.copyWith(
                              color: _phase == _Phase.holding
                                  ? Colors.white
                                  : Theme.of(context).colorScheme.primary,
                            ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          if (judged) ...[
            const SizedBox(height: 12),
            AppButton(
              label: '再来一次${_countdownLeft != null ? ' ($_countdownLeft)' : ''}',
              kind: AppButtonKind.secondary,
              onPressed: _onRetry,
            ),
          ],
          const SizedBox(height: 12),
          AppButton(
            label: '跳过（判错）',
            kind: AppButtonKind.text,
            loading: _busy,
            onPressed: _skipItem,
          ),
        ],
      );
    }
    // en2zh：交互保留（无 verdict），跳过常驻
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AppButton(
          label: _capturing ? '我说完了' : (_lastWrong ? '再试一次' : '开始说'),
          icon: _capturing ? Icons.check_circle_rounded : Icons.mic_rounded,
          loading: _busy,
          onPressed: _capturing ? _onSpeakDone : _beginSpeak,
        ),
        const SizedBox(height: 12),
        AppButton(
          label: '跳过（判错）',
          kind: AppButtonKind.text,
          loading: _busy,
          onPressed: _skipItem,
        ),
      ],
    );
  }
}

/// 自绘键盘（F16-6 回退方案）：系统键盘不可控时用，字母间距调大一档，带 ⌫。
class _FallbackKeyboard extends StatelessWidget {
  const _FallbackKeyboard({required this.onLetter, required this.onBackspace});

  static const _rows = <String>['QWERTYUIOP', 'ASDFGHJKL', 'ZXCVBNM'];

  /// 回退时把字母间距调大一档（规格 A6：真机弹中文输入法 → 自绘 + 大间距）。
  static const double keyGap = 10;

  final ValueChanged<String> onLetter;
  final VoidCallback onBackspace;

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
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: keyGap / 2),
                  child: InkWell(
                    borderRadius: BorderRadius.circular(8),
                    onTap: () => onLetter(letter),
                    child: Container(
                      width: 36,
                      height: AgeProfile.minTouchTarget,
                      alignment: Alignment.center,
                      child: Text(letter, style: profile.bodyStyle),
                    ),
                  ),
                ),
            ],
          ),
        const SizedBox(height: 4),
        OutlinedButton.icon(
          onPressed: onBackspace,
          icon: const Icon(Icons.backspace_outlined, size: 20),
          label: const Text('删除'),
        ),
        Text(
          '提示：系统键盘更好用；只有弹不出英文键盘时才用这块',
          style: profile.captionStyle.copyWith(color: extra.textFaint),
        ),
      ],
    );
  }
}

/// 录音中的音量动效：5 根跳动竖条（level 驱动，静音时平伏微动）。
///
/// 电平来自 `PcmRecorder.levelStream`（约 150ms 一帧），静音段 rms≈0 →
/// 条子落回最低高度并保持呼吸感，学生一眼能看出「App 正在听」。
class _MicWave extends StatelessWidget {
  const _MicWave({required this.level});

  /// 0~1，来自麦克风采样的归一化 rms。
  final double level;

  @override
  Widget build(BuildContext context) {
    // 静音底高 6px；有声时按电平抬起（最高 ~34px），中间条最活跃。
    const shape = <double>[0.55, 0.8, 1.0, 0.8, 0.55];
    final lift = 6.0 + level.clamp(0.0, 1.0) * 28.0;
    final active = level > 0.04;
    final color = active
        ? Theme.of(context).colorScheme.primary
        : AppExtraColors.of(context).textFaint;
    return SizedBox(
      height: 40,
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: <Widget>[
          for (var i = 0; i < shape.length; i++)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 3),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 120),
                curve: Curves.easeOut,
                width: 5,
                height: (lift * shape[i] + (active ? 0 : 2 * (1 - shape[i])))
                    .clamp(4.0, 40.0),
                decoration: BoxDecoration(
                  color: color.withValues(alpha: active ? 0.85 : 0.4),
                  borderRadius: BorderRadius.circular(3),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
