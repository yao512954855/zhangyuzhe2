/// P1 服务器发现页 —— UI 规范 §7.2（U1 ~ U5）。
///
/// **设计意图（照抄规范要点，不要自作聪明"优化"掉）**：
/// - 启动即**静默尝试**上次成功的地址 + UDP 广播，全程不弹错误；
/// - 发现成功**自动进入**下一个页面，学生完全无感知；
/// - 只有全部失败才展示本页，且**主按钮是扫码**（不是手动输入）；
/// - 文案里不出现「IP」「端口」「网络」等术语。
///
/// 这条链路的第一道坎之所以放在最前面，是因为**连不上服务器，后面全是白做**。
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../../core/discovery/qr_parser.dart';
import '../../core/providers.dart';
import '../../core/storage/server_store.dart';
import '../../core/theme/age_profile.dart';
import '../../core/theme/app_theme.dart';
import '../../core/widgets/app_button.dart';
import '../../core/widgets/feedback.dart';

/// 页面在找服务器过程中的状态。
enum _FindPhase { searching, failed }

/// 服务器发现页。
class ServerSetupPage extends ConsumerStatefulWidget {
  const ServerSetupPage({super.key, this.autoFind = true});

  /// 启动时是否自动找（从启动页过来时为 `true`）。
  final bool autoFind;

  @override
  ConsumerState<ServerSetupPage> createState() => _ServerSetupPageState();
}

class _ServerSetupPageState extends ConsumerState<ServerSetupPage> {
  _FindPhase _phase = _FindPhase.searching;
  String _status = '正在寻找服务器…';
  String? _lastFailReason;
  bool _connecting = false;

  @override
  void initState() {
    super.initState();
    if (widget.autoFind) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _autoFind());
    } else {
      _phase = _FindPhase.failed;
      _status = '请扫码或输入老师给你的地址';
    }
  }

  @override
  void dispose() {
    super.dispose();
  }

  // ------------------------------------------------------------ 自动发现

  /// 启动静默发现：先试记忆过的地址，再广播。
  Future<void> _autoFind() async {
    if (!mounted) {
      return;
    }
    setState(() {
      _phase = _FindPhase.searching;
      _status = '正在寻找服务器…';
    });

    final serverStore = ref.read(serverStoreProvider);

    // ① 记忆中的地址（含最近成功的那几个），逐个快速试
    final candidates = <ServerAddress>{
      if (serverStore.current != null) serverStore.current!,
      ...serverStore.recents,
    }.toList();
    for (final address in candidates) {
      if (!mounted) {
        return;
      }
      if (await _probe(address, quiet: true)) {
        await _enter(address);
        return;
      }
    }

    // ② UDP 广播（部分路由器会拦，失败就静默降级到扫码）
    final finder = ref.read(broadcastFinderProvider);
    if (finder.supported) {
      final found = await finder.find(
        onStatus: (s) {
          if (mounted) {
            setState(() => _status = s);
          }
        },
      );
      if (!mounted) {
        return;
      }
      if (found != null && await _probe(found, quiet: true)) {
        await _enter(found);
        return;
      }
      _lastFailReason = finder.lastError;
    } else {
      _lastFailReason = finder.lastError;
    }

    if (!mounted) {
      return;
    }
    setState(() {
      _phase = _FindPhase.failed;
      _status = '请扫码或输入老师给你的地址';
    });
  }

  /// 探活。
  Future<bool> _probe(ServerAddress address, {bool quiet = false}) async {
    final api = ref.read(apiClientProvider);
    final info = await api.ping(base: address, timeout: const Duration(seconds: 3));
    if (info != null) {
      return true;
    }
    if (!quiet) {
      _lastFailReason = '这个地址连不上，检查一下有没有输错';
    }
    return false;
  }

  /// 连上了：保存地址并自动进入登录页。
  Future<void> _enter(ServerAddress address) async {
    await ref.read(serverStoreProvider).save(address);
    if (!mounted) {
      return;
    }
    // 用回调让上层决定去哪（启动页会据此判断是进登录还是进首页）
    Navigator.of(context).pop(true);
  }

  // ------------------------------------------------------------ 扫码 / 手输

  Future<void> _scan() async {
    final raw = await Navigator.of(context).push<String>(
      MaterialPageRoute<String>(builder: (_) => const _ScanPage()),
    );
    if (raw == null || !mounted) {
      return;
    }
    final result = ServerLinkParser.parse(raw, fromQr: true);
    if (result is ServerLinkError) {
      setState(() => _lastFailReason = result.reason);
      return;
    }
    final address = (result as ServerLinkOk).address;
    await _connectManually(address);
  }

  Future<void> _manualInput() async {
    final controller = TextEditingController(
      text: ref.read(serverStoreProvider).current?.authority ??
          ref.read(serverStoreProvider).recents.firstOrNull?.authority ??
          '',
    );
    final raw = await showDialog<String>(
      context: context,
      builder: (ctx) {
        final profile = AgeScope.of(ctx);
        return AlertDialog(
          title: const Text('输入老师给的地址'),
          content: TextField(
            controller: controller,
            autofocus: true,
            keyboardType: TextInputType.text,
            decoration: const InputDecoration(hintText: '例：192.168.1.80'),
            style: profile.bodyStyle,
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(),
              child: const Text('取消'),
            ),
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(controller.text),
              child: const Text('连接'),
            ),
          ],
        );
      },
    );
    controller.dispose();
    if (raw == null || raw.trim().isEmpty || !mounted) {
      return;
    }

    final result = ServerLinkParser.parse(raw);
    if (result is ServerLinkError) {
      setState(() => _lastFailReason = result.reason);
      return;
    }
    await _connectManually((result as ServerLinkOk).address);
  }

  /// 用手输/扫码拿到的地址连一次，成功就进下一步。
  Future<void> _connectManually(ServerAddress address) async {
    setState(() {
      _connecting = true;
      _lastFailReason = null;
    });
    final ok = await _probe(address);
    if (!mounted) {
      return;
    }
    setState(() => _connecting = false);
    if (ok) {
      await _enter(address);
    } else {
      // 不弹模态框：错误就地显示在按钮上方（UI 规范 §5.2）
      setState(() {});
    }
  }

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    final profile = AgeScope.of(context);
    final isSearching = _phase == _FindPhase.searching;

    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AgeProfile.screenEdgeInset * 1.5,
          ),
          child: Column(
            children: [
              const Spacer(flex: 2),
              Icon(
                Icons.menu_book_rounded,
                size: 64,
                color: Theme.of(context).colorScheme.primary,
              ),
              const SizedBox(height: AgeProfile.elementGap),
              Text('背记系统', style: profile.pageTitleStyle),
              const Spacer(),
              _StatusCard(
                status: _status,
                searching: isSearching,
                failReason: _phase == _FindPhase.failed ? _lastFailReason : null,
              ),
              const Spacer(),
              if (_connecting) ...[
                const LoadingView(message: '正在连接…'),
                const SizedBox(height: AgeProfile.elementGap),
              ],
              if (_phase == _FindPhase.failed && _lastFailReason != null) ...[
                InlineNotice(text: _lastFailReason!, kind: NoticeKind.error),
                const SizedBox(height: AgeProfile.elementGap),
              ],
              AppButton(
                label: '扫码连接',
                icon: Icons.qr_code_scanner_rounded,
                onPressed: _connecting ? null : _scan,
              ),
              const SizedBox(height: AgeProfile.adjacentButtonGap),
              AppButton(
                label: '手动输入地址',
                kind: AppButtonKind.secondary,
                onPressed: _connecting ? null : _manualInput,
              ),
              if (_phase == _FindPhase.failed) ...[
                const SizedBox(height: AgeProfile.elementGap),
                AppButton(
                  label: '再找一次',
                  kind: AppButtonKind.text,
                  onPressed: _connecting ? null : _autoFind,
                ),
              ],
              const Spacer(flex: 2),
              Text(
                '请让老师给你看管理端首页上的二维码',
                textAlign: TextAlign.center,
                style: profile.captionStyle.copyWith(color: extra.textFaint),
              ),
              const SizedBox(height: AgeProfile.elementGap),
            ],
          ),
        ),
      ),
    );
  }
}

/// 状态卡片：寻找中显示三点动画（**慢速呼吸，绝不闪**）。
class _StatusCard extends StatefulWidget {
  const _StatusCard({required this.status, required this.searching, this.failReason});

  final String status;
  final bool searching;
  final String? failReason;

  @override
  State<_StatusCard> createState() => _StatusCardState();
}

class _StatusCardState extends State<_StatusCard>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    // 3 秒一个循环：远低于 §5.1 的 3Hz 上限，光敏性癫痫风险为零
    duration: const Duration(milliseconds: 3000),
  );

  @override
  void initState() {
    super.initState();
    if (widget.searching) {
      _controller.repeat();
    }
  }

  @override
  void didUpdateWidget(covariant _StatusCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.searching && !_controller.isAnimating) {
      _controller.repeat();
    } else if (!widget.searching && _controller.isAnimating) {
      _controller.stop();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final extra = AppExtraColors.of(context);
    final profile = AgeScope.of(context);
    final primary = Theme.of(context).colorScheme.primary;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 22),
      decoration: BoxDecoration(
        color: extra.card,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: extra.divider, width: 0.5),
      ),
      child: Column(
        children: [
          Text(
            widget.status,
            textAlign: TextAlign.center,
            style: profile.bodyStyle,
          ),
          if (widget.searching) ...[
            const SizedBox(height: 14),
            AnimatedBuilder(
              animation: _controller,
              builder: (context, _) {
                return Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: List.generate(3, (i) {
                    // 相位错开，形成"依次呼吸"而不是整排一起闪
                    final t = (_controller.value + i * 0.22) % 1.0;
                    final glow = (1 - (t - 0.5).abs() * 2).clamp(0.25, 1.0);
                    return Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 3),
                      child: Opacity(
                        opacity: glow,
                        child: Container(
                          width: 8,
                          height: 8,
                          decoration: BoxDecoration(
                            color: primary,
                            shape: BoxShape.circle,
                          ),
                        ),
                      ),
                    );
                  }),
                );
              },
            ),
          ],
        ],
      ),
    );
  }
}

/// 扫码页。
class _ScanPage extends StatefulWidget {
  const _ScanPage();

  @override
  State<_ScanPage> createState() => _ScanPageState();
}

class _ScanPageState extends State<_ScanPage> {
  final MobileScannerController _controller = MobileScannerController(
    detectionSpeed: DetectionSpeed.noDuplicates,
  );
  bool _handled = false;
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _onDetect(BarcodeCapture capture) {
    if (_handled) {
      return;
    }
    for (final barcode in capture.barcodes) {
      final raw = barcode.rawValue;
      if (raw == null || raw.isEmpty) {
        continue;
      }
      final result = ServerLinkParser.parse(raw, fromQr: true);
      if (result is ServerLinkOk) {
        _handled = true;
        Navigator.of(context).pop(raw);
        return;
      }
      setState(() => _error = '这不是背记系统的二维码，让老师重新出示一下');
    }
  }

  @override
  Widget build(BuildContext context) {
    final profile = AgeScope.of(context);
    final extra = AppExtraColors.of(context);

    return Scaffold(
      appBar: AppBar(title: const Text('扫一扫')),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: Stack(
                fit: StackFit.expand,
                children: [
                  MobileScanner(
                    controller: _controller,
                    onDetect: _onDetect,
                  ),
                  // 取景框
                  Center(
                    child: Container(
                      width: 240,
                      height: 240,
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(16),
                        border: Border.all(
                          color: Theme.of(context).colorScheme.primary,
                          width: 3,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(AgeProfile.screenEdgeInset),
              child: Column(
                children: [
                  if (_error != null) ...[
                    InlineNotice(text: _error!, kind: NoticeKind.error),
                    const SizedBox(height: AgeProfile.elementGap),
                  ],
                  Text(
                    '把镜头对准老师屏幕上的二维码',
                    style: profile.captionStyle.copyWith(color: extra.textSub),
                  ),
                  const SizedBox(height: AgeProfile.elementGap),
                  AppButton(
                    label: '扫不上？手动输入',
                    kind: AppButtonKind.secondary,
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
