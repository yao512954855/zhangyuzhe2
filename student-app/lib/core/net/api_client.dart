/// 统一 HTTP 客户端 —— 30-APP-01 F10（U14 / U15）。
///
/// 契约约定（§3 / §5.3）：**HTTP 状态码恒为 200，业务成败看 `code`**。
/// 所以这里只在一层里完成「解包 + 判 code + 抛中文异常」，
/// 页面拿到的要么是 `data`，要么是一个能直接上屏的 [ApiException]。
///
/// **所有请求都必须走这里**（学生端共通约束第 7 条）：不要在业务代码里 `Dio()`。
library;

import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

import '../storage/local_store.dart';
import '../storage/server_store.dart';
import 'api_exception.dart';

/// 一次请求的可选覆盖项。
class RequestOptionsOverride {
  const RequestOptionsOverride({
    this.base,
    this.skipAuth = false,
    this.timeout,
    this.headers,
  });

  /// 临时指定服务器（服务发现页「测试连接」用，不改全局 [ServerStore]）。
  final ServerAddress? base;

  /// 免鉴权接口（登录、注册、探活）。
  final bool skipAuth;

  /// 覆盖超时。
  final Duration? timeout;

  /// 附加（或覆盖）请求头。
  ///
  /// 加这个字段是为了 `Idempotency-Key`（契约 §5.1 / §9.2）：提交答案这类写操作
  /// 必须带幂等键，弱网重试才不会重复计分。**默认 `null` = 一个字节都不改**，
  /// 既有调用方行为完全不变。
  ///
  /// ⚠️ 只允许放**协议级**的头（幂等键、条件请求等）。鉴权头仍由本类统一管理，
  /// 业务代码不要拿它去绕鉴权 —— `skipAuth` 才是那个开关。
  final Map<String, String>? headers;
}

/// 探活返回（`GET /api/ping`，接口设计 §3.1）。
@immutable
class ServerInfo {
  const ServerInfo({
    required this.service,
    required this.version,
    required this.serverName,
  });

  factory ServerInfo.fromJson(Map<String, dynamic> json) => ServerInfo(
        service: json['service']?.toString() ?? '',
        version: json['version']?.toString() ?? '',
        serverName: json['server_name']?.toString() ?? '背记系统',
      );

  final String service;
  final String version;
  final String serverName;
}

/// 学生端 HTTP 客户端。
class ApiClient {
  ApiClient({
    required this.serverStore,
    required this.localStore,
    Dio? dio,
    this.timeout = const Duration(seconds: 10),
  }) : _dio = dio ??
            Dio(
              BaseOptions(
                connectTimeout: const Duration(seconds: 10),
                receiveTimeout: const Duration(seconds: 10),
                sendTimeout: const Duration(seconds: 10),
                responseType: ResponseType.json,
                // 自己判 code，不让 dio 把 4xx/5xx 直接抛成异常
                validateStatus: (status) => status != null && status < 600,
              ),
            );

  final ServerStore serverStore;
  final LocalStore localStore;
  final Dio _dio;
  final Duration timeout;

  /// 登录态失效时的回调（由 app 层注入：清 token + 跳登录页）。
  ///
  /// **注意仍会照常抛异常**：钩子只负责"顺手清一下状态"，
  /// 页面该处理的失败还是要处理（否则会出现"静默什么都没发生"）。
  void Function()? onUnauthorized;

  // ------------------------------------------------------------------ 动词

  Future<T?> get<T>(String path, {Map<String, dynamic>? query, RequestOptionsOverride? override}) {
    return _send<T>('GET', path, query: query, override: override);
  }

  Future<T?> post<T>(
    String path, {
    Object? body,
    Map<String, dynamic>? query,
    RequestOptionsOverride? override,
  }) {
    return _send<T>('POST', path, body: body, query: query, override: override);
  }

  Future<T?> put<T>(
    String path, {
    Object? body,
    Map<String, dynamic>? query,
    RequestOptionsOverride? override,
  }) {
    return _send<T>('PUT', path, body: body, query: query, override: override);
  }

  Future<T?> delete<T>(
    String path, {
    Object? body,
    Map<String, dynamic>? query,
    RequestOptionsOverride? override,
  }) {
    return _send<T>('DELETE', path, body: body, query: query, override: override);
  }

  /// 取 `data` 里的 `items` 列表（分页接口用，契约 §5.3）。
  Future<List<dynamic>> list(
    String path, {
    Map<String, dynamic>? query,
    RequestOptionsOverride? override,
  }) async {
    final data = await get<Map<String, dynamic>>(path, query: query, override: override);
    final items = data?['items'];
    return items is List ? items : const [];
  }

  // ---------------------------------------------------------------- 探活

  /// 探活。**不抛异常**（返回 `null` 即"这个地址不通"），
  /// 因为它在启动路径上，失败是常态而不是异常。
  Future<ServerInfo?> ping({ServerAddress? base, Duration? timeout}) async {
    try {
      final data = await get<Map<String, dynamic>>(
        '/api/ping',
        override: RequestOptionsOverride(
          base: base,
          skipAuth: true,
          timeout: timeout ?? const Duration(seconds: 3),
        ),
      );
      if (data == null) {
        return null;
      }
      return ServerInfo.fromJson(data);
    } on ApiException {
      return null;
    } catch (_) {
      return null;
    }
  }

  // ---------------------------------------------------------------- 内部

  Future<T?> _send<T>(
    String method,
    String path, {
    Object? body,
    Map<String, dynamic>? query,
    RequestOptionsOverride? override,
    bool allowRetry = true,
  }) async {
    final opts = override ?? const RequestOptionsOverride();
    final base = opts.base ?? serverStore.current;
    if (base == null) {
      throw ApiException.notReady();
    }

    final uri = _buildUri(base, path, query);
    final headers = <String, Object?>{};
    // 附加头**先加**，鉴权头后加 —— 这样顺序保证了调用方无论传什么，
    // 都覆盖不了 `Authorization`（附加头只用于幂等键这类协议头）。
    if (opts.headers != null) {
      headers.addAll(opts.headers!);
    }
    if (!opts.skipAuth) {
      final token = localStore.token;
      if (token != null && token.isNotEmpty) {
        headers['Authorization'] = 'Bearer $token';
      }
    }

    try {
      final response = await _dio.request<dynamic>(
        uri.toString(),
        data: body,
        options: Options(
          method: method,
          headers: headers,
          sendTimeout: opts.timeout ?? timeout,
          receiveTimeout: opts.timeout ?? timeout,
        ),
      );
      final data = _unwrap(response);
      return data as T?;
    } on ApiException catch (e) {
      // 业务码 10002 是从 _unwrap 里抛出来的，**必须在这里统一触发过期钩子**；
      // 早先只在 dio 异常分支里挂钩子，导致"服务端说登录过期了，App 却毫无反应"。
      _notifyIfUnauthorized(e);
      rethrow;
    } on DioException catch (e) {
      final mapped = _mapDio(e);
      // 只对**幂等的 GET** 重试一次：POST 重试可能造成重复注册/重复交卷
      if (allowRetry &&
          method == 'GET' &&
          (mapped.kind == ApiErrorKind.timeout || mapped.kind == ApiErrorKind.network)) {
        await Future<void>.delayed(const Duration(milliseconds: 300));
        return _send<T>(
          method,
          path,
          body: body,
          query: query,
          override: override,
          allowRetry: false,
        );
      }
      _notifyIfUnauthorized(mapped);
      throw mapped;
    } on SocketException catch (e) {
      final mapped = ApiException.network(e);
      _notifyIfUnauthorized(mapped);
      throw mapped;
    } catch (e) {
      throw ApiException.server(e);
    }
  }

  /// 拼查询串。
  ///
  /// ⚠️ `Uri.replace(queryParameters: ...)` 只接受 `String` 或 `Iterable<String>`，
  /// 直接塞 `{'grade': 3}`（int）会在运行时抛
  /// `type 'int' is not a subtype of type 'Iterable<dynamic>'` ——
  /// 而且是**每一次带数字参数的请求都会炸**，所以这里统一转成字符串。
  /// `null` 值直接丢掉（不要发出 `?grade=null`）。
  Uri _buildUri(ServerAddress base, String path, Map<String, dynamic>? query) {
    final params = <String, dynamic>{};
    query?.forEach((key, value) {
      if (value == null) {
        return;
      }
      if (value is Iterable) {
        params[key] = value.map((e) => '$e').toList(growable: false);
      } else {
        params[key] = '$value';
      }
    });
    return base.baseUri.replace(
      path: path,
      queryParameters: params.isEmpty ? null : params,
    );
  }

  /// 解包 `{code, message, data, trace_id}` 并判成败。
  dynamic _unwrap(Response<dynamic> response) {
    final status = response.statusCode ?? 0;
    final payload = response.data;

    if (payload is! Map) {
      // HTTP 层先出问题（网关 502、代理返回 HTML 等）
      if (status == 401) {
        // 钩子由 _send 的 `on ApiException` 统一触发，这里不重复调
        throw ApiException.business(
          ApiException.codeUnauthorized,
          '登录已过期，请重新登录',
        );
      }
      if (status >= 500) {
        throw ApiException.server(payload, status);
      }
      throw ApiException.parse(payload);
    }

    final code = _asInt(payload['code']);
    final message = payload['message']?.toString() ?? '';
    final traceId = payload['trace_id']?.toString();

    if (code != 0) {
      throw ApiException.business(code, message, traceId: traceId);
    }
    return payload['data'];
  }

  int _asInt(Object? value) {
    if (value is int) {
      return value;
    }
    if (value is num) {
      return value.toInt();
    }
    return int.tryParse(value?.toString() ?? '') ?? 0;
  }

  ApiException _mapDio(DioException e) {
    switch (e.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.sendTimeout:
      case DioExceptionType.receiveTimeout:
      case DioExceptionType.transformTimeout:
        return ApiException.timeout(e);
      case DioExceptionType.connectionError:
        return ApiException.network(e);
      case DioExceptionType.badCertificate:
        return ApiException.network(e);
      case DioExceptionType.cancel:
        return ApiException.cancelled();
      case DioExceptionType.badResponse:
        final status = e.response?.statusCode ?? 0;
        if (status == 401) {
          return ApiException.business(
            ApiException.codeUnauthorized,
            '登录已过期，请重新登录',
          );
        }
        if (status >= 500) {
          return ApiException.server(e, status);
        }
        // 4xx 但没走业务码：按解析失败处理，别把英文状态行漏给学生
        return ApiException.server(e, status);
      case DioExceptionType.unknown:
        if (e.error is SocketException) {
          return ApiException.network(e);
        }
        return ApiException.network(e);
    }
  }

  void _notifyIfUnauthorized(ApiException e) {
    if (e.isUnauthorized) {
      onUnauthorized?.call();
    }
  }
}
