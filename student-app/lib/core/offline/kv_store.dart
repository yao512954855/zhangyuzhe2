/// 离线层的持久化键值后端抽象 —— WP-2M（范围 A）。
///
/// 离线缓存与提交队列都只认这个抽象，不认具体引擎：
/// - 测试注入 [MemoryKvStore]（无副作用、无路径，便于纯逻辑验证）；
/// - 生产注入 [SharedPreferencesKvStore]（落 `shared_preferences`，与既有
///   `LocalStore` 共用同一套引擎，**不引入任何硬编码路径**）。
///
/// 这样「持久化」的语义被收敛到这一个边界：任何落库行为都必须经过它，
/// 重启后从同一个 store 重新读出即等于「重启后仍在」。
library;

import 'package:shared_preferences/shared_preferences.dart';

/// 极简键值存储抽象：只暴露离线层真正用得到的四个动作。
///
/// 故意不做「事务 / 批量」等离线层不需要的能力，避免把复杂度带进生产实现。
abstract class KvStore {
  /// 写入一个字符串值（离线层自己负责 JSON 序列化）。
  Future<void> writeString(String key, String value);

  /// 读出字符串值；不存在返回 `null`。
  String? readString(String key);

  /// 删除一个键。
  Future<void> remove(String key);

  /// 当前持有的全部键（[CacheStore.clear] 按命名空间清理用）。
  List<String> keys();

  /// 清空全部键（仅测试隔离用，生产业务不要直接清全局 store）。
  Future<void> clear();
}

/// 内存实现 —— 测试替身。
///
/// 它不是「假数据」，而是把「持久化边界」本身搬到内存里：队列/缓存把数据
/// 写进它、再从它读出，与写进 SharedPreferences 的字节流在**行为上等价**。
/// 因此「重启」= 用同一个 [MemoryKvStore] 重新构造队列实例，足以验证持久化。
class MemoryKvStore implements KvStore {
  MemoryKvStore([Map<String, String>? seed]) : _data = Map<String, String>.from(seed ?? const {});

  final Map<String, String> _data;

  @override
  Future<void> writeString(String key, String value) async => _data[key] = value;

  @override
  String? readString(String key) => _data[key];

  @override
  Future<void> remove(String key) async => _data.remove(key);

  @override
  List<String> keys() => _data.keys.toList(growable: false);

  @override
  Future<void> clear() async => _data.clear();
}

/// 生产实现 —— 复用 `shared_preferences`（与 `LocalStore` 同一引擎）。
///
/// ⚠️ 不写任何文件路径：SharedPreferences 的落盘位置由平台 SDK 决定，
/// 客户端代码既不关心也不允许写死（契约 §13.10 禁止硬编码路径）。
class SharedPreferencesKvStore implements KvStore {
  SharedPreferencesKvStore(this._prefs);

  final SharedPreferences _prefs;

  @override
  Future<void> writeString(String key, String value) => _prefs.setString(key, value);

  @override
  String? readString(String key) => _prefs.getString(key);

  @override
  Future<void> remove(String key) => _prefs.remove(key);

  @override
  List<String> keys() => _prefs.getKeys().toList(growable: false);

  @override
  Future<void> clear() => _prefs.clear();
}
