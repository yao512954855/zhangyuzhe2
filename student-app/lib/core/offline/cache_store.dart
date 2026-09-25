/// 内容缓存（范围 A：只缓存「待练内容」）—— WP-2M。
///
/// 设计：
/// - 在线时把「待练内容」（单元/课文/场次组卷结果等）写进来；
/// - 断网时仍能读出来，让学生「看得到已缓存的待练内容」；
/// - 数据一律经 [KvStore] 落盘，解析失败静默降级返回 `null`（缓存坏了不该崩）。
///
/// 本类**不定义**任何「待练内容」的具体形状 —— 它只负责「存 JSON / 取 JSON」，
/// 具体字段由调用方（组卷响应等）决定，避免离线层越界到业务契约。
library;

import 'dart:convert';

import 'kv_store.dart';

/// 断网可读的内容缓存。
class CacheStore {
  CacheStore({required this.store, required this.namespace});

  final KvStore store;

  /// 命名空间：避免离线缓存键与 `LocalStore` 的缓存键（或其它离线键）相撞。
  final String namespace;

  String _key(String name) => '$namespace.$name';

  /// 写一份可 JSON 序列化的内容（通常为 `Map` / `List`）。
  ///
  /// 覆盖写：同名内容只保留最新一份（断网前最后一次在线拿到的为准）。
  Future<void> put(String name, Object value) =>
      store.writeString(_key(name), jsonEncode(value));

  /// 读一份内容；不存在或解析失败返回 `null`。
  ///
  /// 返回 `Object?`（通常是 `Map<dynamic, dynamic>` / `List<dynamic>`），
  /// 调用方按需转换；深度相等比较不受泛型参数影响。
  Object? get(String name) {
    final raw = store.readString(_key(name));
    if (raw == null) {
      return null;
    }
    try {
      return jsonDecode(raw);
    } on Object {
      // 缓存损坏：静默降级，不抛（契约 §13 的精神 —— 缓存坏了不该崩）。
      return null;
    }
  }

  /// 是否存在某个缓存条目（命中判断，UI 可据此区分「有缓存」与「无缓存」）。
  bool contains(String name) => store.readString(_key(name)) != null;

  /// 删除单条缓存。
  Future<void> remove(String name) => store.remove(_key(name));

  /// 清空本命名空间下的全部缓存（不影响其它命名空间）。
  Future<void> clear() async {
    final owned = store.keys().where((k) => k.startsWith('$namespace.'));
    for (final k in owned) {
      await store.remove(k);
    }
  }
}
