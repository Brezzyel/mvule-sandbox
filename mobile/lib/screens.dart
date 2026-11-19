import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'api.dart';
import 'main.dart';
import 'ui.dart';

/// Wallet dropdown shared by money screens.
class WalletPicker extends StatelessWidget {
  final List<Map<String, dynamic>> wallets;
  final VoidCallback? onChanged;
  const WalletPicker(this.wallets, {super.key, this.onChanged});
  @override
  Widget build(BuildContext context) => ValueListenableBuilder(
        valueListenable: selectedCur,
        builder: (_, cur, __) => DropdownButtonFormField<String>(
          initialValue: wallets.any((w) => w['currency'] == cur) ? cur : null,
          isExpanded: true,
          items: wallets
              .map((w) => DropdownMenuItem(
                  value: w['currency'] as String, child: Text('${w['currency']} · ${money(w['balance'], w['currency'])} available')))
              .toList(),
          onChanged: (v) {
            selectedCur.value = v!;
            onChanged?.call();
          },
        ),
      );
}

final _amountFormatter = FilteringTextInputFormatter.allow(RegExp(r'[0-9.,]'));

mixin WalletsLoader<T extends StatefulWidget> on State<T> {
  List<Map<String, dynamic>>? wallets;
  Future<void> loadWallets() async {
    try {
      final w = await Api.i.wallets();
      if (mounted) setState(() => wallets = w);
    } catch (e) {
      if (mounted) toast(context, e.toString(), error: true);
    }
  }
}

Widget loadingBody() => const Padding(padding: EdgeInsets.only(top: 80), child: Center(child: CircularProgressIndicator(color: C.brand)));

// ------------------------------------------------------------------ top-up
class TopupScreen extends StatefulWidget {
  const TopupScreen({super.key});
  @override
  State<TopupScreen> createState() => _TopupScreenState();
}

class _TopupScreenState extends State<TopupScreen> with WalletsLoader {
  final amt = TextEditingController();
  final key = newKey();
  bool busy = false;

  @override
  void initState() {
    super.initState();
    loadWallets();
  }

  Future<void> submit() async {
    setState(() => busy = true);
    try {
      final t = await Api.i.req('POST', '/api/topups', body: {'currency': selectedCur.value, 'amount': amt.text}, idem: key);
      if (!mounted) return;
      toast(context, 'Test funds added');
      Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => ReceiptScreen(t['ref'])));
    } catch (e) {
      if (mounted) toast(context, e.toString(), error: true);
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cur = selectedCur.value;
    final max = (Api.i.meta?['currencies']?[cur]?['topup_max'] ?? 1000) as num;
    return SbxPage(
      back: true,
      title: 'Add test funds',
      child: wallets == null
          ? loadingBody()
          : Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Text('Sandbox money for trying the app. Up to ${money(max, cur)} per top-up.', style: muted),
              fieldLabel('Wallet'),
              WalletPicker(wallets!, onChanged: () => setState(() {})),
              fieldLabel('Amount'),
              TextField(controller: amt, style: amountStyle, keyboardType: const TextInputType.numberWithOptions(decimal: true), inputFormatters: [_amountFormatter], decoration: const InputDecoration(hintText: '0')),
              const SizedBox(height: 10),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [0.05, 0.1, 0.5, 1]
                    .map((f) => (max * f).round())
                    .map((v) => ActionChip(label: Text(fmt(v, cur), style: mono), onPressed: () => amt.text = '$v', backgroundColor: C.card, side: const BorderSide(color: C.line)))
                    .toList(),
              ),
              const SizedBox(height: 22),
              FilledButton(onPressed: busy ? null : submit, child: const Text('Add funds')),
            ]),
    );
  }
}

// -------------------------------------------------------------------- send
class SendScreen extends StatefulWidget {
  final bool embedded;
  final VoidCallback? onDone;
  const SendScreen({super.key, this.embedded = false, this.onDone});
  @override
  State<SendScreen> createState() => _SendScreenState();
}

class _SendScreenState extends State<SendScreen> with WalletsLoader {
  final rc = TextEditingController(), amt = TextEditingController(), memo = TextEditingController();
  String? found, lookupErr;
  bool looking = false, save = false;
  List<Map<String, dynamic>> payees = [];
  Timer? debounce;

  @override
  void initState() {
    super.initState();
    loadWallets();
    Api.i.req('GET', '/api/payees').then((p) {
      if (mounted) setState(() => payees = List<Map<String, dynamic>>.from((p as List).map((e) => Map<String, dynamic>.from(e))).where((x) => x['kind'] == 'internal').toList());
    }).catchError((_) {});
  }

  void onRecipient(String q) {
    debounce?.cancel();
    setState(() {
      found = null;
      lookupErr = null;
      looking = q.trim().length >= 4;
    });
    if (q.trim().length < 4) return;
    debounce = Timer(const Duration(milliseconds: 350), () async {
      try {
        final r = await Api.i.req('GET', '/api/recipients/lookup?q=${Uri.encodeQueryComponent(q.trim())}');
        if (mounted) setState(() => found = r['name']);
      } catch (e) {
        if (mounted) setState(() => lookupErr = e.toString());
      } finally {
        if (mounted) setState(() => looking = false);
      }
    });
  }

  Future<void> review() async {
    if ((double.tryParse(amt.text.replaceAll(',', '')) ?? 0) <= 0) return toast(context, 'Enter an amount', error: true);
    final body = {'currency': selectedCur.value, 'amount': amt.text, 'recipient': rc.text.trim(), 'memo': memo.text.isEmpty ? null : memo.text};
    final ok = await Navigator.push<bool>(context, MaterialPageRoute(builder: (_) => ConfirmSendScreen(body: body, name: found!, save: save)));
    if (ok == true) {
      rc.clear();
      amt.clear();
      memo.clear();
      setState(() => found = null);
      loadWallets();
      if (widget.embedded) {
        widget.onDone?.call();
      } else if (mounted) {
        Navigator.pop(context);
      }
    }
  }

  @override
  Widget build(BuildContext context) => SbxPage(
        back: !widget.embedded,
        title: 'Send money',
        onRefresh: loadWallets,
        child: wallets == null
            ? loadingBody()
            : Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Text('Instant transfer to another Mvule sandbox user.', style: muted),
                fieldLabel('Wallet'),
                WalletPicker(wallets!),
                fieldLabel('Recipient'),
                TextField(controller: rc, onChanged: onRecipient, autocorrect: false, decoration: const InputDecoration(hintText: 'Email, phone or account number')),
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: looking
                      ? const Align(alignment: Alignment.centerLeft, child: SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: C.brand)))
                      : Text(found != null ? '✓ $found' : (lookupErr ?? ''), style: TextStyle(fontSize: 12, color: found != null ? C.brand : C.bad)),
                ),
                if (payees.isNotEmpty)
                  Wrap(
                    spacing: 8,
                    children: payees
                        .map((p) => ActionChip(
                            label: Text(p['label']),
                            backgroundColor: C.card,
                            side: const BorderSide(color: C.line),
                            onPressed: () {
                              rc.text = p['details']['recipient'];
                              onRecipient(rc.text);
                            }))
                        .toList(),
                  ),
                fieldLabel('Amount'),
                TextField(controller: amt, style: amountStyle, keyboardType: const TextInputType.numberWithOptions(decimal: true), inputFormatters: [_amountFormatter], decoration: const InputDecoration(hintText: '0')),
                fieldLabel('Note (optional)'),
                TextField(controller: memo, maxLength: 140, decoration: const InputDecoration(counterText: '')),
                CheckboxListTile(
                  value: save,
                  onChanged: (v) => setState(() => save = v!),
                  title: const Text('Save recipient'),
                  contentPadding: EdgeInsets.zero,
                  controlAffinity: ListTileControlAffinity.leading,
                  activeColor: C.brand,
                ),
                const SizedBox(height: 8),
                FilledButton(onPressed: found == null ? null : review, child: const Text('Review')),
              ]),
      );
}

class ConfirmSendScreen extends StatefulWidget {
  final Map<String, dynamic> body;
  final String name;
  final bool save;
  const ConfirmSendScreen({super.key, required this.body, required this.name, required this.save});
  @override
  State<ConfirmSendScreen> createState() => _ConfirmSendScreenState();
}

class _ConfirmSendScreenState extends State<ConfirmSendScreen> {
  final key = newKey();
  bool busy = false;

  Future<void> send() async {
    setState(() => busy = true);
    try {
      final t = await Api.i.req('POST', '/api/transfers', body: widget.body, idem: key);
      if (widget.save) {
        await Api.i.req('POST', '/api/payees', body: {'label': widget.name, 'kind': 'internal', 'details': {'recipient': widget.body['recipient']}}).catchError((_) => null);
      }
      if (!mounted) return;
      toast(context, 'Sent');
      await Navigator.push(context, MaterialPageRoute(builder: (_) => ReceiptScreen(t['ref'])));
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        toast(context, e.toString(), error: true);
        setState(() => busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final b = widget.body, cur = b['currency'] as String;
    return SbxPage(
      back: true,
      title: 'Confirm transfer',
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const SizedBox(height: 14),
        CardBox(
          child: Column(children: [
            Text(money(b['amount'], cur), style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w600, letterSpacing: -1)),
            const SizedBox(height: 8),
            Kv('To', widget.name),
            Kv('Recipient ID', b['recipient'], isMono: true),
            Kv('Fee', money(0, cur)),
            if (b['memo'] != null) Kv('Note', b['memo']),
          ]),
        ),
        const SizedBox(height: 22),
        FilledButton(onPressed: busy ? null : send, child: Text('Send ${money(b['amount'], cur)}')),
      ]),
    );
  }
}

// ------------------------------------------------------------------ payout
class PayoutScreen extends StatefulWidget {
  const PayoutScreen({super.key});
  @override
  State<PayoutScreen> createState() => _PayoutScreenState();
}

class _PayoutScreenState extends State<PayoutScreen> with WalletsLoader {
  String rail = 'mockmomo';
  String? bank;
  final name = TextEditingController(text: Api.i.user?['full_name'] ?? ''), acct = TextEditingController(), amt = TextEditingController();
  final key = newKey();
  bool busy = false;

  @override
  void initState() {
    super.initState();
    loadWallets();
  }

  Map<String, dynamic> get rails => Map<String, dynamic>.from(Api.i.meta!['rails']);

  Future<void> submit() async {
    setState(() => busy = true);
    try {
      final t = await Api.i.req('POST', '/api/payouts',
          body: {'currency': selectedCur.value, 'amount': amt.text, 'rail': rail, 'bank_code': bank, 'account_name': name.text, 'account_number': acct.text},
          idem: key);
      if (!mounted) return;
      toast(context, 'Payout submitted');
      Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => ReceiptScreen(t['ref'])));
    } catch (e) {
      if (mounted) {
        toast(context, e.toString(), error: true);
        setState(() => busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = rails[rail];
    final banks = List<Map<String, dynamic>>.from((r['banks'] as List).map((e) => Map<String, dynamic>.from(e)));
    if (r['needs_bank'] == true && bank == null && banks.isNotEmpty) bank = banks.first['code'];
    return SbxPage(
      back: true,
      title: 'Pay out',
      child: wallets == null
          ? loadingBody()
          : Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Text('Simulated withdrawal. Settles in about ${r['settle_seconds']}s.', style: muted),
              const SizedBox(height: 16),
              SegmentedButton<String>(
                segments: rails.entries.map((e) => ButtonSegment(value: e.key, label: Text(e.value['name'], style: const TextStyle(fontSize: 13)))).toList(),
                selected: {rail},
                showSelectedIcon: false,
                onSelectionChanged: (s) => setState(() {
                  rail = s.first;
                  bank = null;
                }),
              ),
              fieldLabel('Wallet'),
              WalletPicker(wallets!),
              if (r['needs_bank'] == true) ...[
                fieldLabel('Sandbox bank'),
                DropdownButtonFormField<String>(
                  initialValue: bank,
                  items: banks.map((b) => DropdownMenuItem(value: b['code'] as String, child: Text(b['name']))).toList(),
                  onChanged: (v) => setState(() => bank = v),
                ),
              ],
              fieldLabel('Account name'),
              TextField(controller: name),
              fieldLabel(r['needs_bank'] == true ? 'Account number' : 'Mobile number'),
              TextField(controller: acct, keyboardType: TextInputType.number),
              fieldLabel('Amount'),
              TextField(controller: amt, style: amountStyle, keyboardType: const TextInputType.numberWithOptions(decimal: true), inputFormatters: [_amountFormatter], decoration: const InputDecoration(hintText: '0')),
              const SizedBox(height: 6),
              const Text('Tip: amounts ending in .13 (UGX: 13) are rejected by the sandbox rail so you can test refunds.', style: TextStyle(fontSize: 12, color: C.ink3)),
              const SizedBox(height: 22),
              FilledButton(onPressed: busy ? null : submit, child: const Text('Pay out')),
            ]),
    );
  }
}

// ---------------------------------------------------------------- activity
class ActivityScreen extends StatefulWidget {
  const ActivityScreen({super.key});
  @override
  State<ActivityScreen> createState() => _ActivityScreenState();
}

class _ActivityScreenState extends State<ActivityScreen> {
  String? filter;
  List<Map<String, dynamic>>? txs;
  Timer? poll;

  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  void dispose() {
    poll?.cancel();
    super.dispose();
  }

  Future<void> load() async {
    try {
      final t = await Api.i.txns(limit: 100, currency: filter);
      if (!mounted) return;
      setState(() => txs = t);
      poll?.cancel();
      if (t.any((x) => x['status'] == 'pending')) poll = Timer(const Duration(seconds: 3), load);
    } catch (e) {
      if (mounted) toast(context, e.toString(), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final curs = (Api.i.meta?['currencies'] as Map?)?.keys.cast<String>().toList() ?? [];
    return SbxPage(
      title: 'Activity',
      onRefresh: load,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          children: [null, ...curs]
              .map((c) => ChoiceChip(
                    label: Text(c ?? 'All', style: mono),
                    selected: filter == c,
                    selectedColor: C.ink,
                    labelStyle: TextStyle(color: filter == c ? Colors.white : C.ink),
                    showCheckmark: false,
                    onSelected: (_) {
                      setState(() => filter = c);
                      load();
                    },
                  ))
              .toList(),
        ),
        const SizedBox(height: 14),
        txs == null
            ? loadingBody()
            : CardBox(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                child: txs!.isEmpty
                    ? const Padding(padding: EdgeInsets.symmetric(vertical: 28), child: Center(child: Text('Nothing here yet.', style: muted)))
                    : Column(
                        children: txs!
                            .map((t) => TxRow(t, onTap: () async {
                                  await Navigator.push(context, MaterialPageRoute(builder: (_) => ReceiptScreen(t['ref'])));
                                  load();
                                }))
                            .toList()),
              ),
      ]),
    );
  }
}

// ----------------------------------------------------------------- receipt
class ReceiptScreen extends StatefulWidget {
  final String ref;
  const ReceiptScreen(this.ref, {super.key});
  @override
  State<ReceiptScreen> createState() => _ReceiptScreenState();
}

class _ReceiptScreenState extends State<ReceiptScreen> {
  Map<String, dynamic>? t;
  Timer? poll;

  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  void dispose() {
    poll?.cancel();
    super.dispose();
  }

  Future<void> load() async {
    try {
      final r = await Api.i.txn(widget.ref);
      if (!mounted) return;
      setState(() => t = r);
      if (r['status'] == 'pending') poll = Timer(const Duration(milliseconds: 2500), load);
    } catch (e) {
      if (mounted) toast(context, e.toString(), error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (t == null) return SbxPage(back: true, child: loadingBody());
    final x = t!, cur = x['currency'] as String, inbound = x['direction'] == 'in';
    final cp = Map<String, dynamic>.from(x['counterparty'] ?? {});
    final railName = switch (x['rail']) {
      'internal' => 'Mvule instant transfer',
      'sandbox' => 'Sandbox faucet',
      _ => Api.i.meta?['rails']?[x['rail']]?['name'] ?? x['rail'],
    };
    return SbxPage(
      back: true,
      onRefresh: load,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const SizedBox(height: 6),
        CardBox(
          padding: const EdgeInsets.all(18),
          child: Stack(children: [
            Positioned.fill(
              child: IgnorePointer(
                child: Center(
                  child: Transform.rotate(
                    angle: -0.42,
                    child: Text('SANDBOX · NOT A REAL TRANSACTION',
                        style: TextStyle(fontFamily: 'monospace', fontSize: 16, fontWeight: FontWeight.w700, color: C.warn.withValues(alpha: .14))),
                  ),
                ),
              ),
            ),
            Column(children: [
              StatusPill(x['status']),
              const SizedBox(height: 10),
              Text('${inbound ? '+' : '−'}${money(x['amount'], cur)}', style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w600, letterSpacing: -1)),
              const SizedBox(height: 4),
              Text(x['title'] ?? '', style: muted),
              const SizedBox(height: 12),
              if (x['failure_reason'] != null)
                Container(
                  width: double.infinity,
                  margin: const EdgeInsets.only(bottom: 8),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(color: C.badSoft, borderRadius: BorderRadius.circular(12)),
                  child: Text('${x['failure_reason']} · funds returned to your wallet.', style: const TextStyle(color: C.bad, fontSize: 13)),
                ),
              Kv('Reference', x['ref'], isMono: true),
              Kv('Type', x['type']),
              Kv('Method', railName.toString()),
              if (cp['name'] != null || cp['account_name'] != null) Kv(inbound ? 'From' : 'To', (cp['name'] ?? cp['account_name']).toString()),
              if (cp['bank_name'] != null) Kv('Bank', cp['bank_name']),
              if (cp['account_number'] != null) Kv('Account', cp['account_number'].toString(), isMono: true),
              if (x['memo'] != null) Kv('Note', x['memo']),
              Kv('Created', when(x['created_at'])),
              if (x['status'] == 'pending' && x['settle_at'] != null) Kv('Expected', when(x['settle_at'])),
              const Kv('Environment', 'SANDBOX', isMono: true),
            ]),
          ]),
        ),
        const SizedBox(height: 16),
        OutlinedButton.icon(
          onPressed: () {
            Clipboard.setData(ClipboardData(text: x['ref']));
            toast(context, 'Reference copied');
          },
          icon: const Icon(Icons.copy, size: 18),
          label: const Text('Copy reference'),
        ),
      ]),
    );
  }
}

// ---------------------------------------------------------------- settings
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});
  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> with WalletsLoader {
  final cur = TextEditingController(), nw = TextEditingController(), cf = TextEditingController();
  bool busy = false;

  @override
  void initState() {
    super.initState();
    loadWallets();
  }

  Future<void> changePw() async {
    if (nw.text != cf.text) return toast(context, 'New passwords do not match', error: true);
    setState(() => busy = true);
    try {
      await Api.i.req('POST', '/api/me/password', body: {'current_password': cur.text, 'new_password': nw.text});
      cur.clear();
      nw.clear();
      cf.clear();
      if (mounted) toast(context, 'Password updated');
    } catch (e) {
      if (mounted) toast(context, e.toString(), error: true);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final u = Api.i.user ?? {};
    final rules = List<String>.from(Api.i.meta?['test_rules'] ?? []);
    return SbxPage(
      title: 'Settings',
      onRefresh: loadWallets,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        sectionTitle('Profile'),
        CardBox(child: Column(children: [Kv('Name', u['full_name'] ?? ''), Kv('Email', u['email'] ?? ''), Kv('Phone', u['phone'] ?? '—')])),
        sectionTitle('Account numbers'),
        CardBox(child: Column(children: (wallets ?? []).map((w) => Kv(w['currency'], w['account_number'], isMono: true)).toList())),
        sectionTitle('Change password'),
        CardBox(
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            TextField(controller: cur, obscureText: true, decoration: const InputDecoration(labelText: 'Current password')),
            const SizedBox(height: 12),
            TextField(controller: nw, obscureText: true, decoration: const InputDecoration(labelText: 'New password (8+ characters)')),
            const SizedBox(height: 12),
            TextField(controller: cf, obscureText: true, decoration: const InputDecoration(labelText: 'Confirm new password')),
            const SizedBox(height: 16),
            FilledButton(onPressed: busy ? null : changePw, child: const Text('Update password')),
          ]),
        ),
        sectionTitle('Server'),
        CardBox(child: Kv('API', Api.i.baseUrl, isMono: true)),
        sectionTitle('Sandbox rules'),
        CardBox(
          child: Text(
            'Wallets start at 0. Test top-ups are capped per currency. Transfers only reach other sandbox users. '
            'Payout rails and banks are fictional and settle on a timer.${rules.map((r) => '\n• $r').join()}',
            style: const TextStyle(color: C.ink2, height: 1.5, fontSize: 14),
          ),
        ),
        const SizedBox(height: 22),
        OutlinedButton(
          style: OutlinedButton.styleFrom(foregroundColor: C.bad, side: const BorderSide(color: Color(0xFFF3C7C2))),
          onPressed: () => Api.i.signOut(),
          child: const Text('Sign out'),
        ),
      ]),
    );
  }
}
