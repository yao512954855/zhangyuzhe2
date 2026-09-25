/// 广播发现的**平台选择**。
///
/// 条件导入规则：默认取 `broadcast_finder_web.dart`；
/// 当 `dart.library.io` 可用（Android / iOS / 桌面）时改用 `broadcast_finder_io.dart`。
///
/// 业务代码只 `import 'broadcast_finder_factory.dart'` 并调 [createBroadcastFinder]，
/// **不要**直接 import 那两个实现文件，否则 Web 构建会因为 `dart:io` 失败。
library;

import 'broadcast_finder.dart';
import 'broadcast_finder_web.dart'
    if (dart.library.io) 'broadcast_finder_io.dart' as impl;

/// 按当前平台创建广播发现器。
BroadcastFinder createBroadcastFinder() => impl.createBroadcastFinder();
