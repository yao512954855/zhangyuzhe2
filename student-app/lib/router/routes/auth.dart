/// 登录 / 注册路由 —— WP-APP-01 所有（F14 / F15）。
library;

import 'package:flutter/material.dart';

import '../../features/auth/login_page.dart';
import '../../features/auth/register_page.dart';

/// P2 登录 / 注册。
abstract final class AuthRoutes {
  static const login = '/login';
  static const register = '/register';

  static Map<String, WidgetBuilder> get routes => {
        login: (_) => const LoginPage(),
        register: (_) => const RegisterPage(),
      };
}
