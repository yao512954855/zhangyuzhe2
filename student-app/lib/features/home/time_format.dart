/// 时间展示 —— UI 规范 §7.4「上次：今天 09:01」。
///
/// ⚠️ 服务端时间戳是 **UTC**（`utc_now_iso()` 带 `Z`），必须先 `toLocal()`
/// 再判断"今天/昨天"，否则东八区学生早上 8 点练完，卡片会显示成"昨天"。
///
/// `now` 可注入 —— 否则"今天/昨天"的用例在跨零点时会随机失败（假红）。
library;

/// ISO8601 → 「今天 09:01」/「昨天 09:01」/「9月20日 09:01」/「2025年9月20日 09:01」。
///
/// 解析不了就返回 `null`（调用方据此不显示这一行），**不返回"无效时间"之类的怪文案**。
String? formatLastPractice(Object? iso, {DateTime? now}) {
  final parsed = _parse(iso);
  if (parsed == null) {
    return null;
  }
  final local = parsed.toLocal();
  final today = (now ?? DateTime.now()).toLocal();
  final clock = '${_two(local.hour)}:${_two(local.minute)}';

  final dayDiff = _dateOnly(today).difference(_dateOnly(local)).inDays;
  if (dayDiff == 0) {
    return '今天 $clock';
  }
  if (dayDiff == 1) {
    return '昨天 $clock';
  }
  if (local.year == today.year) {
    return '${local.month}月${local.day}日 $clock';
  }
  return '${local.year}年${local.month}月${local.day}日 $clock';
}

/// 缓存写入时间 → 「刚刚」/「3 分钟前」/「2 小时前」/「今天 09:01」。
///
/// 断网降级时 UI 要说清"你看到的是什么时候的数据"（F15 / U10）。
String? formatCachedAt(Object? iso, {DateTime? now}) {
  final parsed = _parse(iso);
  if (parsed == null) {
    return null;
  }
  final local = parsed.toLocal();
  final current = (now ?? DateTime.now()).toLocal();
  final diff = current.difference(local);

  if (diff.inSeconds < 60) {
    return '刚刚';
  }
  if (diff.inMinutes < 60) {
    return '${diff.inMinutes} 分钟前';
  }
  if (diff.inHours < 24) {
    return '${diff.inHours} 小时前';
  }
  return formatLastPractice(iso, now: now);
}

/// 学期代码 → 「上学期」/「下学期」。
String semesterLabel(int? code) => switch (code) {
      1 => '上学期',
      2 => '下学期',
      _ => '未选学期',
    };

/// 按当前月份猜学期：**8 月 – 次年 1 月 = 上学期，2 – 7 月 = 下学期**。
///
/// 只用于"预选"（少点一次），学生仍要按确定。猜错也无害 ——
/// 首页顶部随时可改（F2：之后长期记忆，不再询问）。
int suggestSemester(DateTime now) => (now.month >= 8 || now.month <= 1) ? 1 : 2;

/// 年级 + 学期 → 「三年级 · 上学期」。
String gradeSemesterLabel(int? grade, int? semester) {
  if (grade == null || semester == null) {
    return '还没选择年级';
  }
  return '$grade 年级 · ${semesterLabel(semester)}';
}

DateTime? _parse(Object? raw) {
  final text = raw?.toString();
  if (text == null || text.isEmpty) {
    return null;
  }
  return DateTime.tryParse(text);
}

DateTime _dateOnly(DateTime value) => DateTime(value.year, value.month, value.day);

String _two(int value) => value.toString().padLeft(2, '0');
