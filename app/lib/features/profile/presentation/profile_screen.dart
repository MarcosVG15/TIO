import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/widgets/async_value_view.dart';
import '../../../core/widgets/soft_card.dart';
import '../../../data/models/profile.dart';
import '../../../data/providers/providers.dart';

class ProfileScreen extends ConsumerWidget {
  const ProfileScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final profileAsync = ref.watch(profileControllerProvider);

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
      body: AsyncValueView<UserProfile>(
        value: profileAsync,
        data: (profile) => _ProfileBody(profile: profile),
      ),
    );
  }
}

class _ProfileBody extends ConsumerWidget {
  const _ProfileBody({required this.profile});

  final UserProfile profile;

  void _snack(BuildContext context, String message) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);

    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 120),
      children: [
        Center(
          child: Column(
            children: [
              Stack(
                children: [
                  CircleAvatar(
                    radius: 56,
                    backgroundColor: theme.colorScheme.surfaceContainerHigh,
                    backgroundImage: NetworkImage(profile.avatarUrl),
                  ),
                  Positioned(
                    right: 0,
                    bottom: 0,
                    child: CircleAvatar(
                      radius: 16,
                      backgroundColor: theme.colorScheme.tertiary,
                      child: const Icon(Icons.edit, size: 16, color: Colors.white),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Text(profile.name, style: theme.textTheme.displayMedium),
              const SizedBox(height: 4),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.verified, size: 16, color: theme.colorScheme.tertiary),
                  const SizedBox(width: 4),
                  Text(
                    '${profile.title} • Level ${profile.level}',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 28),
        LayoutBuilder(
          builder: (context, constraints) {
            final wide = constraints.maxWidth > 760;
            final settings = _AccountSettingsForm(
              profile: profile,
              onSaved: (name, email) {
                ref
                    .read(profileControllerProvider.notifier)
                    .save(profile.copyWith(name: name, email: email));
                _snack(context, 'Profile saved.');
              },
            );
            final preferences = _PreferencesCard(
              profile: profile,
              onSnack: (m) => _snack(context, m),
            );
            final recalibration = _RecalibrationCard(onSnack: (m) => _snack(context, m));

            if (!wide) {
              return Column(
                children: [
                  settings,
                  const SizedBox(height: 20),
                  preferences,
                  const SizedBox(height: 20),
                  recalibration,
                ],
              );
            }
            return Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  flex: 7,
                  child: Column(
                    children: [
                      settings,
                      const SizedBox(height: 20),
                      preferences,
                    ],
                  ),
                ),
                const SizedBox(width: 24),
                Expanded(flex: 5, child: recalibration),
              ],
            );
          },
        ),
        const SizedBox(height: 28),
        _DangerZone(onSnack: (m) => _snack(context, m)),
      ],
    );
  }
}

class _AccountSettingsForm extends StatefulWidget {
  const _AccountSettingsForm({required this.profile, required this.onSaved});

  final UserProfile profile;
  final void Function(String name, String email) onSaved;

  @override
  State<_AccountSettingsForm> createState() => _AccountSettingsFormState();
}

class _AccountSettingsFormState extends State<_AccountSettingsForm> {
  late final _nameController = TextEditingController(text: widget.profile.name);
  late final _emailController = TextEditingController(text: widget.profile.email);

  @override
  void dispose() {
    _nameController.dispose();
    _emailController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SoftCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Icon(Icons.manage_accounts_outlined, color: theme.colorScheme.primary),
              const SizedBox(width: 8),
              Text('Account Settings', style: theme.textTheme.headlineSmall),
            ],
          ),
          const SizedBox(height: 20),
          LayoutBuilder(
            builder: (context, constraints) {
              final twoUp = constraints.maxWidth > 420;
              final name = TextField(
                controller: _nameController,
                decoration: const InputDecoration(labelText: 'Full Name'),
              );
              final email = TextField(
                controller: _emailController,
                decoration: const InputDecoration(labelText: 'Email Address'),
              );
              if (!twoUp) {
                return Column(children: [name, const SizedBox(height: 16), email]);
              }
              return Row(
                children: [
                  Expanded(child: name),
                  const SizedBox(width: 16),
                  Expanded(child: email),
                ],
              );
            },
          ),
          const SizedBox(height: 16),
          TextField(
            obscureText: true,
            enabled: false,
            controller: TextEditingController(text: '••••••••••••'),
            decoration: const InputDecoration(labelText: 'Password'),
          ),
          const SizedBox(height: 20),
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton(
              onPressed: () =>
                  widget.onSaved(_nameController.text, _emailController.text),
              style: FilledButton.styleFrom(shape: const StadiumBorder()),
              child: const Text('Save Changes'),
            ),
          ),
        ],
      ),
    );
  }
}

class _PreferencesCard extends StatelessWidget {
  const _PreferencesCard({required this.profile, required this.onSnack});

  final UserProfile profile;
  final ValueChanged<String> onSnack;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Consumer(
      builder: (context, ref, _) {
        return SoftCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                children: [
                  Icon(Icons.settings_suggest_outlined, color: theme.colorScheme.primary),
                  const SizedBox(width: 8),
                  Text('App Preferences', style: theme.textTheme.headlineSmall),
                ],
              ),
              const SizedBox(height: 8),
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                secondary: const Icon(Icons.notifications_outlined),
                title: const Text('Push Notifications'),
                subtitle: const Text('Itinerary updates and AI tips'),
                value: profile.pushNotificationsEnabled,
                onChanged: (value) => ref
                    .read(profileControllerProvider.notifier)
                    .togglePushNotifications(value),
              ),
              const Divider(),
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.shield_outlined),
                title: const Text('Privacy & Visibility'),
                subtitle: const Text('Manage what your travel soul shares'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => onSnack('Privacy settings coming soon.'),
              ),
              const Divider(),
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.link),
                title: const Text('Linked Accounts'),
                subtitle: const Text('Google, Apple, and Social Profiles'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => onSnack('Linked accounts coming soon.'),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _RecalibrationCard extends StatelessWidget {
  const _RecalibrationCard({required this.onSnack});

  final ValueChanged<String> onSnack;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Consumer(
      builder: (context, ref, _) {
        return SoftCard(
          color: theme.colorScheme.surfaceContainerHigh,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 56,
                height: 56,
                decoration: BoxDecoration(
                  color: theme.colorScheme.primary.withValues(alpha: 0.1),
                  borderRadius: BorderRadius.circular(16),
                ),
                child: Icon(Icons.auto_fix_high, color: theme.colorScheme.primary),
              ),
              const SizedBox(height: 16),
              Text(
                'Recalibrate Your Travel Soul',
                style: theme.textTheme.headlineSmall,
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 8),
              Text(
                'Has your travel rhythm changed? Update your profile to help '
                'TIO find your next perfect match.',
                textAlign: TextAlign.center,
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 20),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: () => context.push('/onboarding'),
                  icon: const Icon(Icons.psychology_outlined),
                  label: const Text('Refine My Profile'),
                  style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 14),
                  ),
                ),
              ),
              const SizedBox(height: 10),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton.icon(
                  onPressed: () async {
                    final confirmed = await showDialog<bool>(
                      context: context,
                      builder: (context) => AlertDialog(
                        title: const Text('Reset & Start Over?'),
                        content: const Text(
                          'This will permanently erase your AI personalization '
                          'history.',
                        ),
                        actions: [
                          TextButton(
                            onPressed: () => Navigator.of(context).pop(false),
                            child: const Text('Cancel'),
                          ),
                          FilledButton(
                            onPressed: () => Navigator.of(context).pop(true),
                            child: const Text('Reset'),
                          ),
                        ],
                      ),
                    );
                    if (confirmed == true) {
                      final current = ref.read(profileControllerProvider).valueOrNull;
                      if (current != null) {
                        await ref
                            .read(profileControllerProvider.notifier)
                            .save(current.resetPersonalization());
                      }
                      onSnack('Personalization reset.');
                    }
                  },
                  icon: Icon(Icons.restart_alt, color: theme.colorScheme.error),
                  label: const Text('Reset & Start Over'),
                  style: OutlinedButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 14),
                  ),
                ),
              ),
              const SizedBox(height: 14),
              Text(
                'Resetting will permanently erase your AI personalization '
                'history.',
                textAlign: TextAlign.center,
                style: theme.textTheme.labelSmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant.withValues(alpha: 0.7),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _DangerZone extends StatelessWidget {
  const _DangerZone({required this.onSnack});

  final ValueChanged<String> onSnack;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 20),
      decoration: BoxDecoration(
        border: Border(top: BorderSide(color: theme.colorScheme.outlineVariant)),
      ),
      child: Column(
        children: [
          Text(
            'Danger Zone',
            style: theme.textTheme.headlineSmall?.copyWith(
              color: theme.colorScheme.error.withValues(alpha: 0.8),
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'Irreversible account actions and data deletion',
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
          const SizedBox(height: 16),
          OutlinedButton(
            onPressed: () async {
              final confirmed = await showDialog<bool>(
                context: context,
                builder: (context) => AlertDialog(
                  title: const Text('Deactivate Account?'),
                  content: const Text(
                    'Your profile and trips will be hidden until you sign back in.',
                  ),
                  actions: [
                    TextButton(
                      onPressed: () => Navigator.of(context).pop(false),
                      child: const Text('Cancel'),
                    ),
                    FilledButton(
                      onPressed: () => Navigator.of(context).pop(true),
                      style: FilledButton.styleFrom(
                        backgroundColor: theme.colorScheme.error,
                      ),
                      child: const Text('Deactivate'),
                    ),
                  ],
                ),
              );
              if (confirmed == true) onSnack('Account deactivated.');
            },
            style: OutlinedButton.styleFrom(
              foregroundColor: theme.colorScheme.error,
              side: BorderSide(color: theme.colorScheme.error),
            ),
            child: const Text('Deactivate Account'),
          ),
        ],
      ),
    );
  }
}
