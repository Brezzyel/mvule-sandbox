import 'package:flutter/material.dart';

import 'api.dart';

class C {
  static const bg = Color(0xFFF6F7F4);
  static const card = Colors.white;
  static const ink = Color(0xFF0F1A14);
  static const ink2 = Color(0xFF4B5A51);
  static const ink3 = Color(0xFF8A968E);
  static const line = Color(0xFFE4E8E2);
  static const brand = Color(0xFF1F6B45);
  static const brandSoft = Color(0xFFE5F2EA);
  static const warn = Color(0xFFB7791F);
  static const warnSoft = Color(0xFFFCF3E2);
  static const bad = Color(0xFFB42318);
  static const badSoft = Color(0xFFFDECEA);
}

const mono = TextStyle(fontFamily: 'monospace');

ThemeData buildTheme() {
  final base = ThemeData(useMaterial3: true, colorSchemeSeed: C.brand, scaffoldBackgroundColor: C.bg);
  OutlineInputBorder b(Color c) => OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: c));
  return base.copyWith(
    appBarTheme: const AppBarTheme(backgroundColor: C.bg, surfaceTintColor: Colors.transparent, foregroundColor: C.ink, elevation: 0),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: C.card,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
      border: b(C.line),
      enabledBorder: b(C.line),
      focusedBorder: b(C.brand),
      labelStyle: const TextStyle(color: C.ink2),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: C.brand,
        minimumSize: const Size.fromHeight(52),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: C.ink,
        minimumSize: const Size.fromHeight(52),
        side: const BorderSide(color: C.line),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
    ),
  );
}

/// The always-visible sandbox banner.
class SandboxBanner extends StatelessWidget {
  const SandboxBanner({super.key});
  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        color: C.warnSoft,
        padding: EdgeInsets.only(top: MediaQuery.of(context).padding.top + 6, bottom: 6),
        child: const Text('SANDBOX · TEST FUNDS ONLY · NO REAL MONEY',
            textAlign: TextAlign.center, style: TextStyle(fontFamily: 'monospace', fontSize: 10.5, letterSpacing: 1, color: C.warn, fontWeight: FontWeight.w600)),
      );
}

/// Page scaffold with banner on top.
class SbxPage extends StatelessWidget {
  final String? title;
  final Widget child;
  final bool back;
  final Future<void> Function()? onRefresh;
  const SbxPage({super.key, this.title, required this.child, this.back = false, this.onRefresh});

  @override
  Widget build(BuildContext context) {
    Widget body = ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(18, 8, 18, 32),
      children: [
        if (back)
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
                onPressed: () => Navigator.pop(context),
                style: TextButton.styleFrom(foregroundColor: C.ink2, padding: EdgeInsets.zero),
                icon: const Icon(Icons.arrow_back, size: 18),
                label: const Text('Back')),
          ),
        if (title != null) Padding(padding: const EdgeInsets.only(top: 8, bottom: 4), child: Text(title!, style: h1)),
        child,
      ],
    );
    if (onRefresh != null) body = RefreshIndicator(onRefresh: onRefresh!, color: C.brand, child: body);
    return Scaffold(body: Column(children: [const SandboxBanner(), Expanded(child: body)]));
  }
}

const h1 = TextStyle(fontSize: 24, fontWeight: FontWeight.w700, letterSpacing: -0.5, color: C.ink);
const muted = TextStyle(color: C.ink3);

Widget sectionTitle(String t) => Padding(
      padding: const EdgeInsets.only(top: 24, bottom: 10),
      child: Text(t.toUpperCase(), style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, letterSpacing: 1, color: C.ink2)),
    );

class CardBox extends StatelessWidget {
  final Widget child;
  final EdgeInsets padding;
  const CardBox({super.key, required this.child, this.padding = const EdgeInsets.all(16)});
  @override
  Widget build(BuildContext context) => Container(
        padding: padding,
        decoration: BoxDecoration(color: C.card, borderRadius: BorderRadius.circular(16), border: Border.all(color: C.line)),
        child: child,
      );
}

class StatusPill extends StatelessWidget {
  final String status;
  const StatusPill(this.status, {super.key});
  @override
  Widget build(BuildContext context) {
    final (bg, fg) = switch (status) {
      'pending' => (C.warnSoft, C.warn),
      'failed' => (C.badSoft, C.bad),
      _ => (C.brandSoft, C.brand),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(99)),
      child: Text(status.toUpperCase(), style: TextStyle(fontFamily: 'monospace', fontSize: 10, color: fg, fontWeight: FontWeight.w600, letterSpacing: .5)),
    );
  }
}

class Kv extends StatelessWidget {
  final String k;
  final String v;
  final bool isMono;
  const Kv(this.k, this.v, {super.key, this.isMono = false});
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(vertical: 11),
        decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: C.line))),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(k, style: muted),
          const SizedBox(width: 16),
          Expanded(child: Text(v, textAlign: TextAlign.right, style: isMono ? mono : null)),
        ]),
      );
}

class TxRow extends StatelessWidget {
  final Map<String, dynamic> t;
  final VoidCallback onTap;
  const TxRow(this.t, {super.key, required this.onTap});
  @override
  Widget build(BuildContext context) {
    final inbound = t['direction'] == 'in';
    final icon = switch (t['type']) {
      'topup' => Icons.add,
      'payout' => Icons.logout,
      _ => inbound ? Icons.south_west : Icons.north_east,
    };
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Row(children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(color: inbound ? C.brandSoft : const Color(0xFFEEF1EC), borderRadius: BorderRadius.circular(12)),
            child: Icon(icon, size: 18, color: inbound ? C.brand : C.ink2),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(t['title'] ?? '', maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w500)),
              const SizedBox(height: 3),
              Row(children: [
                Text(when(t['created_at']), style: const TextStyle(fontSize: 12, color: C.ink3)),
                if (t['status'] != 'completed') ...[const SizedBox(width: 6), StatusPill(t['status'])],
              ]),
            ]),
          ),
          Text('${inbound ? '+' : '−'}${money(t['amount'], t['currency'])}',
              style: TextStyle(fontWeight: FontWeight.w600, color: inbound ? C.brand : C.ink)),
        ]),
      ),
    );
  }
}

void toast(BuildContext context, String msg, {bool error = false}) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(
      content: Text(msg),
      behavior: SnackBarBehavior.floating,
      backgroundColor: error ? C.bad : C.ink,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
    ));
}

Widget fieldLabel(String t) => Padding(padding: const EdgeInsets.only(top: 16, bottom: 6), child: Text(t, style: const TextStyle(fontWeight: FontWeight.w500, color: C.ink2, fontSize: 13)));

const amountStyle = TextStyle(fontSize: 28, fontWeight: FontWeight.w600, letterSpacing: -0.5);
