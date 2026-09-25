/// 背诵展示用的「目标课文」模型 —— WP-APP-04（F1/F2/F4 渲染需要）。
///
/// **为什么存在**：UI 规范 §7.7 P8 要求「已背出的字正常显示」（真实汉字，
/// 不是占位块），但后端 `POST /api/recitations` 的响应**刻意不含正文**
/// （契约 F1：正文学生已在 §8.7 拿过，且判定前不能下发答案）。
///
/// 所以这里只负责「拿展示用正文」这一件与判分无关的事：
/// - 从 `GET /api/texts/{id}` 取回段落（§8.7 已确认返回 `paragraphs` + `recite_scope`）；
/// - 按**背诵范围**切片、抽成纯汉字序列（标点不进网格，F4 明文）；
/// - 网格只把「已背到的字」渲染出来，**未背到的字永远不显示**（F1/F2）。
///
/// 这个模型**不参与任何对齐/判分**（契约 §13 禁止客户端自对齐）——它只是
/// 学生已经念出来的字该显示成什么形状。范围切片口径与服务端 `build_scoped`
/// 一致（只累计 `recite_scope` 内的段落），否则会出现「范围不含第 0 段时整体错位」。
library;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';

/// 汉字码点区间 —— **必须与服务端 `PinyinService._HAN_RANGES` 逐段一致**。
///
/// ⚠️ 两份表同源是硬约束：`ReciteText.chars.length` 要恒等于服务端的 `total_chars`
/// （`len(han_only(content_plain))`），否则网格框数与「已背出 N 字」的索引会整体错位
/// —— 学生看到框数和实际要背的字数对不上。
///
/// 历史 bug（`D-APP04-01`，2026-09-25 实跑定位）：客户端原先只写了「基本区 + 扩展 A」
/// 两段，**漏了兼容表意（F900 区）与增补平面（20000+）**，含这类字的课文会被切短。
/// 注意这里给的是**码点**区间（用 `runes` 遍历），增补平面字符是单个码点、不是两个
/// UTF-16 code unit，所以不能用普通正则的 `\uXXXX` 转义表达。
const List<List<int>> _hanRanges = [
  [0x3400, 0x4DBF], // CJK 扩展 A
  [0x4E00, 0x9FFF], // CJK 基本区
  [0xF900, 0xFAFF], // CJK 兼容表意文字
  [0x20000, 0x2A6DF], // CJK 扩展 B
  [0x2A700, 0x2EBEF], // CJK 扩展 C ~ F
  [0x30000, 0x3134F], // CJK 扩展 G
];

/// 单个**码点**是否为汉字（与服务端 `is_han` 同判定）。
bool _isHan(int codePoint) {
  for (final range in _hanRanges) {
    if (codePoint >= range[0] && codePoint <= range[1]) return true;
  }
  return false;
}

/// 一篇课文在「背诵范围」内的展示数据。
@immutable
class ReciteText {
  const ReciteText({
    required this.chars,
    required this.paragraphCharCounts,
  });

  /// 背诵范围内的全部汉字（顺序即显示顺序，长度 == 服务端 `total_chars`）。
  final List<String> chars;

  /// 每段字数（与 `POST /api/recitations` 返回的 `char_count_per_paragraph` 对应，
  /// 用于在网格里按段换行、保留段落形状，F4）。
  final List<int> paragraphCharCounts;

  /// 由 §8.7 的 `paragraphs` + `recite_scope` 构造。
  ///
  /// [scope] 为空表示背全文（取 `recite_scope`）；否则只取指定段落。
  factory ReciteText.fromDetail(Map<String, dynamic> json, {List<int> scope = const []}) {
    final rawParagraphs = json['paragraphs'];
    final reciteScope = (json['recite_scope'] is List)
        ? (json['recite_scope'] as List)
            .map((e) => int.tryParse(e.toString()) ?? -1)
            .where((e) => e >= 0)
            .toList()
        : const <int>[];
    final picked = scope.isNotEmpty ? scope : reciteScope;

    final paras = <Map<String, dynamic>>[];
    if (rawParagraphs is List) {
      for (final raw in rawParagraphs) {
        if (raw is Map) {
          paras.add(Map<String, dynamic>.from(raw));
        }
      }
    }
    paras.sort((a, b) {
      final ia = int.tryParse(a['idx'].toString()) ?? 0;
      final ib = int.tryParse(b['idx'].toString()) ?? 0;
      return ia.compareTo(ib);
    });

    final chars = <String>[];
    final counts = <int>[];
    for (final para in paras) {
      final idx = int.tryParse(para['idx'].toString()) ?? -1;
      if (picked.isNotEmpty && !picked.contains(idx)) {
        continue;
      }
      final text = para['text']?.toString() ?? '';
      // 用 `runes`（码点）而不是 `code units`：增补平面汉字是单个码点。
      final hanzi = text.runes.where(_isHan).map(String.fromCharCode).toList();
      if (hanzi.isEmpty) {
        continue;
      }
      chars.addAll(hanzi);
      counts.add(hanzi.length);
    }
    return ReciteText(chars: chars, paragraphCharCounts: counts);
  }
}

/// `reciteTextProvider` 的查询键（文本 id + 本次背诵范围）。
@immutable
class ReciteTextKey {
  const ReciteTextKey(this.textId, this.scope);

  final int textId;
  final List<int> scope;

  @override
  bool operator ==(Object other) =>
      other is ReciteTextKey &&
      other.textId == textId &&
      const ListEquality<int>().equals(other.scope, scope);

  @override
  int get hashCode => Object.hash(textId, const ListEquality<int>().hash(scope));
}

/// 取一篇课文的展示正文（按背诵范围切片）。
///
/// 失败（拿不到正文）**不阻塞背诵**：返回空数据，网格届时只显示空框，
/// 学生仍然能正常背、进度照常结算（显示正文只是锦上添花）。
final reciteTextProvider =
    FutureProvider.family<ReciteText, ReciteTextKey>((ref, key) async {
  final api = ref.watch(apiClientProvider);
  try {
    final data = await api.get<Map<String, dynamic>>('/api/texts/${key.textId}');
    return ReciteText.fromDetail(data ?? const {}, scope: key.scope);
  } on ApiException {
    return const ReciteText(chars: [], paragraphCharCounts: []);
  }
});

class ListEquality<E> {
  const ListEquality();
  bool equals(List<E> a, List<E> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }

  int hash(List<E> list) =>
      list.fold(0, (prev, e) => Object.hash(prev, e));
}
