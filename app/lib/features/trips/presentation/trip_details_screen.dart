import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../../core/utils/icon_mapper.dart';
import '../../../core/widgets/app_image.dart';
import '../../../core/widgets/async_value_view.dart';
import '../../../core/widgets/pill_tag.dart';
import '../../../core/widgets/soft_card.dart';
import '../../../data/models/trip.dart';
import '../../../data/providers/providers.dart';

class TripDetailsScreen extends ConsumerWidget {
  const TripDetailsScreen({super.key, required this.tripId});

  final String tripId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tripAsync = ref.watch(tripByIdProvider(tripId));

    return Scaffold(
      body: AsyncValueView<Trip?>(
        value: tripAsync,
        data: (trip) {
          if (trip == null) {
            return const Center(child: Text('Trip not found.'));
          }
          return _TripDetailsBody(trip: trip);
        },
      ),
    );
  }
}

class _TripDetailsBody extends StatelessWidget {
  const _TripDetailsBody({required this.trip});

  final Trip trip;

  String get _statusLabel => switch (trip.status) {
    TripStatus.completed => 'Completed',
    TripStatus.active => 'In Progress',
    TripStatus.upcoming => 'Upcoming',
  };

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final dateFormat = DateFormat('MMM d');

    return CustomScrollView(
      slivers: [
        SliverAppBar(
          expandedHeight: 340,
          pinned: true,
          backgroundColor: theme.colorScheme.surface,
          surfaceTintColor: Colors.transparent,
          flexibleSpace: FlexibleSpaceBar(
            background: Stack(
              fit: StackFit.expand,
              children: [
                AppImage(trip.coverImageUrl),
                const DecoratedBox(
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.topCenter,
                      end: Alignment.bottomCenter,
                      colors: [Colors.transparent, Colors.black87],
                    ),
                  ),
                ),
                Positioned(
                  left: 20,
                  right: 20,
                  bottom: 20,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      PillTag(_statusLabel.toUpperCase()),
                      const SizedBox(height: 10),
                      Text(
                        trip.destination,
                        style: theme.textTheme.displayMedium?.copyWith(
                          color: Colors.white,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Row(
                        children: [
                          const Icon(
                            Icons.calendar_month,
                            color: Colors.white70,
                            size: 16,
                          ),
                          const SizedBox(width: 6),
                          Text(
                            '${dateFormat.format(trip.startDate)} — '
                            '${dateFormat.format(trip.endDate)}, '
                            '${trip.endDate.year}',
                            style: theme.textTheme.bodyMedium?.copyWith(
                              color: Colors.white70,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
        SliverToBoxAdapter(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 20, 20, 120),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (trip.status == TripStatus.active) ...[
                  _LiveNowBanner(tripId: trip.id),
                  const SizedBox(height: 24),
                ],
                LayoutBuilder(
                  builder: (context, constraints) {
                    final wide = constraints.maxWidth > 760;
                    final sidebar = _Sidebar(trip: trip);
                    final itinerary = _ItinerarySection(trip: trip);

                    if (!wide) {
                      return Column(
                        children: [
                          sidebar,
                          const SizedBox(height: 24),
                          itinerary,
                        ],
                      );
                    }
                    return Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(flex: 4, child: sidebar),
                        const SizedBox(width: 24),
                        Expanded(flex: 8, child: itinerary),
                      ],
                    );
                  },
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _LiveNowBanner extends StatelessWidget {
  const _LiveNowBanner({required this.tripId});

  final String tripId;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Material(
      color: scheme.primary,
      borderRadius: BorderRadius.circular(16),
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: () => context.push('/trips/$tripId/live'),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
          child: Row(
            children: [
              const Icon(Icons.sensors, color: Colors.white),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  'This trip is happening right now — open the live timeline',
                  style: Theme.of(
                    context,
                  ).textTheme.titleSmall?.copyWith(color: Colors.white),
                ),
              ),
              const Icon(Icons.arrow_forward, color: Colors.white),
            ],
          ),
        ),
      ),
    );
  }
}

class _Sidebar extends StatelessWidget {
  const _Sidebar({required this.trip});

  final Trip trip;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (trip.accommodation != null)
          SoftCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  children: [
                    Icon(Icons.bed_outlined, color: theme.colorScheme.primary),
                    const SizedBox(width: 8),
                    Text('Stay', style: theme.textTheme.headlineSmall),
                  ],
                ),
                const SizedBox(height: 12),
                AspectRatio(
                  aspectRatio: 16 / 10,
                  child: AppImage(
                    trip.accommodation!.imageUrl,
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
                const SizedBox(height: 10),
                Text(
                  trip.accommodation!.name,
                  style: theme.textTheme.titleMedium?.copyWith(
                    color: theme.colorScheme.primary,
                  ),
                ),
                Text(
                  trip.accommodation!.description,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
        if (trip.foodSpots.isNotEmpty) ...[
          const SizedBox(height: 16),
          SoftCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  children: [
                    Icon(
                      Icons.restaurant_outlined,
                      color: theme.colorScheme.primary,
                    ),
                    const SizedBox(width: 8),
                    Text('Taste', style: theme.textTheme.headlineSmall),
                  ],
                ),
                const SizedBox(height: 12),
                for (final spot in trip.foodSpots)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Row(
                      children: [
                        SizedBox(
                          width: 48,
                          height: 48,
                          child: AppImage(
                            spot.imageUrl,
                            borderRadius: BorderRadius.circular(10),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(
                                spot.name,
                                style: theme.textTheme.titleSmall?.copyWith(
                                  color: theme.colorScheme.primary,
                                ),
                              ),
                              Text(
                                spot.description,
                                style: theme.textTheme.bodySmall?.copyWith(
                                  color: theme.colorScheme.onSurfaceVariant,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ],
      ],
    );
  }
}

class _ItinerarySection extends StatelessWidget {
  const _ItinerarySection({required this.trip});

  final Trip trip;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (trip.itinerary.isEmpty) {
      return SoftCard(
        child: Text(
          'No itinerary yet — this trip is still being planned.',
          style: theme.textTheme.bodyMedium,
        ),
      );
    }

    return SoftCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Itinerary', style: theme.textTheme.headlineMedium),
              TextButton.icon(
                onPressed: () => ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Export PDF coming soon.')),
                ),
                icon: const Icon(Icons.download_outlined, size: 18),
                label: const Text('Export PDF'),
              ),
            ],
          ),
          const SizedBox(height: 20),
          for (final day in trip.itinerary) ...[
            _DayBlock(day: day),
            if (day != trip.itinerary.last) const SizedBox(height: 28),
          ],
        ],
      ),
    );
  }
}

class _DayBlock extends StatelessWidget {
  const _DayBlock({required this.day});

  final ItineraryDay day;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Column(
          children: [
            Container(
              width: 28,
              height: 28,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: theme.colorScheme.primary,
                shape: BoxShape.circle,
              ),
              child: Text(
                '${day.dayNumber}',
                style: theme.textTheme.labelMedium?.copyWith(
                  color: Colors.white,
                ),
              ),
            ),
            Expanded(
              child: Container(
                width: 2,
                margin: const EdgeInsets.symmetric(vertical: 4),
                color: theme.colorScheme.outlineVariant,
              ),
            ),
          ],
        ),
        const SizedBox(width: 16),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(day.title, style: theme.textTheme.headlineSmall),
              Text(
                day.subtitle,
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 16),
              for (final activity in day.activities)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: _ActivityCard(activity: activity),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

class _ActivityCard extends StatelessWidget {
  const _ActivityCard({required this.activity});

  final ItineraryActivity activity;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerLow,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: theme.colorScheme.primary.withValues(alpha: 0.12),
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 56,
            child: Text(
              activity.time,
              style: theme.textTheme.labelMedium?.copyWith(
                color: theme.colorScheme.primary,
              ),
            ),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  children: [
                    Icon(
                      iconForKey(activity.icon),
                      size: 16,
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                    const SizedBox(width: 6),
                    Text(activity.title, style: theme.textTheme.titleSmall),
                  ],
                ),
                const SizedBox(height: 4),
                Text(activity.description, style: theme.textTheme.bodyMedium),
                if (activity.imageUrl != null) ...[
                  const SizedBox(height: 10),
                  AspectRatio(
                    aspectRatio: 16 / 9,
                    child: AppImage(
                      activity.imageUrl!,
                      borderRadius: BorderRadius.circular(10),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}
