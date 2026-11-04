import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../bridge.dart';

/// Nine code characters in three groups of three: upper case, dashes added as you type, and only the
/// characters codes use (no 0, O, 1, I or L), so a typo shows at once.
class PairingCodeFormatter extends TextInputFormatter {
  static const alphabet = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';

  static String format(String text) {
    final chars = text.toUpperCase().split('').where(alphabet.contains).take(9).toList();
    final groups = <String>[];
    for (var i = 0; i < chars.length; i += 3) {
      groups.add(chars.sublist(i, i + 3 > chars.length ? chars.length : i + 3).join());
    }
    return groups.join('-');
  }

  @override
  TextEditingValue formatEditUpdate(TextEditingValue oldValue, TextEditingValue newValue) {
    // Deleting a dash deletes the character before it, so backspace never sticks on a dash.
    var raw = newValue.text;
    if (oldValue.text.length == raw.length + 1 && oldValue.text.endsWith('-') && !raw.endsWith('-')) {
      raw = raw.isEmpty ? raw : raw.substring(0, raw.length - 1);
    }
    final text = format(raw);
    return TextEditingValue(text: text, selection: TextSelection.collapsed(offset: text.length));
  }
}

class PairPage extends StatefulWidget {
  const PairPage({super.key});

  @override
  State<PairPage> createState() => _PairPageState();
}

class _PairPageState extends State<PairPage> {
  final _server = TextEditingController();
  final _code = TextEditingController();
  final _name = TextEditingController();
  String? _error;
  String? _note;
  bool _busy = false;
  bool _scanning = false;
  StreamSubscription<PairLink>? _links;

  @override
  void initState() {
    super.initState();
    final bridge = context.read<AppModel>().bridge;
    bridge.pendingPairLink().then((link) { if (link != null) _fill(link); });
    _links = bridge.pairLinks.listen(_fill);
  }

  @override
  void dispose() {
    _links?.cancel();
    super.dispose();
  }

  void _fill(PairLink link) {
    if (!mounted) return;
    setState(() {
      _server.text = link.server;
      _code.text = PairingCodeFormatter.format(link.code);
      _error = null;
      _note = 'Filled in from the pairing code. Check the server is your household\'s, name this phone, then tap Pair.';
    });
  }

  Future<void> _scan() async {
    setState(() { _error = null; _scanning = true; });
    try {
      final link = await context.read<AppModel>().bridge.scanPairCode();
      if (link != null) _fill(link);
    } on PlatformException catch (e) {
      if (mounted) setState(() => _error = e.message ?? e.code);
    } finally {
      ChannelBridge.scannerStatus.value = null;
      if (mounted) setState(() => _scanning = false);
    }
  }

  Future<void> _pair() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final err = await context.read<AppModel>().pair(_server.text.trim(), _code.text.trim(), _name.text.trim());
    if (!mounted) return;
    setState(() {
      _busy = false;
      _error = err;
    });
    // Opened from the status page to pair again: go back once the new code took.
    if (err == null && Navigator.of(context).canPop()) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).textTheme.bodyMedium!.copyWith(color: const Color(0xFF5F6B70));
    return Scaffold(
      appBar: AppBar(title: const Text('Pair this phone')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 4, 20, 32),
        children: [
          Text('Your household server runs on your own Tailscale network. The admin creates a pairing code on its Devices page; it is valid for 15 minutes.', style: muted),
          const SizedBox(height: 16),
          OutlinedButton.icon(onPressed: _busy || _scanning ? null : _scan, icon: const Icon(Icons.qr_code_scanner), label: const Text('Scan code')),
          ValueListenableBuilder<String?>(
            valueListenable: ChannelBridge.scannerStatus,
            builder: (context, status, _) => status == null
                ? const SizedBox.shrink()
                : Padding(padding: const EdgeInsets.only(top: 10), child: Text(status, style: muted)),
          ),
          if (_note != null) ...[
            const SizedBox(height: 10),
            Text(_note!, style: muted),
          ],
          const SizedBox(height: 24),
          const Text('Server', style: TextStyle(fontWeight: FontWeight.w500)),
          const SizedBox(height: 6),
          TextField(controller: _server, keyboardType: TextInputType.url, autocorrect: false,
              decoration: const InputDecoration(hintText: 'https://watchman.your-tailnet.ts.net')),
          const SizedBox(height: 16),
          const Text('Pairing code', style: TextStyle(fontWeight: FontWeight.w500)),
          const SizedBox(height: 6),
          TextField(controller: _code, textCapitalization: TextCapitalization.characters, autocorrect: false,
              enableSuggestions: false, keyboardType: TextInputType.visiblePassword,
              inputFormatters: [PairingCodeFormatter()],
              decoration: const InputDecoration(hintText: 'K7Q-4MZ-P2D')),
          const SizedBox(height: 16),
          const Text("This phone's name", style: TextStyle(fontWeight: FontWeight.w500)),
          const SizedBox(height: 6),
          TextField(controller: _name, decoration: const InputDecoration(hintText: 'Pixel')),
          const SizedBox(height: 24),
          FilledButton(onPressed: _busy ? null : _pair, child: Text(_busy ? 'Pairing' : 'Pair')),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(_error!, style: const TextStyle(color: Color(0xFFC4242A))),
          ],
        ],
      ),
    );
  }
}
