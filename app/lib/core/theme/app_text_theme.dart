import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// Typography for the Terra design system:
/// Literata (warm serif) for headlines/display, Nunito Sans (friendly,
/// rounded) for body/labels. Generous line-height for unhurried reading.
class AppTextTheme {
  AppTextTheme._();

  static TextTheme build(Color onSurface) {
    final base = GoogleFonts.nunitoSansTextTheme();
    final headlineStyle = GoogleFonts.literataTextTheme();

    return base
        .copyWith(
          displayLarge: headlineStyle.displayLarge?.copyWith(
            fontWeight: FontWeight.w700,
            fontSize: 48,
            height: 56 / 48,
            letterSpacing: -0.5,
          ),
          displayMedium: headlineStyle.displayMedium?.copyWith(
            fontWeight: FontWeight.w700,
            fontSize: 32,
            height: 40 / 32,
            letterSpacing: -0.3,
          ),
          headlineLarge: headlineStyle.headlineLarge?.copyWith(
            fontWeight: FontWeight.w600,
            fontSize: 28,
            height: 1.25,
          ),
          headlineMedium: headlineStyle.headlineMedium?.copyWith(
            fontWeight: FontWeight.w600,
            fontSize: 24,
            height: 32 / 24,
          ),
          headlineSmall: headlineStyle.headlineSmall?.copyWith(
            fontWeight: FontWeight.w600,
            fontSize: 20,
            height: 28 / 20,
          ),
          titleLarge: headlineStyle.titleLarge?.copyWith(
            fontWeight: FontWeight.w600,
            fontSize: 18,
          ),
          titleMedium: base.titleMedium?.copyWith(
            fontWeight: FontWeight.w600,
            fontSize: 16,
            height: 1.5,
          ),
          titleSmall: base.titleSmall?.copyWith(
            fontWeight: FontWeight.w600,
            fontSize: 14,
          ),
          bodyLarge: base.bodyLarge?.copyWith(fontSize: 18, height: 1.6),
          bodyMedium: base.bodyMedium?.copyWith(fontSize: 16, height: 1.6),
          bodySmall: base.bodySmall?.copyWith(fontSize: 14, height: 1.5),
          labelLarge: base.labelLarge?.copyWith(
            fontWeight: FontWeight.w600,
            fontSize: 14,
            letterSpacing: 0.4,
          ),
          labelMedium: base.labelMedium?.copyWith(
            fontWeight: FontWeight.w600,
            fontSize: 13,
            letterSpacing: 0.3,
          ),
          labelSmall: base.labelSmall?.copyWith(
            fontWeight: FontWeight.w600,
            fontSize: 11,
            letterSpacing: 0.5,
          ),
        )
        .apply(bodyColor: onSurface, displayColor: onSurface);
  }
}
