import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/utils/icon_mapper.dart';
import '../../../core/widgets/app_image.dart';
import '../../../core/widgets/async_value_view.dart';
import '../../../data/models/trip.dart';
import '../../../data/providers/providers.dart';

class ActiveTripScreen extends ConsumerWidget {
  const ActiveTripScreen({super.key, required this.tripId});

  final String tripId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tripAsync = ref.watch(tripByIdProvider(tripId));

    return Scaffold(
      appBar: AppBar(
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.public, color: Theme.of(context).colorScheme.primary),
            const SizedBox(width: 8),
            const Text('Active Trip'),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.search),
            onPressed: () => ScaffoldMessenger.of(
              context,
            ).showSnackBar(const SnackBar(content: Text('Search coming soon.'))),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Memory saved to your trip journal.')),
        ),
        backgroundColor: Theme.of(context).colorScheme.tertiary,
        icon: const Icon(Icons.add_a_photo, color: Colors.white),
        label: const Text(
          'Add Quick Memory',
          style: TextStyle(color: Colors.white),
        ),
      ),
      body: AsyncValueView<Trip?>(
        value: tripAsync,
        data: (trip) {
          if (trip == null || trip.liveTimeline.isEmpty) {
            return const Center(child: Text('No trip is active right now.'));
          }
          return _ActiveTripBody(trip: trip);
        },
      ),
    );
  }
}

class _ActiveTripBody extends StatelessWidget {
  const _ActiveTripBody({required this.trip});

  final Trip trip;

  int? get _currentDay {
    for (final day in trip.itinerary) {
      if (day.activities.any((a) => a.status == ActivityStatus.active)) {
        return day.dayNumber;
      }
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final day = _currentDay;

    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 120),
      children: [
        Text(trip.destination, style: theme.textTheme.headlineMedium),
        const SizedBox(height: 2),
        Text(
          day != null ? 'Day $day of ${trip.days}' : '${trip.days}-day trip',
          style: theme.textTheme.bodyMedium?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
        const SizedBox(height: 16),
        Container(
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
            color: theme.colorScheme.surfaceContainer,
            borderRadius: BorderRadius.circular(16),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    'Trip Progress',
                    style: theme.textTheme.labelMedium?.copyWith(
                      color: theme.colorScheme.primary,
                    ),
                  ),
                  Text(
                    '${(trip.progress * 100).round()}%',
                    style: theme.textTheme.labelLarge,
                  ),
                ],
              ),
              const SizedBox(height: 10),
              ClipRRect(
                borderRadius: BorderRadius.circular(999),
                child: LinearProgressIndicator(
                  value: trip.progress,
                  minHeight: 10,
                  backgroundColor: theme.colorScheme.outlineVariant.withValues(
                    alpha: 0.3,
                  ),
                  color: theme.colorScheme.primary,
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 28),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text('Live Timeline', style: theme.textTheme.headlineSmall),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: theme.colorScheme.surfaceContainerHigh,
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                'TODAY',
                style: theme.textTheme.labelSmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                  letterSpacing: 1,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 20),
        for (var i = 0; i < trip.liveTimeline.length; i++)
          _TimelineTile(
            activity: trip.liveTimeline[i],
            isLast: i == trip.liveTimeline.length - 1,
          ),
      ],
    );
  }
}

class _TimelineTile extends StatelessWidget {
  const _TimelineTile({required this.activity, required this.isLast});

  final ItineraryActivity activity;
  final bool isLast;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;

    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Column(
            children: [
              _StatusDot(status: activity.status, icon: activity.icon),
              if (!isLast)
                Expanded(
                  child: Container(
                    width: 2,
                    margin: const EdgeInsets.symmetric(vertical: 4),
                    color: scheme.outlineVariant.withValues(alpha: 0.4),
                  ),
                ),
            ],
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.only(bottom: 28),
              child: _ActivityContent(activity: activity),
            ),
          ),
        ],
      ),
    );
  }
}

class _StatusDot extends StatefulWidget {
  const _StatusDot({required this.status, required this.icon});

  final ActivityStatus status;
  final String icon;

  @override
  State<_StatusDot> createState() => _StatusDotState();
}

class _StatusDotState extends State<_StatusDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final Color bg;
    final Color fg;
    switch (widget.status) {
      case ActivityStatus.completed:
        bg = scheme.primaryContainer;
        fg = scheme.onPrimaryContainer;
      case ActivityStatus.active:
        bg = scheme.primary;
        fg = Colors.white;
      case ActivityStatus.upcoming:
        bg = scheme.surfaceContainerHigh;
        fg = scheme.onSurfaceVariant;
    }

    final dot = Container(
      width: 40,
      height: 40,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: bg,
        shape: BoxShape.circle,
        border: widget.status == ActivityStatus.upcoming
            ? Border.all(color: scheme.outlineVariant, width: 2)
            : null,
        boxShadow: widget.status == ActivityStatus.active
            ? [
                BoxShadow(
                  color: scheme.primary.withValues(alpha: 0.3),
                  blurRadius: 10,
                ),
              ]
            : null,
      ),
      child: Icon(
        widget.status == ActivityStatus.completed
            ? Icons.check_circle
            : iconForKey(widget.icon),
        color: fg,
        size: 18,
      ),
    );

    if (widget.status != ActivityStatus.active) return dot;

    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) {
        return Transform.scale(
          scale: 1 + (_controller.value * 0.08),
          child: child,
        );
      },
      child: dot,
    );
  }
}

class _ActivityContent extends StatefulWidget {
  const _ActivityContent({required this.activity});

  final ItineraryActivity activity;

  @override
  State<_ActivityContent> createState() => _ActivityContentState();
}

class _ActivityContentState extends State<_ActivityContent> {
  int _rating = 0;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final activity = widget.activity;
    final isActive = activity.status == ActivityStatus.active;
    final isCompleted = activity.status == ActivityStatus.completed;

    final header = Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                activity.time.toUpperCase(),
                style: theme.textTheme.labelSmall?.copyWith(
                  color: isActive
                      ? scheme.primary
                      : scheme.onSurfaceVariant,
                  fontWeight: FontWeight.w700,
                ),
              ),
              Text(
                activity.title,
                style: theme.textTheme.titleMedium?.copyWith(
                  color: activity.status == ActivityStatus.upcoming
                      ? scheme.onSurfaceVariant
                      : scheme.onSurface,
                ),
              ),
            ],
          ),
        ),
        if (isCompleted)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(
              color: scheme.primaryContainer.withValues(alpha: 0.5),
              borderRadius: BorderRadius.circular(6),
            ),
            child: Text(
              'COMPLETED',
              style: theme.textTheme.labelSmall?.copyWith(
                color: scheme.primary,
              ),
            ),
          ),
        if (isActive)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(
              color: scheme.error,
              borderRadius: BorderRadius.circular(999),
            ),
            child: Text(
              'LIVE',
              style: theme.textTheme.labelSmall?.copyWith(
                color: Colors.white,
                fontWeight: FontWeight.w900,
              ),
            ),
          ),
      ],
    );

    if (isCompleted) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          header,
          const SizedBox(height: 8),
          Row(
            children: [
              Row(
                children: List.generate(
                  5,
                  (i) => Icon(
                    Icons.star,
                    size: 14,
                    color: theme.colorScheme.tertiary,
                  ),
                ),
              ),
              if (activity.imageUrl != null) ...[
                const SizedBox(width: 12),
                SizedBox(
                  width: 40,
                  height: 40,
                  child: AppImage(
                    activity.imageUrl!,
                    borderRadius: BorderRadius.circular(8),
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  'Photo captured',
                  style: theme.textTheme.bodySmall?.copyWith(
                    fontStyle: FontStyle.italic,
                    color: scheme.onSurfaceVariant,
                  ),
                ),
              ],
            ],
          ),
        ],
      );
    }

    if (isActive) {
      return Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: scheme.surfaceContainerLow,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: scheme.primary.withValues(alpha: 0.2)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            header,
            const SizedBox(height: 6),
            Text(activity.description, style: theme.textTheme.bodyMedium),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: FilledButton.icon(
                    onPressed: () => ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Photo added to trip journal.')),
                    ),
                    icon: const Icon(Icons.photo_camera, size: 16),
                    label: const Text('Take Photo'),
                    style: FilledButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 12),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        'RATE ACTIVITY',
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: scheme.onSurfaceVariant,
                        ),
                      ),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: List.generate(5, (i) {
                          final filled = i < _rating;
                          return GestureDetector(
                            onTap: () => setState(() => _rating = i + 1),
                            child: Icon(
                              filled ? Icons.star : Icons.star_border,
                              size: 18,
                              color: filled
                                  ? scheme.tertiary
                                  : scheme.outlineVariant,
                            ),
                          );
                        }),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ],
        ),
      );
    }

    // Upcoming.
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        header,
        if (activity.bookingReference != null) ...[
          const SizedBox(height: 10),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: scheme.secondaryContainer.withValues(alpha: 0.4),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: scheme.surfaceContainerLowest,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Icon(
                    Icons.confirmation_number_outlined,
                    color: scheme.tertiary,
                    size: 18,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        'BOOKING CONFIRMED',
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: scheme.onSecondaryContainer,
                        ),
                      ),
                      Text(
                        activity.bookingReference!,
                        style: theme.textTheme.titleSmall,
                      ),
                    ],
                  ),
                ),
                TextButton(
                  onPressed: () => ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('Booking changes coming soon.')),
                  ),
                  child: const Text('Change'),
                ),
              ],
            ),
          ),
        ],
      ],
    );
  }
}
