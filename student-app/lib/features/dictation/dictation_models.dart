/// 听写链路数据模型 —— WP-D（契约《01-共享契约冻结》§4.1 / §7）。
///
/// 听写 = mode `listen`：报音（中文或英文）→ 学生纸上写 → 拍照上传
/// `POST /api/dictation/grade` → 逐词对错。
/// 播报节奏与判分无关（纸上写，无实时对错），所以这里没有「作答状态」。
library;

/// 一道听写题（来自 `POST /api/practice/start` 的 item，mode=listen）。
class DictationItem {
  const DictationItem({
    required this.id,
    required this.en,
    required this.zh,
    this.pos = '',
  });

  factory DictationItem.fromJson(Map<String, dynamic> json) => DictationItem(
        id: (json['id'] as num).toInt(),
        en: json['en']?.toString() ?? '',
        zh: json['zh']?.toString() ?? '',
        pos: json['pos']?.toString() ?? '',
      );

  final int id;

  /// 英文（学生要写的内容，也是上传批改时的 `words` 字段值）。
  final String en;

  /// 中文释义（报中文模式下由 TTS 读出）。
  final String zh;

  /// 词性，选填（展示用，不播报）。
  final String pos;

  /// 上传批改时的词表。
  static List<String> wordsOf(List<DictationItem> items) =>
      items.map((e) => e.en).toList(growable: false);
}

/// 听写播报设置（WP-D D-2：报中文/报英文 + 播报间隔 5~30s，默认 10）。
class DictationSettings {
  const DictationSettings({
    this.readChinese = true,
    this.intervalSeconds = defaultIntervalSeconds,
  });

  /// 间隔默认值与取值范围（10-学生端规格 D-2 写死）。
  static const int defaultIntervalSeconds = 10;
  static const int minIntervalSeconds = 5;
  static const int maxIntervalSeconds = 30;

  /// true = 报中文（zh-CN，学生写英文）；false = 报英文（en-US）。
  final bool readChinese;

  /// 两题之间的播报间隔（秒）。
  final int intervalSeconds;

  /// TTS 语言（契约 §8：报英文 en-US、报中文 zh-CN）。
  String get ttsLanguage => readChinese ? 'zh-CN' : 'en-US';

  String get intervalLabel => '$intervalSeconds 秒';

  DictationSettings copyWith({bool? readChinese, int? intervalSeconds}) =>
      DictationSettings(
        readChinese: readChinese ?? this.readChinese,
        intervalSeconds:
            intervalSeconds ?? this.intervalSeconds,
      );

  /// 步进后夹在 5~30 范围内。
  DictationSettings withIntervalDelta(int delta) {
    final next = (intervalSeconds + delta).clamp(
      minIntervalSeconds,
      maxIntervalSeconds,
    );
    return copyWith(intervalSeconds: next);
  }
}

/// 服务端听写设置（`GET /api/settings` 的相关键，契约 v1.3 §4.1/§7）。
///
/// 进听写时拉一次（APP-13 F17-1/F17-2）；拉取失败回退
/// `zh` / `defaultCountdownSeconds`（3）。
class DictationRemoteSettings {
  const DictationRemoteSettings({
    this.hint = 'zh',
    this.countdownSeconds = defaultCountdownSeconds,
    this.speechRate = defaultSpeechRate,
  });

  /// 倒计时回退值（契约 §4.1：dictation_countdown_s 值域 1~10）。
  static const int defaultCountdownSeconds = 3;

  /// 报词语速回退值（todo13 #5：管理端 dictation_speech_rate 0~100，
  /// App 换算 flutter_tts setSpeechRate = 值/100；默认 45 = 中速偏慢）。
  static const double defaultSpeechRate = 0.45;

  /// 报词屏显语言：`zh` = 中文释义，`en` = 英文（回退 zh）。
  final String hint;

  /// 报词后 N 秒倒计时自动报下一个（1~10）。
  final int countdownSeconds;

  /// 报词 TTS 语速（0.0~1.0；来自管理端设置，拉取失败用默认值）。
  final double speechRate;

  factory DictationRemoteSettings.fromJson(Map<String, dynamic> json) {
    final rawHint = json['dictation_hint']?.toString() ?? 'zh';
    final seconds =
        (json['dictation_countdown_s'] as num?)?.toInt() ??
            defaultCountdownSeconds;
    final rate = (json['dictation_speech_rate'] as num?)?.toDouble();
    return DictationRemoteSettings(
      hint: rawHint == 'en' ? 'en' : 'zh',
      countdownSeconds: seconds.clamp(1, 10),
      speechRate: rate == null
          ? defaultSpeechRate
          // 后端域 0~100，异常值一并兜到默认（正常不会走到）
          : (rate / 100).clamp(0.1, 1.0),
    );
  }

  /// 当前词的屏显文本（hint=zh 显示释义，hint=en 显示英文）。
  String hintTextOf(DictationItem item) => hint == 'en' ? item.en : item.zh;
}

/// 单词批改结果（`POST /api/dictation/grade` 响应的 results 项）。
class GradeWordResult {
  const GradeWordResult({
    required this.word,
    required this.recognized,
    required this.correct,
    required this.confidence,
    required this.uncertain,
  });

  factory GradeWordResult.fromJson(Map<String, dynamic> json) =>
      GradeWordResult(
        word: json['word']?.toString() ?? '',
        recognized: json['recognized']?.toString() ?? '',
        correct: json['correct'] == true,
        confidence: (json['confidence'] as num?)?.toInt() ?? 0,
        uncertain: json['uncertain'] == true,
      );

  final String word;

  /// 与该词对齐上的 OCR 原文；无对齐为空串。
  final String recognized;
  final bool correct;

  /// 0~100。
  final int confidence;

  /// 置信度 <85 →「拿不准」，结果页标灰提示复核。
  final bool uncertain;
}

/// 批改报告（`/api/dictation/grade` 的 data）。
class DictationGradeReport {
  const DictationGradeReport({
    required this.results,
    required this.ocrContent,
  });

  factory DictationGradeReport.fromJson(Map<String, dynamic> json) {
    final rawResults = json['results'];
    return DictationGradeReport(
      results: rawResults is List
          ? rawResults
              .whereType<Map>()
              .map((e) => GradeWordResult.fromJson(e.cast<String, dynamic>()))
              .toList(growable: false)
          : const <GradeWordResult>[],
      ocrContent: json['ocr_content']?.toString() ?? '',
    );
  }

  final List<GradeWordResult> results;
  final String ocrContent;

  int get correctCount => results.where((r) => r.correct).length;
  int get wrongCount => results.where((r) => !r.correct).length;
}

/// 听写链路的路由参数包（设置页 → 播报页 → 拍照页 一路携带）。
class DictationSessionArgs {
  const DictationSessionArgs({
    required this.items,
    this.unitName = '',
    this.returnRoute,
  });

  /// 本次要听写的题目（来自 practice/start）。
  final List<DictationItem> items;

  /// 单元名（结果页/引导页展示用）。
  final String unitName;

  /// 结算后自动跳回的路由名（单元列表页，由 WP-C 的 J1 接线时传入）；
  /// 为空则退回应用首页栈底。
  final String? returnRoute;
}
