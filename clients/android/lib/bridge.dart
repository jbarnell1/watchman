import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// What the Kotlin side reports. Everything the screens show comes from here.
class Status {
  const Status({
    required this.paired,
    this.serverName,
    this.server,
    this.policyVersion,
    this.pausedUntil,
    required this.modelReady,
    required this.accessibility,
    required this.capture,
    required this.overlay,
    required this.deviceAdmin,
    required this.batteryUnrestricted,
    required this.serviceRunning,
    required this.lastHeartbeatAt,
    this.lastError,
    required this.queued,
    this.classifyMs = 0,
    this.provider,
    this.budget,
    this.work,
  });

  final bool paired;
  final String? serverName;
  final String? server;
  final int? policyVersion;
  final String? pausedUntil;
  final bool modelReady;
  final bool accessibility;
  final bool capture;
  final bool overlay;
  final bool deviceAdmin;
  final bool batteryUnrestricted;
  final bool serviceRunning;
  final int lastHeartbeatAt;
  final String? lastError;
  final int queued;
  final double classifyMs;
  final String? provider;
  /// Plugged in, on battery, low battery: what the capture service is doing about it (ADR-023).
  final String? budget;
  /// Frames pulled, screens judged, screens skipped as unchanged, crops asked of the server.
  final String? work;

  bool get allPermissionsGranted => accessibility && overlay && deviceAdmin && batteryUnrestricted;

  bool get paused {
    final p = pausedUntil;
    if (p == null) return false;
    final until = DateTime.tryParse(p);
    return until != null && until.isAfter(DateTime.now().toUtc());
  }

  /// Protection is on when the accessibility service runs: it takes the screenshots itself (ADR-026).
  bool get protectionOn => accessibility && serviceRunning && !paused;

  factory Status.fromMap(Map<Object?, Object?> m) => Status(
        paired: m['paired'] == true,
        serverName: m['serverName'] as String?,
        server: m['server'] as String?,
        policyVersion: m['policyVersion'] as int?,
        pausedUntil: m['pausedUntil'] as String?,
        modelReady: m['modelReady'] == true,
        accessibility: m['accessibility'] == true,
        capture: m['capture'] == true,
        overlay: m['overlay'] == true,
        deviceAdmin: m['deviceAdmin'] == true,
        batteryUnrestricted: m['batteryUnrestricted'] == true,
        serviceRunning: m['serviceRunning'] == true,
        lastHeartbeatAt: (m['lastHeartbeatAt'] as num?)?.toInt() ?? 0,
        lastError: m['lastError'] as String?,
        queued: (m['queued'] as num?)?.toInt() ?? 0,
        classifyMs: (m['classifyMs'] as num?)?.toDouble() ?? 0,
        provider: m['provider'] as String?,
        budget: m['budget'] as String?,
        work: m['work'] as String?,
      );
}

/// The Kotlin side behind an interface so widget tests use a fake.
abstract class Bridge {
  Future<Status> status();
  Future<Status> pair(String server, String code, String name);
  Future<String> unlock(String code);
  Future<void> requestUnlock(int minutes, String reason);
  Future<void> open(String what);
  /// A pairing link the app was opened with (from the portal's /pair page), once.
  Future<PairLink?> pendingPairLink();
  /// Google's QR scanner; null when the person backs out.
  Future<PairLink?> scanPairCode();
  /// Pairing links that arrive while the app is open.
  Stream<PairLink> get pairLinks;
  Future<void> startService();
}

class ChannelBridge implements Bridge {
  const ChannelBridge();
  static const _channel = MethodChannel('watchman');

  @override
  Future<Status> status() async =>
      Status.fromMap((await _channel.invokeMethod<Map<Object?, Object?>>('status'))!);

  @override
  Future<Status> pair(String server, String code, String name) async => Status.fromMap(
      (await _channel.invokeMethod<Map<Object?, Object?>>('pair', {'server': server, 'code': code, 'name': name}))!);

  @override
  Future<String> unlock(String code) async => (await _channel.invokeMethod<String>('unlock', {'code': code}))!;

  @override
  Future<void> requestUnlock(int minutes, String reason) =>
      _channel.invokeMethod('requestUnlock', {'minutes': minutes, 'reason': reason});

  @override
  Future<void> open(String what) => _channel.invokeMethod('open', {'what': what});

  @override
  Future<PairLink?> pendingPairLink() async =>
      PairLink.fromMap(await _channel.invokeMethod<Map<Object?, Object?>>('pendingPairLink'));

  @override
  Future<PairLink?> scanPairCode() async =>
      PairLink.fromMap(await _channel.invokeMethod<Map<Object?, Object?>>('scanPairCode'));

  @override
  Stream<PairLink> get pairLinks {
    _links ??= StreamController<PairLink>.broadcast(onListen: () {
      _channel.setMethodCallHandler((call) async {
        if (call.method == 'pairLink') {
          final link = PairLink.fromMap(call.arguments as Map<Object?, Object?>?);
          if (link != null) _links!.add(link);
        }
        if (call.method == 'scannerStatus') scannerStatus.value = call.arguments as String?;
        return null;
      });
    });
    return _links!.stream;
  }

  static StreamController<PairLink>? _links;

  /// "Getting the scanner ready" while Google's scanner downloads on first use.
  static final scannerStatus = ValueNotifier<String?>(null);

  @override

  @override
  Future<void> startService() => _channel.invokeMethod('startService');
}

class AppModel extends ChangeNotifier {
  AppModel(this.bridge);

  final Bridge bridge;
  Status? status;
  String? error;

  Future<void> refresh() async {
    try {
      status = await bridge.status();
      error = null;
    } on PlatformException catch (e) {
      error = e.message;
    }
    notifyListeners();
  }

  Future<String?> pair(String server, String code, String name) async {
    try {
      status = await bridge.pair(server, code, name);
      notifyListeners();
      return null;
    } on PlatformException catch (e) {
      return e.message ?? e.code;
    }
  }

  Future<String?> unlock(String code) async {
    try {
      await bridge.unlock(code);
      await refresh();
      return null;
    } on PlatformException catch (e) {
      return e.message ?? e.code;
    }
  }

  Future<String?> requestUnlock(int minutes, String reason) async {
    try {
      await bridge.requestUnlock(minutes, reason);
      return null;
    } on PlatformException catch (e) {
      return e.message ?? e.code;
    }
  }
}

/// A pairing hand-off: the server and the code, from a QR code or a link. It only fills in the form.
class PairLink {
  const PairLink(this.server, this.code);
  final String server;
  final String code;

  static PairLink? fromMap(Map<Object?, Object?>? m) {
    if (m == null || m['server'] == null || m['code'] == null) return null;
    return PairLink(m['server'] as String, m['code'] as String);
  }
}
