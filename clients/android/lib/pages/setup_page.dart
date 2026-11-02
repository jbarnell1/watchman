import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../bridge.dart';
import '../theme.dart';

/// The permission walkthrough. Each step opens the system screen; the list refreshes on return.
class SetupPage extends StatefulWidget {
  const SetupPage({super.key});

  @override
  State<SetupPage> createState() => _SetupPageState();
}

class _SetupPageState extends State<SetupPage> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) context.read<AppModel>().refresh();
  }

  @override
  Widget build(BuildContext context) {
    final model = context.watch<AppModel>();
    final s = model.status!;
    final steps = [
      _Step('Accessibility', 'Lets Watchman know which app is in front and where images sit. On Android 13 and later, open App info first and allow restricted settings.', s.accessibility, 'accessibility', extra: 'appInfo'),
      _Step('Draw over other apps', 'Needed to cover an image with a patch the moment it appears.', s.overlay, 'overlay'),
      _Step('Device admin', 'Stops a quiet uninstall. Turning it off is reported to your partners.', s.deviceAdmin, 'deviceAdmin'),
      _Step('Battery: unrestricted', 'So the phone does not put Watchman to sleep.', s.batteryUnrestricted, 'battery'),
    ];
    final done = steps.where((x) => x.done).length;
    return Scaffold(
      appBar: AppBar(title: const Text('Set up protection')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 4, 20, 32),
        children: [
          Text('$done of ${steps.length} done. Each one opens a system screen; come back here after.',
              style: const TextStyle(color: muted)),
          const SizedBox(height: 16),
          for (final step in steps) ...[
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      Expanded(child: Text(step.title, style: const TextStyle(fontWeight: FontWeight.w500, fontSize: 16))),
                      Pill(step.done ? 'Done' : 'Needed', kind: step.done ? PillKind.positive : PillKind.warning),
                    ]),
                    const SizedBox(height: 6),
                    Text(step.text, style: const TextStyle(color: muted)),
                    if (!step.done) ...[
                      const SizedBox(height: 12),
                      Row(children: [
                        FilledButton(onPressed: () => model.bridge.open(step.open), child: const Text('Open settings')),
                        if (step.extra != null) ...[
                          const SizedBox(width: 8),
                          OutlinedButton(onPressed: () => model.bridge.open(step.extra!), child: const Text('App info')),
                        ],
                      ]),
                    ],
                  ],
                ),
              ),
            ),
            const SizedBox(height: 12),
          ],
          const SizedBox(height: 20),
          if (s.allPermissionsGranted)
            FilledButton(onPressed: () => Navigator.of(context).maybePop(), child: const Text('Done'))
          else
            OutlinedButton(onPressed: model.refresh, child: const Text('Check again')),
        ],
      ),
    );
  }
}

class _Step {
  _Step(this.title, this.text, this.done, this.open, {this.extra});
  final String title;
  final String text;
  final bool done;
  final String open;
  final String? extra;
}
