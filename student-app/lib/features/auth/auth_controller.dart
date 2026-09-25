/// 登录 / 注册 / 启动校验 —— 30-APP-01 F5 / F14 / F15（U6 / U7 / U8）。
///
/// **一条容易被写错的产品规则**：启动时校验 token，
/// 若失败原因是「网络不通」而**不是**「token 失效」，
/// **绝不能把学生踢到登录页** —— 断网时他输密码也登不进去，
/// 只会以为"系统坏了"。正确做法是用本地缓存的身份继续进首页，
/// 由各页面在真正需要数据时给出网络提示（U15）。
/// 只有服务端明确回 `10002`（登录已过期）才清 token 跳登录（U8）。
library;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_client.dart';
import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import '../../core/storage/local_store.dart';

/// 学生用户信息（`UserOut` 的子集，学生端只用得到这些）。
@immutable
class StudentUser {
  const StudentUser({
    required this.id,
    required this.username,
    required this.realName,
    this.classId,
    this.className,
    this.preferredGrade,
    this.preferredSemester,
  });

  factory StudentUser.fromJson(Map<String, dynamic> json) => StudentUser(
        id: (json['id'] as num?)?.toInt() ?? 0,
        username: json['username']?.toString() ?? '',
        realName: json['real_name']?.toString() ?? '',
        classId: (json['class_id'] as num?)?.toInt(),
        className: json['class_name']?.toString(),
        preferredGrade: (json['preferred_grade'] as num?)?.toInt(),
        preferredSemester: json['preferred_semester']?.toString(),
      );

  final int id;
  final String username;
  final String realName;
  final int? classId;
  final String? className;
  final int? preferredGrade;
  final String? preferredSemester;

  /// 界面优先显示姓名（管理者看到的也是姓名），没有才退学号。
  String get displayName => realName.isNotEmpty ? realName : username;

  int? get preferredSemesterCode => switch (preferredSemester) {
        'first' => 1,
        'second' => 2,
        _ => null,
      };
}

/// 鉴权状态。
@immutable
class AuthState {
  const AuthState({
    this.checking = false,
    this.busy = false,
    this.user,
    this.offline = false,
  });

  /// 正在做启动校验（此时应显示启动页，而不是闪一下登录页）。
  final bool checking;

  /// 正在提交登录/注册（按钮转圈、防重复提交）。
  final bool busy;

  final StudentUser? user;

  /// 身份来自本地缓存、还没跟服务端核对上（断网启动）。
  final bool offline;

  bool get isLoggedIn => user != null;

  AuthState copyWith({
    bool? checking,
    bool? busy,
    StudentUser? user,
    bool? offline,
    bool clearUser = false,
  }) =>
      AuthState(
        checking: checking ?? this.checking,
        busy: busy ?? this.busy,
        user: clearUser ? null : (user ?? this.user),
        offline: offline ?? this.offline,
      );

  @override
  bool operator ==(Object other) =>
      other is AuthState &&
      other.checking == checking &&
      other.busy == busy &&
      other.user == user &&
      other.offline == offline;

  @override
  int get hashCode => Object.hash(checking, busy, user, offline);
}

/// 鉴权控制器。
class AuthController extends Notifier<AuthState> {
  @override
  AuthState build() => const AuthState();

  ApiClient get _api => ref.read(apiClientProvider);

  LocalStore get _store => ref.read(localStoreProvider);

  /// 启动校验：有 token 就核一次 `/api/auth/me`。
  ///
  /// 返回 `true` 表示可以直接进首页。
  Future<bool> restore() async {
    final token = _store.token;
    if (token == null || token.isEmpty) {
      state = const AuthState();
      return false;
    }

    state = state.copyWith(checking: true);
    try {
      final data = await _api.get<Map<String, dynamic>>('/api/auth/me');
      if (data == null) {
        await _store.clearAuth();
        state = const AuthState();
        return false;
      }
      final user = StudentUser.fromJson(data);
      state = AuthState(user: user);
      await _syncProfile(user);
      return true;
    } on ApiException catch (e) {
      if (e.isUnauthorized) {
        // U8：真正过期才清 token
        await _store.clearAuth();
        state = const AuthState();
        return false;
      }
      if (e.kind == ApiErrorKind.network || e.kind == ApiErrorKind.timeout) {
        // U15：断网不踢人，用本地缓存身份兜住
        state = AuthState(user: _cachedUser(), offline: true);
        return state.isLoggedIn;
      }
      // 其他（服务端 5xx 等）同样按离线处理，不轻易登出
      state = AuthState(user: _cachedUser(), offline: true);
      return state.isLoggedIn;
    }
  }

  /// 用本地缓存拼一个兜底身份（字段在登录时已落库）。
  StudentUser? _cachedUser() {
    final id = _store.userId;
    final username = _store.username;
    if (id == null || username == null) {
      return null;
    }
    return StudentUser(
      id: id,
      username: username,
      realName: _store.realName ?? username,
      classId: _store.classId,
    );
  }

  /// 登录（F14：学号 + 密码，`role` **由客户端固定传 `student`**，隔离管理端入口）。
  ///
  /// 失败时抛 [ApiException]，由页面展示其中文 `displayText`。
  Future<StudentUser> login({
    required String username,
    required String password,
  }) async {
    state = state.copyWith(busy: true);
    try {
      final data = await _api.post<Map<String, dynamic>>(
        '/api/auth/login',
        body: {
          'username': username.trim(),
          'password': password,
          'role': 'student',
        },
        override: const RequestOptionsOverride(skipAuth: true),
      );
      return await _acceptLogin(data);
    } finally {
      state = state.copyWith(busy: false);
    }
  }

  /// 注册（F15：学号 / 姓名 / 密码，可不选班级；**注册成功直接进首页，无需审核**）。
  ///
  /// `20001`（学号已存在）由后端给出中文文案，页面直接展示。
  Future<StudentUser> register({
    required String username,
    required String realName,
    required String password,
    int? classId,
  }) async {
    state = state.copyWith(busy: true);
    try {
      final data = await _api.post<Map<String, dynamic>>(
        '/api/auth/register',
        body: {
          'username': username.trim(),
          'real_name': realName.trim(),
          'password': password,
          if (classId != null) 'class_id': classId, // ignore: use_null_aware_elements
        },
        override: const RequestOptionsOverride(skipAuth: true),
      );
      return await _acceptLogin(data);
    } finally {
      state = state.copyWith(busy: false);
    }
  }

  /// 落地服务端返回的登录态（登录与注册共用）。
  Future<StudentUser> _acceptLogin(Map<String, dynamic>? data) async {
    if (data == null) {
      throw ApiException.parse();
    }
    final token = data['access_token']?.toString() ?? '';
    final userJson = data['user'];
    if (token.isEmpty || userJson is! Map<String, dynamic>) {
      throw ApiException.parse();
    }
    final user = StudentUser.fromJson(userJson);

    await _store.saveAuth(
      token: token,
      userId: user.id,
      username: user.username,
      realName: user.realName,
      classId: user.classId,
    );
    state = AuthState(user: user);
    await _syncProfile(user);
    return user;
  }

  /// 把服务端带过来的年级学期落到本地（为 APP-02 的「换设备带过来」铺路）。
  ///
  /// **只在本地还没选过时写入**：本地是学生当场选的、更新，不能被旧的云端值覆盖。
  Future<void> _syncProfile(StudentUser user) async {
    final grade = user.preferredGrade;
    final semester = user.preferredSemesterCode;
    if (grade == null || semester == null) {
      return;
    }
    final local = ref.read(gradePrefProvider);
    if (local.isSet) {
      return;
    }
    await ref.read(gradePrefProvider.notifier).save(
          grade: grade,
          semester: semester,
        );
  }

  /// 登出：**只清登录态，保留服务器地址与偏好**（否则重登要重新扫码）。
  Future<void> logout() async {
    await _store.clearAuth();
    state = const AuthState();
  }

  /// 供页面在收到网络错误后标记离线态。
  void markOffline() {
    if (state.isLoggedIn) {
      state = state.copyWith(offline: true);
    }
  }
}

/// 鉴权状态 Provider。
final authControllerProvider =
    NotifierProvider<AuthController, AuthState>(AuthController.new);
