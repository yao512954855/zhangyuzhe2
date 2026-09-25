/// APP-02 首页数据仓库 —— 契约 §8.3 / §8.4 / §8.5 / §8.6 + F15 离线容错。
///
/// **唯一允许碰首页接口的地方**：页面与控制器只调这里的方法，
/// 不直接拼路径（否则接口一改要满 feature 找 `'/api/me/units'` 字符串）。
///
/// 离线容错（F15 / U10）的口径与 `AuthController.restore()` **保持一致**：
/// 只有**网络类失败**（`network` / `timeout`）才降级读缓存；
/// 服务端明确回了业务错误码（比如 `10002` 登录过期）时**不许拿缓存糊弄** ——
/// 那会把"该重新登录"伪装成"数据正常"，是比白屏更糟的故障。
library;

import '../../core/net/api_client.dart';
import '../../core/net/api_exception.dart';
import '../../core/storage/local_store.dart';
import 'models.dart';

/// 一次带缓存的读取结果。
class Cached<T> {
  const Cached(this.value, {this.fromCache = false, this.cachedAt});

  final T value;

  /// 数据来自本地缓存（UI 需要提示"当前无法连接服务器"）。
  final bool fromCache;

  final DateTime? cachedAt;
}

class HomeRepository {
  HomeRepository({required this.api, required this.store});

  final ApiClient api;
  final LocalStore store;

  /// 缓存名（`LocalStore` 会加 `cache.` 前缀）。
  static const cacheUnits = 'me_units';
  static const cacheProgress = 'me_progress';

  /// 「本地已选、但还没同步到服务端」的年级学期。
  ///
  /// 为什么不放进 `LocalStore` 加个键：`local_store.dart` 归 WP-APP-01 所有，
  /// 而这是 APP-02 的内部状态（契约「不修改他人文件」）。借 `cacheJson`
  /// 这一层现成能力即可，语义上也说得通 —— 它本来就是"临时挂起的数据"。
  static const cachePendingPreference = 'pref_pending_sync';

  // ------------------------------------------------------------------ 单元

  /// `GET /api/me/units`（契约 §8.3）。
  ///
  /// `grade` / `semester` 不传时服务端取用户偏好；三样都没有时
  /// 服务端返回 `{items: []}` 而**不报错**（学生刚注册还没选年级，
  /// 首页必须能正常渲染出引导页，而不是一个错误页）。
  Future<Cached<UnitsPage>> fetchUnits({
    Subject? subject,
    int? grade,
    int? semester,
  }) async {
    final key = _unitsCacheKey(subject, grade, semester);
    try {
      final data = await api.get<Map<String, dynamic>>(
        '/api/me/units',
        query: {
          'subject': subject?.wire,
          'grade': grade,
          'semester': _semesterWire(semester),
        },
      );
      final page = UnitsPage.fromJson(data ?? const <String, dynamic>{});
      await _writeCache(key, data);
      return Cached(page);
    } on ApiException catch (e) {
      final cached = _readCachedUnits(key);
      if (cached != null && _isOffline(e)) {
        return cached;
      }
      rethrow;
    }
  }

  // ------------------------------------------------------------------ 进度

  /// `GET /api/me/progress`（契约 §8.4）。
  Future<Cached<ProgressOverview>> fetchProgress() async {
    try {
      final data = await api.get<Map<String, dynamic>>('/api/me/progress');
      final progress = ProgressOverview.fromJson(data ?? const <String, dynamic>{});
      await _writeCache(cacheProgress, data);
      return Cached(progress);
    } on ApiException catch (e) {
      final cached = _readCachedProgress();
      if (cached != null && _isOffline(e)) {
        return cached;
      }
      rethrow;
    }
  }

  // ------------------------------------------------------------------ 内容

  /// `GET /api/units/{id}/words`（契约 §8.5）。
  ///
  /// `only` 取 `wrong`（只练错题）/ `unmastered`（只练未掌握）。
  /// `mode` 会**隐藏出题侧**（`cn2en` 藏 `en`）—— 单元详情页要完整看两侧，
  /// 所以不传 `mode`。
  Future<List<WordItem>> fetchWords(int unitId, {String? only, String? mode}) async {
    final data = await api.get<Map<String, dynamic>>(
      '/api/units/$unitId/words',
      query: {'only': only, 'mode': mode},
    );
    final raw = data?['words'];
    if (raw is! List) {
      return const [];
    }
    return raw
        .whereType<Map>()
        .map((e) => WordItem.fromJson(Map<String, dynamic>.from(e)))
        .toList(growable: false);
  }

  /// `GET /api/units/{id}/texts`（契约 §8.6）。**不含正文**。
  Future<List<TextBrief>> fetchTexts(int unitId) async {
    final data = await api.get<Map<String, dynamic>>('/api/units/$unitId/texts');
    final raw = data?['texts'];
    if (raw is! List) {
      return const [];
    }
    return raw
        .whereType<Map>()
        .map((e) => TextBrief.fromJson(Map<String, dynamic>.from(e)))
        .toList(growable: false);
  }

  /// `GET /api/me/texts`… 不需要；下面是错题数。
  ///
  /// `GET /api/me/wrong-words`（契约 §8.8）—— 首页「只练错题（12 个）」
  /// 那个数字只需要**总数**，所以 `page_size=1` 只要一条记录：
  /// 把 12 条完整记录拉回来只为显示一个数字是浪费流量。
  Future<int> fetchWrongCount() async {
    final data = await api.get<Map<String, dynamic>>(
      '/api/me/wrong-words',
      query: {'page': 1, 'page_size': 1},
    );
    final total = data?['total'];
    if (total is num) {
      return total.toInt();
    }
    return int.tryParse(total?.toString() ?? '') ?? 0;
  }

  // -------------------------------------------------------------- 偏好同步

  /// `PUT /api/me/profile`（契约 §8.2）—— F3 双向持久化的"服务端那一半"。
  ///
  /// **不抛异常**，返回是否同步成功。理由（这是 F3 最容易写错的点）：
  /// 本地保存必须是"已生效"的事实，服务端同步只是"迟早要补上"的动作 ——
  /// 如果同步失败就回滚本地，学生在**断网时根本选不了年级**，
  /// 会卡在引导页出不去（比"换设备带不过来"严重得多）。
  ///
  /// 失败时把待同步内容记在本地，下次启动自动补（[flushPendingPreference]）。
  Future<bool> syncPreference({required int grade, required int semester}) async {
    try {
      await api.put<Map<String, dynamic>>(
        '/api/me/profile',
        body: {
          'preferred_grade': grade,
          'preferred_semester': _semesterWire(semester),
        },
      );
      await store.clearCache(cachePendingPreference);
      return true;
    } catch (_) {
      await store.cacheJson(cachePendingPreference, {
        'grade': grade,
        'semester': semester,
      });
      return false;
    }
  }

  /// 本地是否有"已选但未同步"的偏好（UI 据此提示「已保存在本机，联网后会同步」）。
  bool hasPendingPreference() => store.readCache(cachePendingPreference) is Map;

  /// 补同步。返回是否**仍然**有未同步项。
  ///
  /// 由首页 `build()` 调用 —— 学生每天早上打开 App 就是一次天然的补同步时机，
  /// 不需要额外的重试定时器。
  Future<bool> flushPendingPreference() async {
    final raw = store.readCache(cachePendingPreference);
    if (raw is! Map) {
      return false;
    }
    final grade = raw['grade'];
    final semester = raw['semester'];
    if (grade is! int || semester is! int) {
      // 缓存结构坏了（版本升级等）→ 直接丢弃，不要让首页卡在这
      await store.clearCache(cachePendingPreference);
      return false;
    }
    final ok = await syncPreference(grade: grade, semester: semester);
    return !ok;
  }

  // -------------------------------------------------------------- 内部实现

  /// 缓存键要**把查询条件编进去** —— 否则切了学科/年级会把上一个 Tab
  /// 的列表当成自己的缓存读出来，学生会看到"语文 Tab 里列着英语单元"。
  String _unitsCacheKey(Subject? subject, int? grade, int? semester) {
    final s = subject?.wire ?? 'all';
    final g = grade?.toString() ?? 'pref';
    final t = semester?.toString() ?? 'pref';
    return '$cacheUnits.$s.$g.$t';
  }

  bool _isOffline(ApiException e) =>
      e.kind == ApiErrorKind.network || e.kind == ApiErrorKind.timeout;

  Future<void> _writeCache(String key, Object? value) async {
    if (value == null) {
      return;
    }
    // 记录写入时间，UI 才能说清"这是什么时候的数据"
    await store.cacheJson(key, {
      'cached_at': DateTime.now().toIso8601String(),
      'payload': value,
    });
  }

  Cached<UnitsPage>? _readCachedUnits(String key) {
    final cached = store.readCache(key);
    if (cached is! Map) {
      return null;
    }
    final payload = cached['payload'];
    if (payload is! Map) {
      return null;
    }
    final page = UnitsPage.fromJson(Map<String, dynamic>.from(payload));
    return Cached(page, fromCache: true, cachedAt: _parseTime(cached['cached_at']));
  }

  Cached<ProgressOverview>? _readCachedProgress() {
    final cached = store.readCache(cacheProgress);
    if (cached is! Map) {
      return null;
    }
    final payload = cached['payload'];
    if (payload is! Map) {
      return null;
    }
    final progress = ProgressOverview.fromJson(Map<String, dynamic>.from(payload));
    return Cached(progress, fromCache: true, cachedAt: _parseTime(cached['cached_at']));
  }

  DateTime? _parseTime(Object? raw) => DateTime.tryParse(raw?.toString() ?? '');

  String? _semesterWire(int? code) => switch (code) {
        1 => 'first',
        2 => 'second',
        _ => null,
      };
}
