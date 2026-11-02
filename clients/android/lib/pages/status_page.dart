import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../bridge.dart';
import '../main.dart';
import '../theme.dart';

/// The one question: is protection on right now? Then what each part is doing.
class StatusPage extends StatelessWidget {
  const StatusPage({super.key});

  @override
  Widget build(BuildContext context) {
    final model = context.watch<AppModel>();
    final s = model.status!;
    final heartbeat = s.lastHeartbeatAt == 0
        ? 'no heartbeat yet'
        : 'heartbeat ${_ago(DateTime.fromMillisecondsSinceEpoch(s.lastHeartbeatAt))}';
    final rows = [
      ('Accessibility', s.accessibility),
      ('Overlay', s.overlay),
      ('Device admin', s.deviceAdmin),
      ('Model on the phone', s.modelReady),
    ];
    return Scaffold(
      appBar: AppBar(
        title: const Text('Watchman'),
        actions: [IconButton(onPressed: model.refresh, icon: const Icon(Icons.refresh), tooltip: 'Refresh')],
      ),
      body: RefreshIndicator(
        onRefresh: model.refresh,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 4, 20, 32),
          children: [
            Text(
              s.paused ? 'Paused by a partner code' : (s.protectionOn ? 'Protection on' : 'Protection off'),
              style: TextStyle(fontSize: 24, fontWeight: FontWeight.w500, letterSpacing: -0.2,
                  color: s.protectionOn && !s.paused ? null : (s.paused ? warning : negative)),
            ),
            const SizedBox(height: 4),
            Text(
              s.paused
                  ? 'Until ${_local(s.pausedUntil!)}. Every pause appears in the next digest.'
                  : 'Reporting to ${s.serverName ?? s.server}, $heartbeat.',
              style: const TextStyle(color: muted),
            ),
            if (s.lastError != null) ...[
              const SizedBox(height: 8),
              Text(s.lastError!, style: const TextStyle(color: negative, fontSize: 12)),
            ],
            const SizedBox(height: 20),
            Card(
              child: Column(
                children: [
                  for (final (i, row) in rows.indexed)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                      decoration: BoxDecoration(
                        border: i == 0 ? null : const Border(top: BorderSide(color: Color(0xFFEDF1F2))),
                      ),
                      child: Row(children: [
                        Expanded(child: Text(row.$1)),
                        Pill(row.$2 ? 'on' : 'off', kind: row.$2 ? PillKind.positive : PillKind.negative),
                      ]),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            Text(
              s.classifyMs > 0
                  ? 'Per image: ${s.classifyMs.toStringAsFixed(0)} ms on ${s.provider ?? 'cpu'}. Under 50 ms is the aim.'
                  : 'Per image: not measured yet; it shows after the first scored screen.',
              style: const TextStyle(color: muted, fontSize: 12),
            ),
            if (s.budget != null) ...[
              const SizedBox(height: 4),
              Text('Power: ${s.budget}.${s.work != null ? ' ${s.work}.' : ''}', style: const TextStyle(color: muted, fontSize: 12)),
            ],
            const SizedBox(height: 8),
            if (s.queued > 0)
              Text('${s.queued} event${s.queued == 1 ? '' : 's'} waiting to send.', style: const TextStyle(color: muted, fontSize: 12)),
            const SizedBox(height: 20),
            OutlinedButton(onPressed: () => Navigator.of(context).push(pauseRoute()), child: const Text('Partner code or pause request')),
            const SizedBox(height: 8),
            OutlinedButton(onPressed: () => Navigator.of(context).push(setupRoute()), child: const Text('Permissions')),
            const SizedBox(height: 24),
            const Text(
              'Images are judged on this phone at their own size. Only covered or flagged crops go to your server, where the original is deleted after a second opinion and partners see a heavy blur.',
              style: TextStyle(color: muted, fontSize: 12),
            ),
          ],
        ),
      ),
    );
  }

  static String _ago(DateTime t) {
    final d = DateTime.now().difference(t);
    if (d.inMinutes < 1) return 'just now';
    if (d.inHours < 1) return '${d.inMinutes} min ago';
    return '${d.inHours} h ago';
  }

  static String _local(String iso) {
    final t = DateTime.tryParse(iso)?.toLocal();
    if (t == null) return iso;
    return '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
  }
}
