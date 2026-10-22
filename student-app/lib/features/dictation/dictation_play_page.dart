/// 听写播报页 —— WP-D D-2 第 2 项 + APP-13 F17-1/F17-2（契约 v1.3）。
///
/// 逐题连播：报完一题启动 **N 秒倒计时**（`settings.dictation_countdown_s`，
/// 进听写拉一次 `GET /api/settings`，失败回退 3），到点自动报下一题；
/// 报词时屏显该词的中文或英文（`settings.dictation_hint`，回退 zh），
/// 位于报词动画区下方；进度 n/总；「重听」只重播当前题（**不计进度**）；
/// 「暂停/继续」；最后一题播完（或学生点「写完了」提前结束）自动进入
/// 拍照批改页，两条路都有明确推进，无静默卡死。
///
/// 未答完 = 漏，照常流转（不特判，契约 todo11 决策 #12：听写无 App 内作答）。
///
/// 红线：播报节奏与判分无关 —— 本页不上报任何 answer。
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/theme/age_profile.dart';
import '../../core/widgets/app_button.dart';
import 'dictation_capture_page.dart';
import 'dictation_models.dart';
import 'dictation_tts.dart';

/// 听写播报页。
class DictationPlayPage extends ConsumerStatefulWidget {
  const DictationPlayPage({
    super.key,
    required this.items,
    required this.settings,
    this.unitName = '',
    this.returnRoute,
    /// 测试注入 fake TTS；为空则从 provider 取默认实现。
    this.tts,
    /// 测试注入服务端设置；为空则进页面拉一次 `GET /api/settings`。
    this.remoteSettings,
  });

  final List<DictationItem> items;
  final DictationSettings settings;
  final String unitName;
  final String? returnRoute;
  final DictationTts? tts;

  /// 服务端设置（dictation_hint / dictation_countdown_s，契约 v1.3）。
  ///
  /// 传 null = 进页面拉一次 `/api/settings`；任何失败（断网/无 ProviderScope）
  /// 都回退 `zh` / 3，不炸（U17-1）。
  final DictationRemoteSettings? remoteSettings;

  @override
  ConsumerState<DictationPlayPage> createState() => _DictationPlayPageState();
}

class _DictationPlayPageState extends ConsumerState<DictationPlayPage> {
  DictationTts? _providerTts;

  DictationTts get _tts {
    final injected = widget.tts;
    if (injected != null) {
      return injected; // 测试注入的 fake 由测试自己管理生命周期
    }
    _providerTts ??= ref.read(dictationTtsProvider);
    return _providerTts!;
  }

  int _index = 0;
  bool _paused = false;
  bool _finished = false;
  bool _speaking = false;
  Timer? _tickTimer;
  int _speakSeq = 0; // 作废在途回调：重听/暂停/退出后旧的推进不再生效

  /// 报词屏显语言（'zh' | 'en'），进页面拉取失败回退 zh（F17-1）。
  String _hintLang = 'zh';

  /// 报词后倒计时秒数，拉取失败回退 3（F17-2）。
  int _countdownSeconds = DictationRemoteSettings.defaultCountdownSeconds;

  /// 报词 TTS 语速（todo13 #5），拉取失败回退 0.45（中速偏慢）。
  double _speechRate = DictationRemoteSettings.defaultSpeechRate;

  /// 倒计时剩余秒数；0 = 未在倒数（正在报 / 已暂停 / 已结束）。
  int _remaining = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) {
        // todo13 #5：先取设置再报词 —— 第一题就要用上管理端语速；
        // _loadRemoteSettings 无论成败都会兜底调 _speakCurrent。
        _loadRemoteSettings();
      }
    });
  }

  @override
  void dispose() {
    _speakSeq++;
    _tickTimer?.cancel();
    _providerTts?.dispose(); // 注入的（fake）由测试自己管
    super.dispose();
  }

  DictationItem get _current => widget.items[_index];

  String get _currentText =>
      widget.settings.readChinese ? _current.zh : _current.en;

  /// 报词屏显（F17-1）：位于报词动画区下方。
  String get _hintText => _hintLang == 'en' ? _current.en : _current.zh;

  /// 拉服务端设置：注入优先；否则进听写拉一次（失败回退默认，不炸）。
  /// 无论成败，最后都会启动首题报读（todo13 #5：保证首题带正确语速）。
  Future<void> _loadRemoteSettings() async {
    final injected = widget.remoteSettings;
    if (injected != null) {
      _applyRemoteSettings(injected);
      _speakCurrent();
      return;
    }
    try {
      final data = await ref
          .read(apiClientProvider)
          .get<Map<String, dynamic>>('/api/settings');
      if (data != null && mounted) {
        _applyRemoteSettings(DictationRemoteSettings.fromJson(data));
      }
    } on Object {
      // 断网/解析失败：回退 zh / 3 / 0.45（U17-1 拉取失败回退不炸）。
    }
    if (mounted && !_finished) {
      _speakCurrent();
    }
  }

  void _applyRemoteSettings(DictationRemoteSettings remote) {
    if (!mounted || _finished) {
      return;
    }
    setState(() {
      _hintLang = remote.hint;
      _countdownSeconds = remote.countdownSeconds;
      _speechRate = remote.speechRate;
    });
    // 进听写拉取常在首个词的倒计时中途返回：取消在途倒数，从新值完整重数。
    if (_remaining > 0) {
      _startCountdown(_speakSeq);
    }
  }

  /// 播当前题；播完后启动倒计时（F17-2），到点推进。
  Future<void> _speakCurrent() async {
    _tickTimer?.cancel();
    final seq = ++_speakSeq;
    if (mounted) {
      setState(() {
        _speaking = true;
        _remaining = 0;
      });
    }
    try {
      await _tts.setLanguage(widget.settings.ttsLanguage);
      // todo13 #5：报词语速走管理端设置（默认 0.45 中速偏慢）——
      // 此前没设语速，吃各机型系统 TTS 默认值，部分机型默认偏快、吐词含糊
      await _tts.setSpeechRate(_speechRate);
      await _tts.speak(_currentText);
    } catch (_) {
      // TTS 故障不静默卡死：给出提示，学生可点「重听当前题」重试。
      if (mounted && seq == _speakSeq) {
        ScaffoldMessenger.maybeOf(context)?.showSnackBar(
          const SnackBar(content: Text('朗读出了问题，可点「重听当前题」再试')),
        );
      }
    }
    if (!mounted || seq != _speakSeq || _paused || _finished) {
      return;
    }
    if (mounted) {
      setState(() => _speaking = false);
    }
    _startCountdown(seq);
  }

  /// 报词后 N 秒倒计时（F17-2）：每秒一跳，到 0 自动报下一个。
  ///
  /// 用「一秒一发」的一次性 Timer 链（而非 periodic），pause/重听/退出
  /// 都是「取消当前 + 重新计时」一个语义。
  void _startCountdown(int seq) {
    _tickTimer?.cancel();
    if (!mounted) {
      return;
    }
    setState(() => _remaining = _countdownSeconds);
    _tickTimer = Timer(const Duration(seconds: 1), () => _onTick(seq));
  }

  void _onTick(int seq) {
    if (!mounted || _finished || _paused || seq != _speakSeq) {
      return;
    }
    final next = _remaining - 1;
    if (next <= 0) {
      setState(() => _remaining = 0);
      _advance();
      return;
    }
    setState(() => _remaining = next);
    _tickTimer = Timer(const Duration(seconds: 1), () => _onTick(seq));
  }

  /// 推进到下一题；最后一题 → 进拍照批改页。
  void _advance() {
    if (!mounted || _finished) {
      return;
    }
    if (_index >= widget.items.length - 1) {
      _goCapture();
      return;
    }
    setState(() => _index += 1);
    _speakCurrent();
  }

  /// 结束播报，进拍照批改页（替换本页，返回键不再回到播报）。
  void _goCapture() {
    _speakSeq++;
    _tickTimer?.cancel();
    _tts.stop();
    if (!mounted) {
      return;
    }
    setState(() {
      _finished = true;
      _remaining = 0;
    });
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => DictationCapturePage(
          items: widget.items,
          unitName: widget.unitName,
          returnRoute: widget.returnRoute,
        ),
      ),
    );
  }

  /// 重听当前题：只重播，**不计进度**（不动 _index）。
  void _replay() {
    if (_paused || _finished) {
      return;
    }
    _speakCurrent();
  }

  void _togglePause() {
    if (_finished) {
      return;
    }
    if (_paused) {
      setState(() => _paused = false);
      _speakCurrent(); // 从当前题重新播起（不跳题、不计进度）
    } else {
      _speakSeq++;
      _tickTimer?.cancel();
      _tts.stop();
      setState(() {
        _paused = true;
        _speaking = false;
        _remaining = 0;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final total = widget.items.length;
    return PopScope(
      canPop: true,
      child: Scaffold(
        appBar: AppBar(
          title: Text(widget.unitName.isEmpty ? '正在听写' : '听写 · ${widget.unitName}'),
        ),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
            child: Column(
              children: [
                Text('第 ${_index + 1} / $total 题', style: profile.pageTitleStyle),
                const SizedBox(height: AgeProfile.elementGap / 2),
                Text(
                  _paused
                      ? '已暂停'
                      : (_speaking ? '正在报，写在本子上…' : '想好了就写下来'),
                  style: profile.captionStyle,
                ),
                const Spacer(),
                Icon(
                  _paused ? Icons.pause_circle_outline_rounded : Icons.hearing_rounded,
                  size: 96,
                  color: profile.captionStyle.color,
                ),
                // F17-1：报词显示（报词动画区下方）。
                const SizedBox(height: AgeProfile.elementGap / 2),
                Text(
                  _hintText,
                  style: profile.cardTitleStyle.copyWith(fontSize: 28),
                  textAlign: TextAlign.center,
                ),
                // F17-2：倒计时。
                const SizedBox(height: AgeProfile.elementGap / 2),
                Text(
                  _remaining > 0 ? '$_remaining 秒后报下一个' : ' ',
                  style: profile.captionStyle,
                ),
                const Spacer(),
                Text('提示：纸上写好后点下面的按钮去拍照批改', style: profile.captionStyle),
                const SizedBox(height: AgeProfile.elementGap),
                Row(
                  children: [
                    Expanded(
                      child: AppButton(
                        label: '重听当前题',
                        kind: AppButtonKind.secondary,
                        onPressed: (_paused || _finished) ? null : _replay,
                      ),
                    ),
                    const SizedBox(width: AgeProfile.elementGap / 2),
                    Expanded(
                      child: AppButton(
                        label: _paused ? '继续' : '暂停',
                        kind: AppButtonKind.secondary,
                        onPressed: _finished ? null : _togglePause,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: AgeProfile.elementGap / 2),
                AppButton(
                  label: '写完了，去拍照批改',
                  onPressed: _finished ? null : _goCapture,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
