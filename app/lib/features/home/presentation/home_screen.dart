import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../../core/widgets/app_image.dart';
import '../../../core/widgets/async_value_view.dart';
import '../../../core/widgets/pill_tag.dart';
import '../../../core/widgets/section_header.dart';
import '../../../core/widgets/soft_card.dart';
import '../../../data/models/recommendation.dart';
import '../../../data/models/trip.dart';
import '../../../data/providers/providers.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profileAsync = ref.watch(profileControllerProvider);
    final pastTrips = ref.watch(pastTripsProvider);
    final recommendations = ref.watch(recommendationsProvider);
    final theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.travel_explore, color: theme.colorScheme.primary),
            const SizedBox(width: 8),
            const Text('TIO'),
          ],
        ),
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(pastTripsProvider);
          ref.invalidate(recommendationsProvider);
        },
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 120),
          children: [
            Text(
              'Welcome back, ${profileAsync.valueOrNull?.name.split(' ').first ?? 'Explorer'}',
              style: theme.textTheme.headlineMedium,
            ),
            const SizedBox(height: 4),
            Text(
              'Your next adventure is just a prompt away. Where to next?',
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
            const SizedBox(height: 20),
            _PlanNewTripCard(
              onTap: () => _comingSoon(context, 'Trip planning'),
            ),
            const SizedBox(height: 32),
            SectionHeader(
              title: 'Past Trips',
              icon: Icons.history,
              actionLabel: 'View All',
              onAction: () => context.go('/trips'),
            ),
            const SizedBox(height: 12),
            SizedBox(
              height: 254,
              child: AsyncValueView<List<Trip>>(
                value: pastTrips,
                data: (trips) => ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: trips.length,
                  separatorBuilder: (_, _) => const SizedBox(width: 16),
                  itemBuilder: (context, index) =>
                      _PastTripCard(trip: trips[index]),
                ),
              ),
            ),
            const SizedBox(height: 32),
            SectionHeader(
              title: 'Potential New Destinations',
              icon: Icons.auto_awesome,
            ),
            const SizedBox(height: 12),
            AsyncValueView<List<Recommendation>>(
              value: recommendations,
              data: (items) => _RecommendationBento(items: items),
            ),
            const SizedBox(height: 32),
            _AgentChatPreview(onTap: () => _comingSoon(context, 'AI chat')),
          ],
        ),
      ),
    );
  }

  void _comingSoon(BuildContext context, String feature) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('$feature is coming soon.')),
    );
  }
}

class _PlanNewTripCard extends StatelessWidget {
  const _PlanNewTripCard({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Material(
      color: scheme.secondaryContainer,
      borderRadius: BorderRadius.circular(20),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(20),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      'Plan New Trip',
                      style: Theme.of(context).textTheme.headlineSmall
                          ?.copyWith(color: scheme.onSecondaryContainer),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'Start your next journey with AI assistance',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: scheme.onSecondaryContainer.withValues(
                          alpha: 0.8,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              Container(
                width: 48,
                height: 48,
                decoration: BoxDecoration(
                  color: scheme.secondary,
                  shape: BoxShape.circle,
                ),
                child: Icon(
                  Icons.add_location_alt_outlined,
                  color: scheme.onSecondary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _PastTripCard extends StatelessWidget {
  const _PastTripCard({required this.trip});

  final Trip trip;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final dateLabel = DateFormat('MMM yyyy').format(trip.startDate);

    return SizedBox(
      width: 260,
      child: Material(
        color: theme.colorScheme.surfaceContainerLow,
        borderRadius: BorderRadius.circular(16),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: () => context.push('/trips/${trip.id}'),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              SizedBox(
                height: 130,
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    AppImage(trip.coverImageUrl),
                    Positioned(
                      top: 8,
                      right: 8,
                      child: PillTag('Completed', dense: true),
                    ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.all(14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      trip.destination,
                      style: theme.textTheme.titleMedium,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 2),
                    Text(
                      '$dateLabel • ${trip.days} days',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 8),
                    PillTag(trip.category),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _RecommendationBento extends StatelessWidget {
  const _RecommendationBento({required this.items});

  final List<Recommendation> items;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) return const SizedBox.shrink();
    final top = items.first;
    final rest = items.skip(1).toList();

    return Column(
      children: [
        _RecommendationTile(item: top, height: 220, big: true),
        if (rest.isNotEmpty) ...[
          const SizedBox(height: 12),
          LayoutBuilder(
            builder: (context, constraints) {
              return Wrap(
                spacing: 12,
                runSpacing: 12,
                children: [
                  for (final r in rest)
                    SizedBox(
                      width: (constraints.maxWidth - 12) / 2,
                      child: _RecommendationTile(item: r, height: 150),
                    ),
                ],
              );
            },
          ),
        ],
      ],
    );
  }
}

class _RecommendationTile extends StatelessWidget {
  const _RecommendationTile({
    required this.item,
    required this.height,
    this.big = false,
  });

  final Recommendation item;
  final double height;
  final bool big;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ClipRRect(
      borderRadius: BorderRadius.circular(20),
      child: SizedBox(
        height: height,
        width: double.infinity,
        child: Stack(
          fit: StackFit.expand,
          children: [
            AppImage(item.imageUrl),
            DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [
                    Colors.transparent,
                    Colors.black.withValues(alpha: big ? 0.75 : 0.55),
                  ],
                ),
              ),
            ),
            Positioned(
              left: 16,
              right: 16,
              bottom: 14,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (item.isTopPick) ...[
                    const PillTag(
                      'TOP PICK',
                      background: Color(0xFF4A7C59),
                      foreground: Colors.white,
                      dense: true,
                    ),
                    const SizedBox(height: 8),
                  ],
                  Text(
                    item.destination,
                    style:
                        (big
                                ? theme.textTheme.headlineMedium
                                : theme.textTheme.titleMedium)
                            ?.copyWith(color: Colors.white),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  if (big) ...[
                    const SizedBox(height: 4),
                    Text(
                      item.tagline,
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: Colors.white.withValues(alpha: 0.85),
                      ),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ] else
                    Text(
                      item.tagline,
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: Colors.white.withValues(alpha: 0.85),
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _AgentChatPreview extends StatelessWidget {
  const _AgentChatPreview({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SoftCard(
      border: true,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 48,
              height: 48,
              decoration: BoxDecoration(
                color: theme.colorScheme.primary,
                borderRadius: BorderRadius.circular(14),
              ),
              child: const Icon(
                Icons.auto_awesome,
                color: Colors.white,
              ),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    'Continue Trip Planning',
                    style: theme.textTheme.titleMedium?.copyWith(
                      color: theme.colorScheme.primary,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: theme.colorScheme.surfaceContainerLow,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(
                      '"I\'ve found three boutique hotels in Amalfi that '
                      'match your preference for sea views. Would you like '
                      'to see the price comparison?"',
                      style: theme.textTheme.bodySmall?.copyWith(
                        fontStyle: FontStyle.italic,
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '3 items waiting for your review',
                    style: theme.textTheme.labelSmall?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant.withValues(
                        alpha: 0.7,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            CircleAvatar(
              backgroundColor: theme.colorScheme.primary,
              foregroundColor: Colors.white,
              child: const Icon(Icons.send, size: 18),
            ),
          ],
        ),
      ),
    );
  }
}
