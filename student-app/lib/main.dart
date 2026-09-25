/// 学生端入口 —— 30-APP-01 F1 / F19。
///
/// 启动顺序（**顺序不能换**）：
/// `ensureInitialized` → 覆盖 `ErrorWidget.builder`（不红屏）
/// → 异步打开本地存储 → `runApp`（把已就绪的 store 注入 ProviderScope）。
///
/// 本地存储必须在 `runApp` 之前拿到：主题、服务器地址、token 都要用它决定首屏，
/// 若放在 App 里异步加载，会出现"先日间闪一下再变夜间"的闪烁
/// —— 而 UI 规范 §2.5 明令禁止闪烁。
library;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app.dart';
import 'core/providers.dart';
import 'core/storage/local_store.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // F19：任何 widget 构建异常都不能露出红屏与英文堆栈
  ErrorWidget.builder = (details) => SafeErrorWidget(details: details);

  final store = await LocalStore.open();

  runApp(
    ProviderScope(
      overrides: [localStoreProvider.overrideWithValue(store)],
      child: const StudentApp(),
    ),
  );
}

/// 兜底错误 UI。
///
/// 它挂在 `MaterialApp` **之上**，所以拿不到 Theme / MediaQuery /
/// Directionality —— 必须全部自带，否则错误组件自己会再抛一次异常（很难排查）。
class SafeErrorWidget extends StatelessWidget {
  const SafeErrorWidget({super.key, required this.details});

  final FlutterErrorDetails details;

  @override
  Widget build(BuildContext context) {
    return Directionality(
      textDirection: TextDirection.ltr,
      child: Container(
        color: const Color(0xFFF7F5F0), // 用日间暖白，即使主题是夜间也不会突兀地闪白
        alignment: Alignment.center,
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(
              Icons.sentiment_neutral_rounded,
              size: 40,
              color: Color(0xFF9A9A9A),
            ),
            const SizedBox(height: 12),
            const Text(
              '这里出了点小问题',
              style: TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w500,
                color: Color(0xFF2B2B2B),
              ),
            ),
            const SizedBox(height: 6),
            const Text(
              '回到上一步再试一次就好',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 14, color: Color(0xFF6B6B6B)),
            ),
            // 学生看不到技术细节；debug 构建下开发者需要它来定位
            if (kDebugMode) ...[
              const SizedBox(height: 16),
              Text(
                details.exceptionAsString(),
                textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 12, color: Color(0xFF9A9A9A)),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
