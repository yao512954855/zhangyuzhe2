/// 本地键值存储 —— 偏好 / token / 缓存。
///
/// 用 `shared_preferences`（三端一致，含 Web），键名集中在此文件，
/// **业务代码不许自己拼 key 字符串**，否则改一次键名要满仓库找。
///
/// 规格里的「文件缓存」（30-APP-01 F13）落在 [LocalStore.cacheJson]：
/// 首页单元列表这类"断网也要能看"的数据走它。
library;

import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// 键名常量。带域前缀，便于调试时一眼看出归属。
abstract final class StoreKeys {
  // 服务器
  static const serverHost = 'server.host';
  static const serverPort = 'server.port';
  static const serverRecents = 'server.recents';
  static const serverLastOkAt = 'server.last_ok_at';

  // 登录
  static const authToken = 'auth.token';
  static const authUserId = 'auth.user_id';
  static const authUsername = 'auth.username';
  static const authRealName = 'auth.real_name';
  static const authClassId = 'auth.class_id';

  // 偏好
  static const prefTheme = 'pref.theme';
  static const prefAutoWarm = 'pref.auto_warm';
  static const prefEyeCare = 'pref.eye_care';
  static const prefReduceMotion = 'pref.reduce_motion';
  static const prefGrade = 'pref.grade';
  static const prefSemester = 'pref.semester';

  // 缓存
  static const cachePrefix = 'cache.';
}

/// 轻封装，让业务层只关心类型，不关心 key 与序列化。
class LocalStore {
  LocalStore(this._prefs);

  final SharedPreferences _prefs;

  static Future<LocalStore> open() async =>
      LocalStore(await SharedPreferences.getInstance());

  // ---------------------------------------------------------------- 服务器

  String? get serverHost => _prefs.getString(StoreKeys.serverHost);

  int? get serverPort => _prefs.getInt(StoreKeys.serverPort);

  List<String> get serverRecents =>
      _prefs.getStringList(StoreKeys.serverRecents) ?? const [];

  Future<void> saveServer({
    required String host,
    required int port,
    List<String> recents = const [],
  }) async {
    await _prefs.setString(StoreKeys.serverHost, host);
    await _prefs.setInt(StoreKeys.serverPort, port);
    await _prefs.setStringList(StoreKeys.serverRecents, recents);
    await _prefs.setString(StoreKeys.serverLastOkAt, DateTime.now().toIso8601String());
  }

  Future<void> clearServer() async {
    await _prefs.remove(StoreKeys.serverHost);
    await _prefs.remove(StoreKeys.serverPort);
  }

  // ------------------------------------------------------------------ 登录

  String? get token => _prefs.getString(StoreKeys.authToken);

  int? get userId => _prefs.getInt(StoreKeys.authUserId);

  String? get username => _prefs.getString(StoreKeys.authUsername);

  String? get realName => _prefs.getString(StoreKeys.authRealName);

  int? get classId => _prefs.getInt(StoreKeys.authClassId);

  Future<void> saveAuth({
    required String token,
    required int userId,
    required String username,
    String? realName,
    int? classId,
  }) async {
    await _prefs.setString(StoreKeys.authToken, token);
    await _prefs.setInt(StoreKeys.authUserId, userId);
    await _prefs.setString(StoreKeys.authUsername, username);
    if (realName != null) {
      await _prefs.setString(StoreKeys.authRealName, realName);
    }
    if (classId != null) {
      await _prefs.setInt(StoreKeys.authClassId, classId);
    }
  }

  /// 只清登录态，**保留服务器地址与偏好** ——
  /// token 过期后重开不该让学生重新扫码找服务器（这是最容易被写成 bug 的一步）。
  Future<void> clearAuth() async {
    await _prefs.remove(StoreKeys.authToken);
    await _prefs.remove(StoreKeys.authUserId);
    await _prefs.remove(StoreKeys.authUsername);
    await _prefs.remove(StoreKeys.authRealName);
    await _prefs.remove(StoreKeys.authClassId);
  }

  // ------------------------------------------------------------------ 偏好

  String? get themeId => _prefs.getString(StoreKeys.prefTheme);

  Future<void> setThemeId(String value) =>
      _prefs.setString(StoreKeys.prefTheme, value);

  bool get autoWarm => _prefs.getBool(StoreKeys.prefAutoWarm) ?? true;

  Future<void> setAutoWarm(bool value) =>
      _prefs.setBool(StoreKeys.prefAutoWarm, value);

  bool get eyeCareEnabled => _prefs.getBool(StoreKeys.prefEyeCare) ?? true;

  Future<void> setEyeCareEnabled(bool value) =>
      _prefs.setBool(StoreKeys.prefEyeCare, value);

  bool get reduceMotion => _prefs.getBool(StoreKeys.prefReduceMotion) ?? false;

  Future<void> setReduceMotion(bool value) =>
      _prefs.setBool(StoreKeys.prefReduceMotion, value);

  int? get grade => _prefs.getInt(StoreKeys.prefGrade);

  int? get semester => _prefs.getInt(StoreKeys.prefSemester);

  Future<void> setGradeSemester({required int grade, required int semester}) async {
    await _prefs.setInt(StoreKeys.prefGrade, grade);
    await _prefs.setInt(StoreKeys.prefSemester, semester);
  }

  // ------------------------------------------------------------------ 缓存

  /// 写一份 JSON 缓存（断网时可读）。
  Future<void> cacheJson(String name, Object? value) =>
      _prefs.setString('${StoreKeys.cachePrefix}$name', jsonEncode(value));

  /// 读 JSON 缓存；不存在或解析失败返回 `null`（**静默降级**，缓存坏了不该崩）。
  Object? readCache(String name) {
    final raw = _prefs.getString('${StoreKeys.cachePrefix}$name');
    if (raw == null) {
      return null;
    }
    try {
      return jsonDecode(raw);
    } catch (_) {
      return null;
    }
  }

  Future<void> clearCache(String name) =>
      _prefs.remove('${StoreKeys.cachePrefix}$name');
}
