import 'package:flutter/material.dart';

/// Raw color tokens for the "Terra — Rooted Warmth" design system.
///
/// Source of truth: Frontend/terra/DESIGN.md — calm, grounded, earthy tones,
/// warm cream surfaces, forest green primary, warm amber tertiary accents.
/// Every neutral carries a warm yellow/green undertone (never sterile gray).
class AppColors {
  AppColors._();

  // Brand
  static const primary = Color(0xFF4A7C59);
  static const onPrimary = Color(0xFFFFFFFF);
  static const primaryContainer = Color(0xFFB9EFC5);
  static const onPrimaryContainer = Color(0xFF1B4229);

  static const secondary = Color(0xFF6B6153);
  static const onSecondary = Color(0xFFFFFFFF);
  static const secondaryContainer = Color(0xFFECE1D3);
  static const onSecondaryContainer = Color(0xFF4B4335);

  static const tertiary = Color(0xFF705C30);
  static const onTertiary = Color(0xFFFFFFFF);
  static const tertiaryContainer = Color(0xFFFAD998);
  static const onTertiaryContainer = Color(0xFF4D3806);

  static const error = Color(0xFFBA1A1A);
  static const onError = Color(0xFFFFFFFF);
  static const errorContainer = Color(0xFFFFDAD6);
  static const onErrorContainer = Color(0xFF93000A);

  // Warm neutrals — surfaces
  static const background = Color(0xFFFAF6F0);
  static const onBackground = Color(0xFF2C2A22);

  static const surface = Color(0xFFFAF6F0);
  static const onSurface = Color(0xFF2C2A22);
  static const onSurfaceVariant = Color(0xFF615C4E);

  static const surfaceContainerLowest = Color(0xFFFFFFFF);
  static const surfaceContainerLow = Color(0xFFF5EFE5);
  static const surfaceContainer = Color(0xFFEFE8DA);
  static const surfaceContainerHigh = Color(0xFFE8E0CF);
  static const surfaceContainerHighest = Color(0xFFDED5C0);

  static const outline = Color(0xFF8A8371);
  static const outlineVariant = Color(0xFFDBD3BE);

  static const inverseSurface = Color(0xFF33312A);
  static const onInverseSurface = Color(0xFFF5F0E5);
}
