import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/widgets/app_image.dart';
import '../../../core/widgets/pill_button.dart';
import '../../../data/providers/providers.dart';

enum _WelcomeAction { newJourney, returning, google, apple }

class WelcomeScreen extends ConsumerStatefulWidget {
  const WelcomeScreen({super.key});

  @override
  ConsumerState<WelcomeScreen> createState() => _WelcomeScreenState();
}

class _WelcomeScreenState extends ConsumerState<WelcomeScreen> {
  _WelcomeAction? _pending;

  Future<void> _handle(_WelcomeAction action) async {
    setState(() => _pending = action);
    final auth = ref.read(authRepositoryProvider);
    try {
      switch (action) {
        case _WelcomeAction.newJourney:
          await auth.startNewJourney();
          if (mounted) context.go('/onboarding');
        case _WelcomeAction.returning:
          await auth.continueAsReturningUser();
          if (mounted) context.go('/home');
        case _WelcomeAction.google:
          await auth.signInWithGoogle();
          if (mounted) context.go('/home');
        case _WelcomeAction.apple:
          await auth.signInWithApple();
          if (mounted) context.go('/home');
      }
    } finally {
      if (mounted) setState(() => _pending = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final wide = MediaQuery.sizeOf(context).width >= 900;

    return Scaffold(
      body: Stack(
        fit: StackFit.expand,
        children: [
          const AppImage(DummyImages.amalfi, fit: BoxFit.cover),
          DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [
                  Colors.black.withValues(alpha: 0.25),
                  Colors.black.withValues(alpha: 0.15),
                  Colors.black.withValues(alpha: 0.75),
                ],
              ),
            ),
          ),
          SafeArea(
            child: LayoutBuilder(
              builder: (context, constraints) {
                return SingleChildScrollView(
                  padding: const EdgeInsets.symmetric(horizontal: 24),
                  child: ConstrainedBox(
                    constraints: BoxConstraints(minHeight: constraints.maxHeight),
                    child: IntrinsicHeight(
                      child: Column(
                        children: [
                          const _BrandRow(),
                          const SizedBox(height: 24),
                          Expanded(
                            child: wide
                                ? Row(
                                    crossAxisAlignment: CrossAxisAlignment.center,
                                    children: [
                                      Expanded(
                                        flex: 7,
                                        child: _HeroText(showStats: true),
                                      ),
                                      const SizedBox(width: 32),
                                      Expanded(
                                        flex: 5,
                                        child: _GetStartedCard(
                                          pending: _pending,
                                          onAction: _handle,
                                        ),
                                      ),
                                    ],
                                  )
                                : Column(
                                    mainAxisAlignment: MainAxisAlignment.center,
                                    crossAxisAlignment: CrossAxisAlignment.stretch,
                                    children: [
                                      const _HeroText(showStats: false),
                                      const SizedBox(height: 28),
                                      _GetStartedCard(
                                        pending: _pending,
                                        onAction: _handle,
                                      ),
                                    ],
                                  ),
                          ),
                          const SizedBox(height: 16),
                          const _FooterRow(),
                          const SizedBox(height: 12),
                        ],
                      ),
                    ),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _BrandRow extends StatelessWidget {
  const _BrandRow();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Row(
        children: [
          const Icon(Icons.explore_outlined, color: Colors.white, size: 28),
          const SizedBox(width: 8),
          Text(
            'TIO',
            style: Theme.of(context).textTheme.titleLarge?.copyWith(
              color: Colors.white,
              fontFamily: 'Literata',
            ),
          ),
        ],
      ),
    );
  }
}

class _HeroText extends StatelessWidget {
  const _HeroText({required this.showStats});

  final bool showStats;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          'Adventure awaits,',
          style: theme.textTheme.displayMedium?.copyWith(color: Colors.white),
        ),
        Text(
          'smarter than ever.',
          style: theme.textTheme.displayMedium?.copyWith(
            color: theme.colorScheme.tertiaryContainer,
          ),
        ),
        const SizedBox(height: 16),
        Text(
          'Your journey, uniquely yours. Let TIO craft the perfect '
          'itinerary tailored to your rhythm, budget, and curiosity.',
          style: theme.textTheme.bodyLarge?.copyWith(
            color: Colors.white.withValues(alpha: 0.9),
          ),
        ),
        if (showStats) ...[
          const SizedBox(height: 32),
          Row(
            children: const [
              _StatBlock(value: '120+', label: 'Countries'),
              _StatDivider(),
              _StatBlock(value: '50k+', label: 'Journeys'),
              _StatDivider(),
              _StatBlock(value: '4.9/5', label: 'Rating'),
            ],
          ),
        ],
      ],
    );
  }
}

class _StatDivider extends StatelessWidget {
  const _StatDivider();

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 40,
      width: 1,
      margin: const EdgeInsets.symmetric(horizontal: 20),
      color: Colors.white.withValues(alpha: 0.2),
    );
  }
}

class _StatBlock extends StatelessWidget {
  const _StatBlock({required this.value, required this.label});

  final String value;
  final String label;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          value,
          style: theme.textTheme.headlineMedium?.copyWith(color: Colors.white),
        ),
        Text(
          label.toUpperCase(),
          style: theme.textTheme.labelSmall?.copyWith(
            color: Colors.white.withValues(alpha: 0.7),
            letterSpacing: 1.2,
          ),
        ),
      ],
    );
  }
}

class _GetStartedCard extends StatelessWidget {
  const _GetStartedCard({required this.pending, required this.onAction});

  final _WelcomeAction? pending;
  final ValueChanged<_WelcomeAction> onAction;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ClipRRect(
      borderRadius: BorderRadius.circular(32),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 20, sigmaY: 20),
        child: Container(
          padding: const EdgeInsets.all(28),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.75),
            borderRadius: BorderRadius.circular(32),
            border: Border.all(color: Colors.white.withValues(alpha: 0.4)),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Get Started', style: theme.textTheme.headlineMedium),
              const SizedBox(height: 4),
              Text(
                'Join a global community of modern explorers.',
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 24),
              PillButton(
                label: 'Start Your Journey',
                icon: Icons.arrow_forward,
                loading: pending == _WelcomeAction.newJourney,
                onPressed: pending == null
                    ? () => onAction(_WelcomeAction.newJourney)
                    : null,
              ),
              const SizedBox(height: 12),
              PillButton(
                label: 'Welcome Back',
                icon: Icons.login,
                filled: false,
                loading: pending == _WelcomeAction.returning,
                onPressed: pending == null
                    ? () => onAction(_WelcomeAction.returning)
                    : null,
              ),
              const SizedBox(height: 24),
              Row(
                children: [
                  Expanded(child: Divider(color: theme.colorScheme.outlineVariant)),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: Text(
                      'OR CONTINUE WITH',
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ),
                  Expanded(child: Divider(color: theme.colorScheme.outlineVariant)),
                ],
              ),
              const SizedBox(height: 24),
              Row(
                children: [
                  Expanded(
                    child: _SocialButton(
                      label: 'Google',
                      icon: Icons.g_mobiledata_rounded,
                      loading: pending == _WelcomeAction.google,
                      onPressed: pending == null
                          ? () => onAction(_WelcomeAction.google)
                          : null,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _SocialButton(
                      label: 'Apple',
                      icon: Icons.apple,
                      loading: pending == _WelcomeAction.apple,
                      onPressed: pending == null
                          ? () => onAction(_WelcomeAction.apple)
                          : null,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 20),
              Text(
                "By continuing, you agree to TIO's Terms of Service and "
                'Privacy Policy.',
                textAlign: TextAlign.center,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SocialButton extends StatelessWidget {
  const _SocialButton({
    required this.label,
    required this.icon,
    required this.onPressed,
    this.loading = false,
  });

  final String label;
  final IconData icon;
  final VoidCallback? onPressed;
  final bool loading;

  @override
  Widget build(BuildContext context) {
    return OutlinedButton(
      onPressed: loading ? null : onPressed,
      style: OutlinedButton.styleFrom(
        backgroundColor: Colors.white,
        foregroundColor: Theme.of(context).colorScheme.onSurface,
        side: BorderSide(color: Theme.of(context).colorScheme.outlineVariant),
        padding: const EdgeInsets.symmetric(vertical: 14),
      ),
      child: loading
          ? const SizedBox(
              height: 16,
              width: 16,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(icon, size: 20),
                const SizedBox(width: 6),
                Text(label),
              ],
            ),
    );
  }
}

class _FooterRow extends StatelessWidget {
  const _FooterRow();

  @override
  Widget build(BuildContext context) {
    return Text(
      '© 2026 TIO Inc. · Designed for the modern explorer',
      textAlign: TextAlign.center,
      style: Theme.of(
        context,
      ).textTheme.labelSmall?.copyWith(color: Colors.white.withValues(alpha: 0.6)),
    );
  }
}
