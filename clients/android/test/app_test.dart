import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:watchman/bridge.dart';
import 'package:watchman/main.dart';
import 'package:watchman/pages/pair_page.dart';

class FakeBridge implements Bridge {
  FakeBridge(this.current);
  Status current;
  final calls = <String>[];

  @override
  Future<Status> status() async => current;

  @override
  Future<Status> pair(String server, String code, String name) async {
    calls.add('pair:$server:$code');
    current = _paired(accessibility: false);
    return current;
  }

  @override
  Future<String> unlock(String code) async => '2026-11-01T13:00:00Z';

  @override
  Future<void> requestUnlock(int minutes, String reason) async => calls.add('request:$minutes');

  @override
  Future<void> open(String what) async => calls.add('open:$what');

  @override
  Future<void> startService() async => calls.add('startService');

  PairLink? pending;
  PairLink? scanned;
  final links = StreamController<PairLink>.broadcast();

  @override
  Future<PairLink?> pendingPairLink() async { final p = pending; pending = null; return p; }

  @override
  Future<PairLink?> scanPairCode() async { calls.add('scan'); return scanned; }

  @override
  Stream<PairLink> get pairLinks => links.stream;
}

Status _paired({bool accessibility = true, bool capture = true, String? pausedUntil, bool rejected = false}) => Status(
      paired: true,
      rejected: rejected,
      serverName: 'Example household',
      server: 'https://watchman.test',
      policyVersion: 3,
      pausedUntil: pausedUntil,
      modelReady: true,
      accessibility: accessibility,
      capture: capture,
      overlay: true,
      deviceAdmin: true,
      batteryUnrestricted: true,
      serviceRunning: true,
      lastHeartbeatAt: DateTime.now().millisecondsSinceEpoch,
      queued: 0,
      classifyMs: 32,
      provider: 'nnapi',
    );

const _unpaired = Status(
  paired: false, modelReady: false, accessibility: false, capture: false, overlay: false, deviceAdmin: false,
  batteryUnrestricted: false, serviceRunning: false, lastHeartbeatAt: 0, queued: 0,
);

void main() {
  testWidgets('an unpaired phone starts on the pairing page and pairs', (tester) async {
    final bridge = FakeBridge(_unpaired);
    await tester.pumpWidget(WatchmanApp(bridge: bridge));
    await tester.pumpAndSettle();
    expect(find.text('Pair this phone'), findsOneWidget);
    await tester.enterText(find.byType(TextField).at(0), 'https://watchman.test');
    await tester.enterText(find.byType(TextField).at(1), 'K7Q-4MZ-P2D');
    await tester.tap(find.widgetWithText(FilledButton, 'Pair'));
    await tester.pumpAndSettle();
    expect(bridge.calls, contains('pair:https://watchman.test:K7Q-4MZ-P2D'));
    // Accessibility is still off, so the walkthrough shows next.
    expect(find.text('Set up protection'), findsOneWidget);
    expect(find.text('3 of 4 done. Each one opens a system screen; come back here after.'), findsOneWidget);
  });

  testWidgets('the code takes its dashes and capitals as you type', (tester) async {
    final bridge = FakeBridge(_unpaired);
    await tester.pumpWidget(WatchmanApp(bridge: bridge));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).at(0), 'https://watchman.test');
    await tester.enterText(find.byType(TextField).at(1), 'k7q4mzp2d');
    expect(tester.widget<TextField>(find.byType(TextField).at(1)).controller!.text, 'K7Q-4MZ-P2D');
    await tester.tap(find.widgetWithText(FilledButton, 'Pair'));
    await tester.pumpAndSettle();
    expect(bridge.calls, contains('pair:https://watchman.test:K7Q-4MZ-P2D'));
  });

  testWidgets('a scanned or linked code fills the form but does not pair', (tester) async {
    final bridge = FakeBridge(_unpaired)..pending = const PairLink('https://watchman.linked.ts.net', 'ABC-DEF-GHJ');
    await tester.pumpWidget(WatchmanApp(bridge: bridge));
    await tester.pumpAndSettle();
    expect(find.text('https://watchman.linked.ts.net'), findsOneWidget);
    expect(tester.widget<TextField>(find.byType(TextField).at(1)).controller!.text, 'ABC-DEF-GHJ');
    bridge.scanned = const PairLink('https://watchman.scanned.ts.net', 'K7Q4MZP2D');
    await tester.tap(find.text('Scan code'));
    await tester.pumpAndSettle();
    expect(find.text('https://watchman.scanned.ts.net'), findsOneWidget);
    expect(tester.widget<TextField>(find.byType(TextField).at(1)).controller!.text, 'K7Q-4MZ-P2D');
    expect(bridge.calls.where((c) => c.startsWith('pair:')), isEmpty);
  });

  test('the formatter keeps only code characters', () {
    expect(PairingCodeFormatter.format('k7q-4mz p2d'), 'K7Q-4MZ-P2D');
    expect(PairingCodeFormatter.format('O0I1L'), '');
    expect(PairingCodeFormatter.format('ABCDEFGHJKMN'), 'ABC-DEF-GHJ');
  });

  testWidgets('a set-up phone shows protection on', (tester) async {
    await tester.pumpWidget(WatchmanApp(bridge: FakeBridge(_paired())));
    await tester.pumpAndSettle();
    expect(find.text('Protection on'), findsOneWidget);
    expect(find.textContaining('Reporting to Example household'), findsOneWidget);
    expect(find.text('on'), findsNWidgets(4)); // accessibility, overlay, device admin, battery; no capture row (ADR-026)
    expect(find.textContaining('Per image: 32 ms on nnapi'), findsOneWidget);
  });

  testWidgets('a phone the server rejected keeps protecting and offers to pair again', (tester) async {
    await tester.pumpWidget(WatchmanApp(bridge: FakeBridge(_paired(rejected: true))));
    await tester.pumpAndSettle();
    expect(find.text('Protection on'), findsOneWidget);
    expect(find.textContaining('The server no longer accepts this device. Protection stays on.'), findsOneWidget);
    await tester.tap(find.text('Pair again'));
    await tester.pumpAndSettle();
    expect(find.text('Pair this phone'), findsOneWidget);
  });

  testWidgets('a paused phone says so', (tester) async {
    final until = DateTime.now().toUtc().add(const Duration(minutes: 20)).toIso8601String();
    await tester.pumpWidget(WatchmanApp(bridge: FakeBridge(_paired(pausedUntil: until))));
    await tester.pumpAndSettle();
    expect(find.text('Paused by a partner code'), findsOneWidget);
  });

  test('status parses the channel map', () {
    final s = Status.fromMap({'paired': true, 'accessibility': true, 'capture': false, 'overlay': true, 'deviceAdmin': true,
      'batteryUnrestricted': true, 'serviceRunning': true, 'modelReady': true, 'lastHeartbeatAt': 5, 'queued': 2});
    expect(s.paired, isTrue);
    expect(s.capture, isFalse);
    expect(s.queued, 2);
    expect(s.rejected, isFalse);
    expect(s.allPermissionsGranted, isTrue);
  });
}
