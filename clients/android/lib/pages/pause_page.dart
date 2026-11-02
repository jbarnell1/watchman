import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../bridge.dart';
import '../theme.dart';

class PausePage extends StatefulWidget {
  const PausePage({super.key});

  @override
  State<PausePage> createState() => _PausePageState();
}

class _PausePageState extends State<PausePage> {
  final _code = TextEditingController();
  final _minutes = TextEditingController(text: '30');
  final _reason = TextEditingController();
  String? _codeError;
  String? _note;

  @override
  Widget build(BuildContext context) {
    final model = context.read<AppModel>();
    return Scaffold(
      appBar: AppBar(title: const Text('Pause')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 4, 20, 32),
        children: [
          const Text('Nothing pauses protection without a partner code. A code is single use and the pause appears in the next digest.', style: TextStyle(color: muted)),
          const SizedBox(height: 24),
          const Text('Code from your partner', style: TextStyle(fontWeight: FontWeight.w500)),
          const SizedBox(height: 6),
          TextField(controller: _code, keyboardType: TextInputType.number, decoration: const InputDecoration(hintText: '482-913')),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: () async {
              final err = await model.unlock(_code.text.trim());
              if (!mounted) return;
              setState(() => _codeError = err);
              if (err == null && context.mounted) Navigator.of(context).pop();
            },
            child: const Text('Pause'),
          ),
          if (_codeError != null) ...[const SizedBox(height: 8), Text(_codeError!, style: const TextStyle(color: negative))],
          const SizedBox(height: 32),
          const Text('Or ask for one', style: TextStyle(fontWeight: FontWeight.w500, fontSize: 16)),
          const SizedBox(height: 6),
          const Text('Your partners get a message. Email-only partners receive a code they can send you; linked partners issue one from the portal.', style: TextStyle(color: muted)),
          const SizedBox(height: 16),
          const Text('Minutes', style: TextStyle(fontWeight: FontWeight.w500)),
          const SizedBox(height: 6),
          TextField(controller: _minutes, keyboardType: TextInputType.number),
          const SizedBox(height: 12),
          const Text('Reason', style: TextStyle(fontWeight: FontWeight.w500)),
          const SizedBox(height: 6),
          TextField(controller: _reason, decoration: const InputDecoration(hintText: 'work upload')),
          const SizedBox(height: 12),
          OutlinedButton(
            onPressed: () async {
              final err = await model.requestUnlock(int.tryParse(_minutes.text) ?? 30, _reason.text);
              if (!mounted) return;
              setState(() => _note = err ?? 'Asked. A partner can send you a code.');
            },
            child: const Text('Ask my partners'),
          ),
          if (_note != null) ...[const SizedBox(height: 8), Text(_note!, style: const TextStyle(color: muted))],
        ],
      ),
    );
  }
}
