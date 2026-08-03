import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// Warm cream card with generous padding, rounded corners and a very soft
/// shadow — the base building block for most content blocks in the app.
class SoftCard extends StatelessWidget {
  const SoftCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(20),
    this.color,
    this.borderRadius = AppRadius.sm,
    this.border = true,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final Color? color;
  final double borderRadius;
  final bool border;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: padding,
      decoration: BoxDecoration(
        color: color ?? scheme.surfaceContainerLowest,
        borderRadius: BorderRadius.circular(borderRadius),
        border: border
            ? Border.all(color: scheme.outlineVariant.withValues(alpha: 0.3))
            : null,
        boxShadow: kSoftShadow,
      ),
      child: child,
    );
  }
}
