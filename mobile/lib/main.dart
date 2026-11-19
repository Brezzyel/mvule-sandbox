import 'dart:async';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'screens.dart';
import 'ui.dart';

final navKey = GlobalKey<NavigatorState>();
final selectedCur = ValueNotifier<String>('UGX');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Api.i.load();
  selectedCur.value = (await SharedPreferences.getInstance()).getString('cur') ?? 'UGX';
  selectedCur.addListener(() async => (await SharedPreferences.getInstance()).setString('cur', selectedCur.value));
  Api.i.onSignedOut = () => navKey.currentState?.pushAndRemoveUntil(MaterialPageRoute(builder: (_) => const AuthScreen()), (_) => false);
  runApp(const MvuleApp());
}

class MvuleApp extends StatelessWidget {
  const MvuleApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Mvule Sandbox',
        debugShowCheckedModeBanner: false,
        navigatorKey: navKey,
        theme: buildTheme(),
        home: const Boot(),
      );
}

class Boot extends StatefulWidget {
  const Boot({super.key});
  @override
  State<Boot> createState() => _BootState();
}

class _BootState extends State<Boot> {
  @override
  void initState() {
    super.initState();
    _go();
  }

  Future<void> _go() async {
    Widget next = const AuthScreen();
    if (Api.i.token != null && Api.i.baseUrl.isNotEmpty) {
      try {
        await Api.i.loadMeta();
        await Api.i.fetchMe();
        next = const Shell();
      } catch (_) {}
    }
    if (mounted) Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => next));
  }

  @override
  Widget build(BuildContext context) => const Scaffold(body: Center(child: CircularProgressIndicator(color: C.brand)));
}

// ------------------------------------------------------------------ auth
class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key});
  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  bool register = false, busy = false;
  final server = TextEditingController(text: Api.i.baseUrl);
  final name = TextEditingController(), email = TextEditingController(), phone = TextEditingController(), pw = TextEditingController();

  Future<void> submit() async {
    setState(() => busy = true);
    try {
      await Api.i.setBaseUrl(server.text);
      Api.i.meta = null;
      await Api.i.loadMeta();
      await Api.i.auth(register, {
        if (register) 'full_name': name.text.trim(),
        'email': email.text.trim(),
        if (register && phone.text.trim().isNotEmpty) 'phone': phone.text.trim(),
        'password': pw.text,
      });
      if (mounted) Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => const Shell()));
    } catch (e) {
      if (mounted) toast(context, e.toString(), error: true);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => SbxPage(
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          const SizedBox(height: 32),
          const Logo(size: 22),
          const SizedBox(height: 28),
          Text(register ? 'Create a sandbox account' : 'Welcome back', style: h1),
          const SizedBox(height: 4),
          Text(register ? 'Wallets open at 0. Add test funds any time.' : 'Sign in to your sandbox wallet.', style: muted),
          fieldLabel('Server address'),
          TextField(controller: server, keyboardType: TextInputType.url, decoration: const InputDecoration(hintText: 'e.g. 192.168.1.20:8000 or https://…')),
          if (register) ...[fieldLabel('Full name'), TextField(controller: name, textCapitalization: TextCapitalization.words)],
          fieldLabel('Email'),
          TextField(controller: email, keyboardType: TextInputType.emailAddress, autocorrect: false),
          if (register) ...[
            fieldLabel('Phone (optional)'),
            TextField(controller: phone, keyboardType: TextInputType.phone, decoration: const InputDecoration(hintText: '+256…')),
          ],
          fieldLabel('Password'),
          TextField(controller: pw, obscureText: true, decoration: InputDecoration(hintText: register ? 'At least 8 characters' : null)),
          const SizedBox(height: 22),
          FilledButton(onPressed: busy ? null : submit, child: busy ? const _Spin() : Text(register ? 'Create account' : 'Sign in')),
          const SizedBox(height: 10),
          OutlinedButton(onPressed: () => setState(() => register = !register), child: Text(register ? 'I already have an account' : 'Create an account')),
          const SizedBox(height: 16),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(color: C.warnSoft, borderRadius: BorderRadius.circular(12)),
            child: const Text('This is a sandbox. Balances, banks and payout rails are simulated. Nothing here can send or receive real money.',
                style: TextStyle(color: Color(0xFF7A5214), fontSize: 13, height: 1.4)),
          ),
        ]),
      );
}

class _Spin extends StatelessWidget {
  const _Spin();
  @override
  Widget build(BuildContext context) => const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white));
}

class Logo extends StatelessWidget {
  final double size;
  const Logo({super.key, this.size = 18});
  @override
  Widget build(BuildContext context) => Row(mainAxisSize: MainAxisSize.min, children: [
        Container(
          width: size * 1.6,
          height: size * 1.6,
          decoration: BoxDecoration(color: C.brand, borderRadius: BorderRadius.circular(9)),
          alignment: Alignment.center,
          child: Text('M', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w700, fontSize: size * .8)),
        ),
        const SizedBox(width: 10),
        Text('Mvule Pay', style: TextStyle(fontSize: size, fontWeight: FontWeight.w700, letterSpacing: -.4)),
      ]);
}

// ------------------------------------------------------------------ shell
class Shell extends StatefulWidget {
  const Shell({super.key});
  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  int tab = 0;
  final homeKey = GlobalKey<HomeScreenState>();

  void setTab(int i) => setState(() => tab = i);

  @override
  Widget build(BuildContext context) {
    final pages = [
      HomeScreen(key: homeKey, onTab: setTab),
      SendScreen(key: ValueKey('send$tab'), embedded: true, onDone: () => setTab(0)),
      ActivityScreen(key: ValueKey('act$tab')),
      const SettingsScreen(),
    ];
    return Scaffold(
      body: IndexedStack(index: tab, children: pages),
      bottomNavigationBar: NavigationBar(
        selectedIndex: tab,
        backgroundColor: Colors.white,
        indicatorColor: C.brandSoft,
        onDestinationSelected: (i) {
          setTab(i);
          if (i == 0) homeKey.currentState?.refresh();
        },
        destinations: const [
          NavigationDestination(icon: Icon(Icons.home_outlined), selectedIcon: Icon(Icons.home, color: C.brand), label: 'Home'),
          NavigationDestination(icon: Icon(Icons.north_east), label: 'Send'),
          NavigationDestination(icon: Icon(Icons.receipt_long_outlined), label: 'Activity'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), label: 'Settings'),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------------- home
class HomeScreen extends StatefulWidget {
  final void Function(int) onTab;
  const HomeScreen({super.key, required this.onTab});
  @override
  State<HomeScreen> createState() => HomeScreenState();
}

class HomeScreenState extends State<HomeScreen> {
  List<Map<String, dynamic>> wallets = [], txs = [];
  bool loading = true;
  Timer? poll;

  @override
  void initState() {
    super.initState();
    refresh();
    selectedCur.addListener(_cur);
  }

  void _cur() => mounted ? setState(() {}) : null;

  @override
  void dispose() {
    poll?.cancel();
    selectedCur.removeListener(_cur);
    super.dispose();
  }

  Future<void> refresh() async {
    try {
      final w = await Api.i.wallets();
      final t = await Api.i.txns(limit: 6);
      if (!mounted) return;
      setState(() {
        wallets = w;
        txs = t;
        loading = false;
      });
      poll?.cancel();
      if (t.any((x) => x['status'] == 'pending')) poll = Timer(const Duration(seconds: 3), refresh);
    } catch (e) {
      if (mounted) toast(context, e.toString(), error: true);
    }
  }

  Future<void> open(Widget w) async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => w));
    refresh();
  }

  @override
  Widget build(BuildContext context) {
    if (loading) return const SbxPage(child: Padding(padding: EdgeInsets.only(top: 120), child: Center(child: CircularProgressIndicator(color: C.brand))));
    final w = wallets.firstWhere((x) => x['currency'] == selectedCur.value, orElse: () => wallets.first);
    final cur = w['currency'] as String;
    final u = Api.i.user!;
    final initials = (u['full_name'] as String).split(' ').where((p) => p.isNotEmpty).take(2).map((p) => p[0]).join();
    return SbxPage(
      onRefresh: refresh,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const SizedBox(height: 8),
        Row(children: [
          const Logo(),
          const Spacer(),
          GestureDetector(
            onTap: () => widget.onTab(3),
            child: CircleAvatar(backgroundColor: C.brandSoft, child: Text(initials, style: const TextStyle(color: C.brand, fontWeight: FontWeight.w600, fontSize: 13))),
          ),
        ]),
        const SizedBox(height: 18),
        Text('Hi ${(u['full_name'] as String).split(' ').first}', style: muted),
        const SizedBox(height: 10),
        Container(
          padding: const EdgeInsets.all(22),
          decoration: BoxDecoration(color: C.ink, borderRadius: BorderRadius.circular(16)),
          child: Stack(children: [
            Positioned(
              right: -6,
              bottom: 0,
              child: Text('SANDBOX', style: TextStyle(fontFamily: 'monospace', fontSize: 40, fontWeight: FontWeight.w700, color: Colors.white.withValues(alpha: .05))),
            ),
            Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Text('$cur balance', style: const TextStyle(color: Color(0xFFA9B8AE), fontSize: 12)),
                const Spacer(),
                if ((double.tryParse(w['pending_out'].toString()) ?? 0) > 0)
                  Text('${money(w['pending_out'], cur)} pending', style: const TextStyle(color: Color(0xFFA9B8AE), fontSize: 12)),
              ]),
              const SizedBox(height: 10),
              Text.rich(TextSpan(children: [
                TextSpan(text: '${sym(cur)} ', style: const TextStyle(fontSize: 18, color: Color(0xFFA9B8AE))),
                TextSpan(text: fmt(w['balance'], cur), style: const TextStyle(fontSize: 34, fontWeight: FontWeight.w600, color: Colors.white, letterSpacing: -1)),
              ])),
              const SizedBox(height: 4),
              Text('Acct ${w['account_number']}', style: const TextStyle(fontFamily: 'monospace', fontSize: 12, color: Color(0xFFA9B8AE))),
              const SizedBox(height: 16),
              Wrap(
                spacing: 6,
                children: wallets.map((x) {
                  final on = x['currency'] == cur;
                  return GestureDetector(
                    onTap: () => selectedCur.value = x['currency'],
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                      decoration: BoxDecoration(color: on ? Colors.white : Colors.white.withValues(alpha: .08), borderRadius: BorderRadius.circular(99)),
                      child: Text(x['currency'], style: TextStyle(fontFamily: 'monospace', fontSize: 12, color: on ? C.ink : const Color(0xFFCFD8D2))),
                    ),
                  );
                }).toList(),
              ),
            ]),
          ]),
        ),
        const SizedBox(height: 14),
        Row(children: [
          _action(Icons.add, 'Test funds', () => open(const TopupScreen())),
          const SizedBox(width: 10),
          _action(Icons.north_east, 'Send', () => open(const SendScreen())),
          const SizedBox(width: 10),
          _action(Icons.logout, 'Pay out', () => open(const PayoutScreen())),
        ]),
        sectionTitle('Recent activity'),
        CardBox(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
          child: txs.isEmpty
              ? const Padding(padding: EdgeInsets.symmetric(vertical: 28), child: Text('No transactions yet.\nAdd test funds to get started.', textAlign: TextAlign.center, style: muted))
              : Column(children: txs.map((t) => TxRow(t, onTap: () => open(ReceiptScreen(t['ref'])))).toList()),
        ),
      ]),
    );
  }

  Widget _action(IconData i, String label, VoidCallback onTap) => Expanded(
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(14),
          child: Ink(
            padding: const EdgeInsets.symmetric(vertical: 14),
            decoration: BoxDecoration(color: C.card, borderRadius: BorderRadius.circular(14), border: Border.all(color: C.line)),
            child: Column(children: [
              Container(
                  width: 38,
                  height: 38,
                  decoration: BoxDecoration(color: C.brandSoft, borderRadius: BorderRadius.circular(11)),
                  child: Icon(i, color: C.brand, size: 19)),
              const SizedBox(height: 8),
              Text(label, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w500)),
            ]),
          ),
        ),
      );
}
