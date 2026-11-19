import 'dart:convert';
import 'dart:math';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

class ApiError implements Exception {
  final String message;
  final int status;
  ApiError(this.message, [this.status = 0]);
  @override
  String toString() => message;
}

String newKey() {
  final r = Random.secure();
  return List.generate(16, (_) => r.nextInt(256).toRadixString(16).padLeft(2, '0')).join();
}

class Api {
  static final Api i = Api._();
  Api._();

  String baseUrl = '';
  String? token;
  Map<String, dynamic>? user;
  Map<String, dynamic>? meta;
  void Function()? onSignedOut;

  Future<void> load() async {
    final p = await SharedPreferences.getInstance();
    baseUrl = p.getString('base_url') ?? const String.fromEnvironment('API_URL');
    token = p.getString('token');
  }

  Future<void> setBaseUrl(String url) async {
    baseUrl = url.trim().replaceAll(RegExp(r'/+$'), '');
    if (baseUrl.isNotEmpty && !baseUrl.startsWith('http')) baseUrl = 'http://$baseUrl';
    (await SharedPreferences.getInstance()).setString('base_url', baseUrl);
  }

  Future<void> _saveToken(String? t) async {
    token = t;
    final p = await SharedPreferences.getInstance();
    t == null ? p.remove('token') : p.setString('token', t);
  }

  Future<dynamic> req(String method, String path, {Object? body, String? idem}) async {
    if (baseUrl.isEmpty) throw ApiError('Set the server address first');
    final h = {'Content-Type': 'application/json'};
    if (token != null) h['Authorization'] = 'Bearer $token';
    if (idem != null) h['Idempotency-Key'] = idem;
    final uri = Uri.parse('$baseUrl$path');
    http.Response r;
    try {
      final b = body == null ? null : jsonEncode(body);
      r = await switch (method) {
        'POST' => http.post(uri, headers: h, body: b),
        'DELETE' => http.delete(uri, headers: h),
        _ => http.get(uri, headers: h),
      }
          .timeout(const Duration(seconds: 15));
    } catch (_) {
      throw ApiError("Can't reach the server at $baseUrl");
    }
    dynamic data;
    try {
      data = jsonDecode(utf8.decode(r.bodyBytes));
    } catch (_) {
      data = {};
    }
    if (r.statusCode == 401 && token != null) {
      await signOut();
      throw ApiError('Session expired', 401);
    }
    if (r.statusCode >= 400) {
      final d = data is Map ? data['detail'] : null;
      final msg = d is String ? d : (d is List && d.isNotEmpty ? (d.first['msg'] ?? 'Request failed') : 'Request failed');
      throw ApiError(msg.toString(), r.statusCode);
    }
    return data;
  }

  Future<void> loadMeta() async => meta ??= Map<String, dynamic>.from(await req('GET', '/api/meta'));

  Future<void> auth(bool register, Map<String, dynamic> body) async {
    final r = await req('POST', register ? '/api/auth/register' : '/api/auth/login', body: body);
    await _saveToken(r['token']);
    user = Map<String, dynamic>.from(r['user']);
  }

  Future<void> fetchMe() async => user = Map<String, dynamic>.from(await req('GET', '/api/me'));

  Future<void> signOut() async {
    await _saveToken(null);
    user = null;
    onSignedOut?.call();
  }

  Future<List<Map<String, dynamic>>> wallets() async =>
      List<Map<String, dynamic>>.from((await req('GET', '/api/wallets') as List).map((e) => Map<String, dynamic>.from(e)));

  Future<List<Map<String, dynamic>>> txns({int limit = 50, String? currency}) async {
    final q = '/api/transactions?limit=$limit${currency != null ? '&currency=$currency' : ''}';
    return List<Map<String, dynamic>>.from((await req('GET', q) as List).map((e) => Map<String, dynamic>.from(e)));
  }

  Future<Map<String, dynamic>> txn(String ref) async => Map<String, dynamic>.from(await req('GET', '/api/transactions/$ref'));
}

// ---------- formatting helpers ----------
String sym(String cur) => Api.i.meta?['currencies']?[cur]?['symbol'] ?? cur;
int decimals(String cur) => Api.i.meta?['currencies']?[cur]?['decimals'] ?? 2;

String fmt(dynamic amount, String cur) {
  final d = decimals(cur);
  final v = double.tryParse(amount.toString()) ?? 0;
  final parts = v.toStringAsFixed(d).split('.');
  final whole = parts[0].replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ',');
  return parts.length > 1 ? '$whole.${parts[1]}' : whole;
}

String money(dynamic amount, String cur) => '${sym(cur)} ${fmt(amount, cur)}';

String when(String iso) {
  final d = DateTime.parse(iso).toLocal();
  const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  final h = d.hour % 12 == 0 ? 12 : d.hour % 12;
  return '${m[d.month - 1]} ${d.day} · $h:${d.minute.toString().padLeft(2, '0')} ${d.hour < 12 ? 'AM' : 'PM'}';
}
