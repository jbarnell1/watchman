import 'package:flutter/material.dart';

// The portal's tokens (ADR-013): ink and off-white chrome, one hue on the mark and the data.
const ink = Color(0xFF0B1114);
const ground = Color(0xFFF2F5F6);
const surface = Color(0xFFFFFFFF);
const muted = Color(0xFF5F6B70);
const hue = Color(0xFF156D7A);
const positive = Color(0xFF247B50);
const negative = Color(0xFFC4242A);
const warning = Color(0xFF92400E);

const darkGround = Color(0xFF000000);
const darkSurface = Color(0xFF111111);
const darkText = Color(0xFFFFFFFF);
const darkMuted = Color(0xFFB4B4B4);
const darkHue = Color(0xFF3FB3C4);

ThemeData watchmanTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final scheme = ColorScheme(
    brightness: brightness,
    primary: dark ? darkText : ink,
    onPrimary: dark ? darkGround : surface,
    secondary: dark ? darkHue : hue,
    onSecondary: surface,
    error: negative,
    onError: surface,
    surface: dark ? darkSurface : surface,
    onSurface: dark ? darkText : ink,
  );
  final base = ThemeData(colorScheme: scheme, useMaterial3: true, brightness: brightness);
  return base.copyWith(
    scaffoldBackgroundColor: dark ? darkGround : ground,
    textTheme: base.textTheme.apply(bodyColor: scheme.onSurface, displayColor: scheme.onSurface),
    appBarTheme: AppBarTheme(
      backgroundColor: dark ? darkGround : ground,
      foregroundColor: scheme.onSurface,
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
      titleTextStyle: TextStyle(fontSize: 24, fontWeight: FontWeight.w500, color: scheme.onSurface, letterSpacing: -0.2),
    ),
    cardTheme: CardThemeData(
      color: scheme.surface,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: scheme.primary,
        foregroundColor: scheme.onPrimary,
        minimumSize: const Size(0, 44),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
        textStyle: const TextStyle(fontWeight: FontWeight.w500),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: scheme.onSurface,
        minimumSize: const Size(0, 44),
        side: BorderSide(color: dark ? const Color(0xFF444444) : const Color(0xFFC9D2D5)),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: scheme.surface,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(6), borderSide: const BorderSide(color: Color(0xFFC9D2D5))),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(6), borderSide: BorderSide(color: dark ? const Color(0xFF444444) : const Color(0xFFC9D2D5))),
      focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(6), borderSide: BorderSide(color: dark ? darkHue : hue, width: 2)),
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
    ),
  );
}

/// A small status pill: pale tint, dark text of the same hue.
class Pill extends StatelessWidget {
  const Pill(this.text, {super.key, this.kind = PillKind.neutral});

  final String text;
  final PillKind kind;

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    final (bg, fg) = switch (kind) {
      PillKind.positive => dark ? (const Color(0xFF172A1D), const Color(0xFF3FC07A)) : (const Color(0xFFDCFAE5), const Color(0xFF04250E)),
      PillKind.negative => dark ? (const Color(0xFF2C1A20), const Color(0xFFE5566F)) : (const Color(0xFFFFEDEA), const Color(0xFF831C1E)),
      PillKind.warning => dark ? (const Color(0xFF2A2208), const Color(0xFFF0C76A)) : (const Color(0xFFFFFBEB), warning),
      PillKind.neutral => dark ? (const Color(0xFF222222), darkMuted) : (const Color(0xFFE9EEF0), const Color(0xFF3A4549)),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 1),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(4)),
      child: Text(text, style: TextStyle(fontSize: 11, fontWeight: FontWeight.w500, color: fg)),
    );
  }
}

enum PillKind { positive, negative, warning, neutral }
