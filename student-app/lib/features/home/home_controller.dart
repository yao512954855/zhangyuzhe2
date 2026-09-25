/// 首页控制器 —— APP-02 F1 ~ F5 / F15。
///
/// 分工（别把这三件事混在一个地方写）：
/// - **本文件**：把"年级学期偏好 + 网络请求 + 缓存降级"合成一个可渲染的状态；
/// - `home_repository.dart`：只管发请求与缓存；
/// - 页面：只管画，不做任何计算。
///
/// 刷新时机只有三个，都要在此收口：
/// 1. 首次进入（`build()`）；
/// 2. 学生切换年级学期 —— 偏好一变，`build()` 自动重跑（靠 `ref.watch`）；
/// 3. 下拉刷新（[refresh]）。
library;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/net/api_exception.dart';
import '../../core/providers.dart';
import 'home_repository.dart';
import 'models.dart';

/// 首页一次性拿到的全部数据。
@immutable
class HomeData {
  const HomeData({
    required this.units,
    this.progress,
    this.wrongCount = 0,
    this.preferencePending = false,
  });

  final UnitsPage units;

  /// 进度总览。**允许为 null** —— 它是锦上添花的信息，
  /// 拉失败不该让整个首页变成错误页（U10 要的是"不崩溃"）。
  final ProgressOverview? progress;

  /// 错题总数（「只练错题」常驻卡上的数字）。
  final int wrongCount;

  /// 年级学期已存在本机、但还没同步到服务端（断网时选过）。
  final bool preferencePending;

  /// 数据来自缓存（断网降级）。
  bool get offline => units.fromCache;

  /// 有没有错题可练（决定「只练错题」卡是否可用）。
  bool get hasWrongWords => wrongCount > 0;

  HomeData copyWith({
    UnitsPage? units,
    ProgressOverview? progress,
    int? wrongCount,
    bool? preferencePending,
  }) =>
      HomeData(
        units: units ?? this.units,
        progress: progress ?? this.progress,
        wrongCount: wrongCount ?? this.wrongCount,
        preferencePending: preferencePending ?? this.preferencePending,
      );
}

class HomeController extends AsyncNotifier<HomeData> {
  HomeRepository get _repo => ref.read(homeRepositoryProvider);

  @override
  Future<HomeData> build() async {
    // watch（而不是 read）：学生一换年级学期，这里自动重建 → 列表刷新（U3）
    final prefs = ref.watch(gradePrefProvider);
    return _load(prefs.grade, prefs.semester);
  }

  Future<HomeData> _load(int? grade, int? semester) async {
    // 有未同步的偏好就顺手补一次。**不 await 结果决定成败** ——
    // 补同步失败只是"还没同步上"，不影响首页要不要显示。
    final stillPending = await _repo.flushPendingPreference();

    // 不传 subject：一次把该年级学期下的英语 + 语文全拉回来，
    // 切 Tab 纯本地过滤（不闪、不转圈）。护眼硬要求里有"禁止闪烁"。
    final units = await _repo.fetchUnits(grade: grade, semester: semester);

    return HomeData(
      units: units.value,
      progress: await _loadProgress(),
      wrongCount: await _loadWrongCount(),
      preferencePending: stillPending || _repo.hasPendingPreference(),
    );
  }

  /// 进度概览：失败返回 `null`，**不向上抛**。
  Future<ProgressOverview?> _loadProgress() async {
    try {
      final cached = await _repo.fetchProgress();
      return cached.value;
    } on ApiException {
      return null;
    }
  }

  /// 错题数：失败按 0 处理（卡片会显示成"暂时取不到"的样子，
  /// 而不是让整个首页跟着失败）。
  Future<int> _loadWrongCount() async {
    try {
      return await _repo.fetchWrongCount();
    } on ApiException {
      return 0;
    }
  }

  /// 下拉刷新。
  Future<void> refresh() async {
    ref.invalidateSelf();
    try {
      await future;
    } catch (_) {
      // 失败已经体现在 AsyncError 里，UI 会渲染错误态；这里吞掉避免未捕获异常
    }
  }

  /// 设置年级学期（F2 / F3）。
  ///
  /// 顺序**不可调换**：先本地、再服务端。
  /// 本地保存成功就代表"学生已经选好了"，服务端只是补同步。
  Future<void> applyGradeSemester({required int grade, required int semester}) async {
    // ① 本地先行（离线也必须能选，否则学生卡在引导页出不去）
    await ref.read(gradePrefProvider.notifier).save(grade: grade, semester: semester);
    // ② 服务端同步（失败只留 pending，不回滚本地）
    await _repo.syncPreference(grade: grade, semester: semester);
    // ③ 偏好变化已触发 build() 重建；这里再显式失效一次，
    //    保证"已是同一个年级、只是重新点一次确认"时也会刷新数据
    ref.invalidateSelf();
  }
}

/// 首页数据 Provider。
final homeControllerProvider =
    AsyncNotifierProvider<HomeController, HomeData>(HomeController.new);

/// 首页数据仓库。
final homeRepositoryProvider = Provider<HomeRepository>((ref) {
  return HomeRepository(
    api: ref.watch(apiClientProvider),
    store: ref.watch(localStoreProvider),
  );
});

/// 当前选中的学科 Tab。
///
/// 用 `Notifier` 而不是 `StateProvider`：flutter_riverpod 3.x 已把
/// `StateProvider` 归入 legacy，新代码不该再引入。
class HomeSubjectController extends Notifier<Subject> {
  @override
  Subject build() => Subject.english;

  void select(Subject subject) {
    if (state != subject) {
      state = subject;
    }
  }
}

final homeSubjectProvider =
    NotifierProvider<HomeSubjectController, Subject>(HomeSubjectController.new);
