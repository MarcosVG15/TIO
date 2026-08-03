import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/utils/icon_mapper.dart';
import '../../../core/widgets/app_image.dart';
import '../../../core/widgets/soft_card.dart';
import '../../../data/providers/providers.dart';
import '../../../data/repositories/mock_data.dart';

class OnboardingScreen extends ConsumerStatefulWidget {
  const OnboardingScreen({super.key});

  @override
  ConsumerState<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends ConsumerState<OnboardingScreen> {
  final Set<String> _selectedPersonalities = {};
  String? _selectedRhythm;
  bool _submitting = false;

  Future<void> _continue() async {
    setState(() => _submitting = true);
    try {
      await ref
          .read(authRepositoryProvider)
          .submitOnboarding(
            travelPersonalities: _selectedPersonalities.toList(),
            travelRhythm: _selectedRhythm,
          );
      if (mounted) context.go('/home');
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(
        leadingWidth: 0,
        leading: const SizedBox.shrink(),
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.explore, color: theme.colorScheme.primary),
            const SizedBox(width: 8),
            Text('TIO', style: theme.textTheme.titleLarge),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => context.go('/home'),
            child: const Row(
              mainAxisSize: MainAxisSize.min,
              children: [Text('Skip'), Icon(Icons.chevron_right)],
            ),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 720),
            child: ListView(
              padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
              children: [
                _ProgressHeader(theme: theme),
                const SizedBox(height: 24),
                SoftCard(
                  padding: const EdgeInsets.all(24),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Which describes your travel soul?',
                        style: theme.textTheme.headlineMedium,
                      ),
                      const SizedBox(height: 8),
                      Text(
                        'We use this to curate your vibe. Are you a museum '
                        'maven, a rave enthusiast, or a luxury seeker?',
                        style: theme.textTheme.bodyLarge?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant,
                        ),
                      ),
                      const SizedBox(height: 24),
                      LayoutBuilder(
                        builder: (context, constraints) {
                          final columns = constraints.maxWidth > 480 ? 2 : 1;
                          return GridView.count(
                            crossAxisCount: columns,
                            shrinkWrap: true,
                            physics: const NeverScrollableScrollPhysics(),
                            mainAxisSpacing: 12,
                            crossAxisSpacing: 12,
                            childAspectRatio: columns == 2 ? 2.6 : 3.6,
                            children: [
                              for (final p in MockData.travelPersonalities)
                                _PersonalityChip(
                                  title: p.title,
                                  description: p.description,
                                  icon: iconForKey(p.icon),
                                  selected: _selectedPersonalities.contains(
                                    p.title,
                                  ),
                                  onTap: () => setState(() {
                                    if (!_selectedPersonalities.remove(
                                      p.title,
                                    )) {
                                      _selectedPersonalities.add(p.title);
                                    }
                                  }),
                                ),
                            ],
                          );
                        },
                      ),
                      const SizedBox(height: 24),
                      Divider(color: theme.colorScheme.outlineVariant),
                      const SizedBox(height: 20),
                      Text(
                        'Choose your travel rhythm:',
                        style: theme.textTheme.titleMedium,
                      ),
                      const SizedBox(height: 12),
                      Wrap(
                        spacing: 10,
                        runSpacing: 10,
                        children: [
                          for (final rhythm in MockData.travelRhythms)
                            ChoiceChip(
                              label: Text(rhythm),
                              selected: _selectedRhythm == rhythm,
                              onSelected: (_) =>
                                  setState(() => _selectedRhythm = rhythm),
                              selectedColor: theme.colorScheme.primary,
                              labelStyle: TextStyle(
                                color: _selectedRhythm == rhythm
                                    ? theme.colorScheme.onPrimary
                                    : theme.colorScheme.onSurface,
                                fontWeight: FontWeight.w600,
                              ),
                              side: BorderSide(
                                color: _selectedRhythm == rhythm
                                    ? theme.colorScheme.primary
                                    : theme.colorScheme.outlineVariant,
                              ),
                            ),
                        ],
                      ),
                      const SizedBox(height: 32),
                      Row(
                        children: [
                          TextButton.icon(
                            onPressed: _submitting
                                ? null
                                : () => context.pop(),
                            icon: const Icon(Icons.arrow_back),
                            label: const Text('Previous'),
                          ),
                          const Spacer(),
                          FilledButton.icon(
                            onPressed: _submitting ? null : _continue,
                            icon: _submitting
                                ? const SizedBox(
                                    height: 16,
                                    width: 16,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                      color: Colors.white,
                                    ),
                                  )
                                : const Icon(Icons.arrow_forward),
                            label: Text(_submitting ? 'Saving...' : 'Continue'),
                            style: FilledButton.styleFrom(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 24,
                                vertical: 16,
                              ),
                              shape: const StadiumBorder(),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 24),
                Row(
                  children: const [
                    Expanded(
                      child: _DecorativeThumb(url: DummyImages.tuscany),
                    ),
                    SizedBox(width: 10),
                    Expanded(child: _DecorativeThumb(url: DummyImages.museum)),
                    SizedBox(width: 10),
                    Expanded(
                      child: _DecorativeThumb(url: DummyImages.nightCity),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _ProgressHeader extends StatelessWidget {
  const _ProgressHeader({required this.theme});

  final ThemeData theme;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              'STEP 2 OF 5',
              style: theme.textTheme.labelMedium?.copyWith(
                color: theme.colorScheme.primary,
                letterSpacing: 1.2,
              ),
            ),
            Text(
              'Getting to know you',
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        ClipRRect(
          borderRadius: BorderRadius.circular(999),
          child: LinearProgressIndicator(
            value: 0.4,
            minHeight: 4,
            backgroundColor: theme.colorScheme.surfaceContainerHighest,
            color: theme.colorScheme.primary,
          ),
        ),
      ],
    );
  }
}

class _PersonalityChip extends StatelessWidget {
  const _PersonalityChip({
    required this.title,
    required this.description,
    required this.icon,
    required this.selected,
    required this.onTap,
  });

  final String title;
  final String description;
  final IconData icon;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(16),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: selected
              ? scheme.secondaryContainer.withValues(alpha: 0.5)
              : scheme.surfaceContainerLowest,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: selected ? scheme.primary : scheme.outlineVariant,
            width: selected ? 1.5 : 1,
          ),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 44,
              height: 44,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: selected
                    ? scheme.secondaryContainer
                    : scheme.surfaceContainer,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Icon(
                icon,
                color: selected
                    ? scheme.onSecondaryContainer
                    : scheme.onSurfaceVariant,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    title,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    description,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: scheme.onSurfaceVariant,
                    ),
                    maxLines: 2,
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

class _DecorativeThumb extends StatelessWidget {
  const _DecorativeThumb({required this.url});

  final String url;

  @override
  Widget build(BuildContext context) {
    return AspectRatio(
      aspectRatio: 1.3,
      child: AppImage(
        url,
        borderRadius: BorderRadius.circular(16),
      ),
    );
  }
}
