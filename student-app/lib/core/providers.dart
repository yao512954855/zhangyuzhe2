/// 基础设施依赖注入 —— 学生端所有页面都从这里取「网络 / 存储 / 发现 / 护眼」。
///
/// **约定**：Provider 只放"跨 feature 的基础能力"。页面自己的状态
/// （首页列表、练习进度）由各自 feature 定义，不要塞进这里。
///
/// `localStoreProvider` 是唯一需要在 `main()` 里 override 的
/// （因为 `SharedPreferences.getInstance()` 是异步的，必须在 runApp 前拿到）。
library;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'discovery/broadcast_finder.dart';
import 'discovery/broadcast_finder_factory.dart';
import 'eye_care/eye_care_service.dart';
import 'net/api_client.dart';
import 'net/ws_client.dart';
import 'storage/local_store.dart';
import 'storage/server_store.dart';
import 'theme/age_profile.dart';

/// 本地存储。**必须在 `main()` 里 override**：
/// ```dart
/// ProviderScope(
///   overrides: [localStoreProvider.overrideWithValue(store)],
///   child: const App(),
/// )
/// ```
final localStoreProvider = Provider<LocalStore>(
  (ref) => throw StateError(
    'localStoreProvider 未初始化：请在 main() 里用 overrideWithValue(await LocalStore.open()) 注入。',
  ),
);

/// 服务器地址记忆。
final serverStoreProvider = Provider<ServerStore>((ref) {
  final store = ServerStore(ref.watch(localStoreProvider));
  ref.onDispose(store.dispose);
  return store;
});

/// HTTP 客户端。
final apiClientProvider = Provider<ApiClient>((ref) {
  return ApiClient(
    serverStore: ref.watch(serverStoreProvider),
    localStore: ref.watch(localStoreProvider),
  );
});

/// 语音 WebSocket 客户端。
///
/// 用 `autoDispose`：退出练习页后连接应当被释放，
/// 否则全局并发闸门（后端 60 路）会被挂着的死连接占掉。
final wsClientProvider = Provider.autoDispose<WsClient>((ref) {
  final client = WsClient(
    serverStore: ref.watch(serverStoreProvider),
    localStore: ref.watch(localStoreProvider),
  );
  ref.onDispose(client.dispose);
  return client;
});

/// UDP 广播发现（按平台自动选择实现；Web 下 `supported == false`）。
final broadcastFinderProvider = Provider<BroadcastFinder>((ref) {
  final finder = createBroadcastFinder();
  ref.onDispose(finder.dispose);
  return finder;
});

/// 护眼提醒服务。
final eyeCareProvider = Provider<EyeCareService>((ref) {
  final service = EyeCareService(
    enabled: ref.watch(localStoreProvider).eyeCareEnabled,
  );
  ref.onDispose(service.dispose);
  return service;
});

/// 年级 + 学期偏好（本地值）。
@immutable
class GradeSemester {
  const GradeSemester({this.grade, this.semester});

  /// 1–9。
  final int? grade;

  /// 1 = 上学期，2 = 下学期。
  final int? semester;

  /// 是否已经选过（首次登录要强制选一次，见 APP-02 F2）。
  bool get isSet => grade != null && semester != null;

  String get semesterLabel => switch (semester) {
        1 => '上学期',
        2 => '下学期',
        _ => '',
      };

  String get label => isSet ? '$grade 年级 $semesterLabel' : '还没选择';

  bool get isGradeValid {
    final g = grade;
    return g != null && g >= 1 && g <= 9;
  }

  @override
  bool operator ==(Object other) =>
      other is GradeSemester &&
      other.grade == grade &&
      other.semester == semester;

  @override
  int get hashCode => Object.hash(grade, semester);
}

/// 年级学期偏好的读写。
///
/// 只负责**本地**这一半；「首次强制引导 + 同步到服务端」是 APP-02 的职责，
/// 它会 watch 这个 Provider 并在 `isSet == false` 时把学生挡在选择页。
class GradePrefController extends Notifier<GradeSemester> {
  @override
  GradeSemester build() {
    final store = ref.read(localStoreProvider);
    return GradeSemester(grade: store.grade, semester: store.semester);
  }

  Future<void> save({required int grade, required int semester}) async {
    state = GradeSemester(grade: grade, semester: semester);
    await ref.read(localStoreProvider).setGradeSemester(
          grade: grade,
          semester: semester,
        );
  }
}

final gradePrefProvider =
    NotifierProvider<GradePrefController, GradeSemester>(GradePrefController.new);

/// 当前分龄档位（字号与触控目标都从它取）。
final ageProfileProvider = Provider<AgeProfile>((ref) {
  return AgeProfile.ofGrade(ref.watch(gradePrefProvider).grade);
});
