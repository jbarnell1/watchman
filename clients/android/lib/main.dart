import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'bridge.dart';
import 'pages/pair_page.dart';
import 'pages/pause_page.dart';
import 'pages/setup_page.dart';
import 'pages/status_page.dart';
import 'theme.dart';

void main() {
  runApp(const WatchmanApp());
}

class WatchmanApp extends StatelessWidget {
  const WatchmanApp({super.key, this.bridge});

  final Bridge? bridge;

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider(
      create: (_) => AppModel(bridge ?? const ChannelBridge())..refresh(),
      child: MaterialApp(
        title: 'Watchman',
        debugShowCheckedModeBanner: false,
        theme: watchmanTheme(Brightness.light),
        darkTheme: watchmanTheme(Brightness.dark),
        home: const Home(),
      ),
    );
  }
}

class Home extends StatelessWidget {
  const Home({super.key});

  @override
  Widget build(BuildContext context) {
    final model = context.watch<AppModel>();
    final status = model.status;
    if (status == null) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    if (!status.paired) return const PairPage();
    if (!status.allPermissionsGranted) return const SetupPage();
    return const StatusPage();
  }
}

/// Routes used from the status page.
Route<void> pauseRoute() => MaterialPageRoute(builder: (_) => const PausePage());
Route<void> setupRoute() => MaterialPageRoute(builder: (_) => const SetupPage());
