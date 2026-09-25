/// WP-APP-01 服务发现 —— 地址解析 + 地址记忆（U2 / U3 / U4 / U5）。
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:student_app/core/discovery/broadcast_finder.dart';
import 'package:student_app/core/discovery/qr_parser.dart';
import 'package:student_app/core/storage/local_store.dart';
import 'package:student_app/core/storage/server_store.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('地址解析 · 扫码内容（接口设计 §3.3）', () {
    test('标准二维码内容', () {
      final r = ServerLinkParser.parse('bjsys://192.168.1.80:8810', fromQr: true);
      expect(r, isA<ServerLinkOk>());
      final ok = r as ServerLinkOk;
      expect(ok.address.host, '192.168.1.80');
      expect(ok.address.port, 8810);
      expect(ok.fromQr, isTrue);
    });

    test('二维码没带端口 → 用默认 8810（契约 §11）', () {
      final r = ServerLinkParser.parse('bjsys://192.168.1.80') as ServerLinkOk;
      expect(r.address.port, kDefaultServerPort);
      expect(kDefaultServerPort, 8810);
    });

    test('容忍大小写、结尾斜杠、查询参数、空白与零宽字符', () {
      for (final raw in [
        'BJSYS://192.168.1.80:8810',
        'bjsys://192.168.1.80:8810/',
        'bjsys://192.168.1.80:8810?from=admin',
        '  bjsys://192.168.1.80:8810  ',
        '\nbjsys://192.168.1.80:8810\n',
        'bjsys://192.168.1.80:8810\u200b',
      ]) {
        final r = ServerLinkParser.parse(raw, fromQr: true);
        expect(r, isA<ServerLinkOk>(), reason: '应能解析：$raw');
        expect((r as ServerLinkOk).address.authority, '192.168.1.80:8810');
      }
    });

    test('别的系统的二维码 → 明确拒绝，且文案能上屏', () {
      final r = ServerLinkParser.parse('https://www.example.com/x', fromQr: true);
      expect(r, isA<ServerLinkError>());
      expect((r as ServerLinkError).reason, contains('背记系统'));
    });

    test('不认识的 scheme 也拒绝', () {
      final r = ServerLinkParser.parse('ftp://192.168.1.80:8810', fromQr: true);
      expect(r, isA<ServerLinkError>());
    });

    test('空二维码', () {
      final r = ServerLinkParser.parse('   ', fromQr: true);
      expect(r, isA<ServerLinkError>());
      expect((r as ServerLinkError).reason, contains('二维码'));
    });

    test('往返：buildQrContent → parse', () {
      const address = ServerAddress(host: '10.0.0.7', port: 8810);
      final content = ServerLinkParser.buildQrContent(address);
      expect(content, 'bjsys://10.0.0.7:8810');
      expect((ServerLinkParser.parse(content) as ServerLinkOk).address, address);
    });
  });

  group('地址解析 · 手动输入（F8 / U5）', () {
    test('老师可能给的几种写法都能吃下', () {
      final cases = <String, String>{
        '192.168.1.80': '192.168.1.80:8810',
        '192.168.1.80:8810': '192.168.1.80:8810',
        'http://192.168.1.80:8810': '192.168.1.80:8810',
        'http://192.168.1.80:8810/admin': '192.168.1.80:8810',
        ' 192.168.1.80:9000 ': '192.168.1.80:9000',
      };
      cases.forEach((raw, expected) {
        final r = ServerLinkParser.parse(raw);
        expect(r, isA<ServerLinkOk>(), reason: '应能解析：$raw');
        expect((r as ServerLinkOk).address.authority, expected);
      });
    });

    test('主机名也允许（有些学校给的是域名）', () {
      final r = ServerLinkParser.parse('bjsys.local:8810') as ServerLinkOk;
      expect(r.address.host, 'bjsys.local');
    });

    test('错误输入给出具体原因，不是笼统的「失败」', () {
      final reasons = <String, String>{
        '': '输入',
        '999.1.1.1': '格式',
        '192.168.1.80:99999': '端口',
        '192.168.1.80:0': '端口',
        '192.168.1.80:88a': '格式',
        '192.168.01.80': '格式',
        'a b c': '格式',
      };
      reasons.forEach((raw, keyword) {
        final r = ServerLinkParser.parse(raw);
        expect(r, isA<ServerLinkError>(), reason: '应该报错：$raw');
        expect(
          (r as ServerLinkError).reason,
          contains(keyword),
          reason: '$raw 的原因应该说清楚',
        );
      });
    });

    test('tryParse 便捷入口', () {
      expect(ServerLinkParser.tryParse('192.168.1.80'), isNotNull);
      expect(ServerLinkParser.tryParse('@@@'), isNull);
    });
  });

  group('ServerAddress 校验', () {
    test('端口范围 1–65535', () {
      expect(ServerAddress.isValidPort(1), isTrue);
      expect(ServerAddress.isValidPort(65535), isTrue);
      expect(ServerAddress.isValidPort(0), isFalse);
      expect(ServerAddress.isValidPort(65536), isFalse);
      expect(ServerAddress.isValidPort(null), isFalse);
    });

    test('主机名合法性', () {
      expect(ServerAddress.isPlausibleHost('192.168.1.80'), isTrue);
      expect(ServerAddress.isPlausibleHost('255.255.255.255'), isTrue);
      expect(ServerAddress.isPlausibleHost('256.1.1.1'), isFalse);
      expect(ServerAddress.isPlausibleHost('1.2.3'), isTrue, reason: '不是 IPv4 就当主机名');
      expect(ServerAddress.isPlausibleHost(''), isFalse);
      expect(ServerAddress.isPlausibleHost('a b'), isFalse);
      expect(ServerAddress.isPlausibleHost('-bad-'), isFalse);
    });

    test('baseUri / wsUri 拼接', () {
      const address = ServerAddress(host: '192.168.1.80', port: 8810);
      expect(address.baseUri.toString(), 'http://192.168.1.80:8810');
      expect(
        address.wsUri('/ws/asr', {'token': 'abc'}).toString(),
        'ws://192.168.1.80:8810/ws/asr?token=abc',
      );
    });

    test('fromAuthority 往返', () {
      expect(
        ServerAddress.fromAuthority('192.168.1.80:8810'),
        const ServerAddress(host: '192.168.1.80', port: 8810),
      );
      expect(ServerAddress.fromAuthority('192.168.1.80')?.port, 8810);
      expect(ServerAddress.fromAuthority('坏数据::'), isNull);
    });
  });

  group('ServerStore · 最近地址记忆（F9 / U3）', () {
    late LocalStore local;
    late ServerStore store;

    setUp(() async {
      SharedPreferences.setMockInitialValues({});
      local = await LocalStore.open();
      store = ServerStore(local);
    });

    test('全新安装：没有地址', () {
      expect(store.hasServer, isFalse);
      expect(store.current, isNull);
      expect(store.recents, isEmpty);
    });

    test('保存后能读回，且进入 recents', () async {
      await store.save(const ServerAddress(host: '192.168.1.80', port: 8810));
      expect(store.hasServer, isTrue);
      expect(store.current?.authority, '192.168.1.80:8810');
      expect(store.recents.first.authority, '192.168.1.80:8810');
    });

    test('换了网络：新地址排最前，旧地址仍在候选里（U3）', () async {
      await store.save(const ServerAddress(host: '192.168.1.80', port: 8810));
      await store.save(const ServerAddress(host: '192.168.88.19', port: 8810));
      expect(store.current?.host, '192.168.88.19');
      expect(store.recents.map((a) => a.host).toList(),
          ['192.168.88.19', '192.168.1.80']);
    });

    test('重复保存同一地址不会产生重复项', () async {
      const address = ServerAddress(host: '192.168.1.80', port: 8810);
      await store.save(address);
      await store.save(address);
      await store.save(address);
      expect(store.recents.length, 1);
    });

    test('recents 最多留 5 个', () async {
      for (var i = 1; i <= 8; i++) {
        await store.save(ServerAddress(host: '192.168.1.$i', port: 8810));
      }
      expect(store.recents.length, ServerStore.maxRecents);
      expect(store.recents.first.host, '192.168.1.8');
    });

    test('forget 只清当前，保留候选（否则下次启动没得试）', () async {
      await store.save(const ServerAddress(host: '192.168.1.80', port: 8810));
      await store.forget();
      expect(store.hasServer, isFalse);
      expect(store.recents, isNotEmpty);
    });

    test('重建 store（模拟杀进程重开）后地址还在（U7）', () async {
      await store.save(const ServerAddress(host: '192.168.1.80', port: 8810));
      final reopened = ServerStore(local);
      expect(reopened.current?.authority, '192.168.1.80:8810');
    });

    test('脏数据不会让构造失败', () async {
      SharedPreferences.setMockInitialValues({
        'server.host': '999.999.999.999',
        'server.port': 8810,
      });
      final bad = ServerStore(await LocalStore.open());
      expect(bad.hasServer, isFalse, reason: '非法主机名应被丢弃而不是拿去请求');
    });
  });

  group('UDP 应答解析（接口设计 §3.2）', () {
    test('正常应答', () {
      final address = parseDiscoverReply({
        'magic': kHereMagic,
        'v': 1,
        'host': '192.168.1.80',
        'port': 8810,
        'server_name': '背记系统',
      });
      expect(address?.authority, '192.168.1.80:8810');
    });

    test('magic 不对直接忽略（局域网里可能有别的广播）', () {
      expect(parseDiscoverReply({'magic': 'OTHER', 'host': '1.2.3.4', 'port': 1}), isNull);
      expect(parseDiscoverReply('不是对象'), isNull);
      expect(parseDiscoverReply(null), isNull);
    });

    test('服务端报了不可用 host → 用 UDP 源地址兜底', () {
      final address = parseDiscoverReply(
        {'magic': kHereMagic, 'host': '0.0.0.0', 'port': 8810},
        replyFrom: '192.168.88.19',
      );
      expect(address?.host, '192.168.88.19');
    });

    test('host 缺失但源地址有效 → 用源地址', () {
      final address = parseDiscoverReply(
        {'magic': kHereMagic, 'port': 8810},
        replyFrom: '10.0.0.5',
      );
      expect(address?.host, '10.0.0.5');
    });

    test('端口缺失 → 用默认值；端口非法 → 丢弃', () {
      final fallback = parseDiscoverReply(
        {'magic': kHereMagic, 'host': '192.168.1.80'},
      );
      expect(fallback?.port, kDefaultServerPort);
      expect(
        parseDiscoverReply({'magic': kHereMagic, 'host': '192.168.1.80', 'port': 0}),
        isNull,
      );
    });

    test('两边都没有有效 host → null（不要造一个假地址）', () {
      expect(
        parseDiscoverReply({'magic': kHereMagic, 'host': '', 'port': 8810}),
        isNull,
      );
    });

    test('发现端口常量与契约一致', () {
      expect(kDiscoveryPort, 9527);
      expect(kDiscoverMagic, 'BJSYS_DISCOVER');
      expect(kHereMagic, 'BJSYS_HERE');
      expect(kDiscoverTimeout, const Duration(seconds: 2));
      expect(kDiscoverRetries, 2);
    });
  });
}
