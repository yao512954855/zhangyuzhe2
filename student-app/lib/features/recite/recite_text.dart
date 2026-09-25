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

/// 纯汉字集合（用于剔除标点/数字/字母，F4）。
final _hanzi = RegExp(r'[\u4e00-\u9fff\u3400-\u4dbf]');

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
      final hanzi = text.runes
          .where((r) => _hanzi.hasMatch(String.fromCharCode(r)))
          .map(String.fromCharCode)
          .toList();
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
