import 'package:flutter/material.dart';

/// Small rounded-pill label used for trip categories ("Relaxation"),
/// statuses ("Completed", "Live") and badges ("Top Pick").
class PillTag extends StatelessWidget {
  const PillTag(
    this.label, {
    super.key,
    this.background,
    this.foreground,
    this.dense = false,
  });

  final String label;
  final Color? background;
  final Color? foreground;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: dense ? 8 : 12,
        vertical: dense ? 3 : 5,
      ),
      decoration: BoxDecoration(
        color: background ?? scheme.secondaryContainer,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        style: Theme.of(context).textTheme.labelSmall?.copyWith(
          color: foreground ?? scheme.onSecondaryContainer,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.6,
        ),
      ),
    );
  }
}
