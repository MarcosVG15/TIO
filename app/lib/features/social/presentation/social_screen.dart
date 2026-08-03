import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/widgets/app_image.dart';
import '../../../core/widgets/async_value_view.dart';
import '../../../core/widgets/pill_tag.dart';
import '../../../core/widgets/soft_card.dart';
import '../../../data/models/social.dart';
import '../../../data/providers/providers.dart';

class SocialScreen extends ConsumerStatefulWidget {
  const SocialScreen({super.key});

  @override
  ConsumerState<SocialScreen> createState() => _SocialScreenState();
}

class _SocialScreenState extends ConsumerState<SocialScreen> {
  String _filter = 'Trending';
  final Set<String> _likedPosts = {};
  String _friendQuery = '';

  void _snack(String message) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final feed = ref.watch(socialFeedProvider);
    final friends = ref.watch(friendsProvider);

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
      floatingActionButton: FloatingActionButton(
        onPressed: () => _snack('Create post coming soon.'),
        child: const Icon(Icons.add),
      ),
      body: LayoutBuilder(
        builder: (context, constraints) {
          final wide = constraints.maxWidth > 900;
          final feedColumn = _FeedColumn(
            feed: feed,
            filter: _filter,
            onFilterChanged: (f) => setState(() => _filter = f),
            likedPosts: _likedPosts,
            onToggleLike: (id) => setState(() {
              if (!_likedPosts.remove(id)) _likedPosts.add(id);
            }),
            onSnack: _snack,
          );
          final friendsPanel = _FriendsPanel(
            friends: friends,
            query: _friendQuery,
            onQueryChanged: (q) => setState(() => _friendQuery = q),
            onInvite: (name) => _snack('Invited $name to plan together.'),
          );

          final content = ListView(
            padding: EdgeInsets.fromLTRB(
              20,
              12,
              20,
              wide ? 32 : 120,
            ),
            children: [
              _GroupTravelHero(onTap: () => _snack('Group planning coming soon.')),
              const SizedBox(height: 24),
              if (wide)
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(flex: 8, child: feedColumn),
                    const SizedBox(width: 24),
                    Expanded(flex: 4, child: friendsPanel),
                  ],
                )
              else ...[
                feedColumn,
                const SizedBox(height: 28),
                friendsPanel,
              ],
            ],
          );

          return content;
        },
      ),
    );
  }
}

class _GroupTravelHero extends StatelessWidget {
  const _GroupTravelHero({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ClipRRect(
      borderRadius: BorderRadius.circular(20),
      child: SizedBox(
        height: 180,
        child: Stack(
          fit: StackFit.expand,
          children: [
            const AppImage(DummyImages.mountainPass),
            DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Colors.transparent, Colors.black.withValues(alpha: 0.65)],
                ),
              ),
            ),
            Positioned(
              left: 16,
              bottom: 16,
              right: 100,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    'Group Travel',
                    style: theme.textTheme.headlineMedium?.copyWith(color: Colors.white),
                  ),
                  Text(
                    'Combining preferences for the ultimate route',
                    style: theme.textTheme.bodySmall?.copyWith(color: Colors.white70),
                  ),
                ],
              ),
            ),
            Positioned(
              right: 16,
              bottom: 16,
              child: FilledButton.icon(
                onPressed: onTap,
                style: FilledButton.styleFrom(
                  backgroundColor: theme.colorScheme.tertiaryContainer,
                  foregroundColor: theme.colorScheme.onTertiaryContainer,
                  shape: const StadiumBorder(),
                ),
                icon: const Icon(Icons.arrow_forward, size: 16),
                label: const Text('Plan Together'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _FeedColumn extends StatelessWidget {
  const _FeedColumn({
    required this.feed,
    required this.filter,
    required this.onFilterChanged,
    required this.likedPosts,
    required this.onToggleLike,
    required this.onSnack,
  });

  final AsyncValue<List<SocialPost>> feed;
  final String filter;
  final ValueChanged<String> onFilterChanged;
  final Set<String> likedPosts;
  final ValueChanged<String> onToggleLike;
  final ValueChanged<String> onSnack;

  static const _filters = ['Trending', 'Following', 'Solo Routes', 'Group Hits'];

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        SizedBox(
          height: 40,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            itemCount: _filters.length,
            separatorBuilder: (_, _) => const SizedBox(width: 8),
            itemBuilder: (context, index) {
              final label = _filters[index];
              final selected = label == filter;
              return ChoiceChip(
                label: Text(label),
                selected: selected,
                onSelected: (_) => onFilterChanged(label),
                selectedColor: Theme.of(context).colorScheme.primary,
                labelStyle: TextStyle(
                  color: selected
                      ? Theme.of(context).colorScheme.onPrimary
                      : Theme.of(context).colorScheme.onSurfaceVariant,
                  fontWeight: FontWeight.w600,
                ),
                side: BorderSide.none,
                backgroundColor: Theme.of(context).colorScheme.surfaceContainerHigh,
              );
            },
          ),
        ),
        const SizedBox(height: 20),
        AsyncValueView<List<SocialPost>>(
          value: feed,
          data: (posts) => Column(
            children: [
              for (final post in posts) ...[
                _PostCard(
                  post: post,
                  liked: likedPosts.contains(post.id),
                  onToggleLike: () => onToggleLike(post.id),
                  onSnack: onSnack,
                ),
                const SizedBox(height: 16),
              ],
            ],
          ),
        ),
      ],
    );
  }
}

class _PostCard extends StatelessWidget {
  const _PostCard({
    required this.post,
    required this.liked,
    required this.onToggleLike,
    required this.onSnack,
  });

  final SocialPost post;
  final bool liked;
  final VoidCallback onToggleLike;
  final ValueChanged<String> onSnack;

  String _timeAgo(DateTime time) {
    final diff = DateTime.now().difference(time);
    if (diff.inHours < 1) return '${diff.inMinutes}m ago';
    if (diff.inHours < 24) return '${diff.inHours}h ago';
    return '${diff.inDays}d ago';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return SoftCard(
      padding: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                CircleAvatar(
                  radius: 20,
                  backgroundImage: NetworkImage(post.authorAvatarUrl),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(post.authorName, style: theme.textTheme.titleSmall),
                      Text(
                        '${_timeAgo(post.postedAt)} • ${post.location}',
                        style: theme.textTheme.bodySmall?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ),
                ),
                if (post.type == SocialPostType.itinerary)
                  const PillTag('Shared Route', dense: true),
              ],
            ),
            const SizedBox(height: 14),
            if (post.type == SocialPostType.photo)
              _PhotoPostBody(
                post: post,
                liked: liked,
                onToggleLike: onToggleLike,
                onSnack: onSnack,
              )
            else
              _ItineraryPostBody(post: post, onSnack: onSnack),
          ],
        ),
      ),
    );
  }
}

class _PhotoPostBody extends StatelessWidget {
  const _PhotoPostBody({
    required this.post,
    required this.liked,
    required this.onToggleLike,
    required this.onSnack,
  });

  final SocialPost post;
  final bool liked;
  final VoidCallback onToggleLike;
  final ValueChanged<String> onSnack;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final likeCount = post.likes + (liked ? 1 : 0);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        AspectRatio(
          aspectRatio: 4 / 3,
          child: AppImage(post.imageUrl!, borderRadius: BorderRadius.circular(14)),
        ),
        const SizedBox(height: 12),
        Text(post.caption ?? '', style: theme.textTheme.bodyMedium),
        const SizedBox(height: 12),
        Row(
          children: [
            _PostAction(
              icon: liked ? Icons.favorite : Icons.favorite_border,
              label: likeCount >= 1000
                  ? '${(likeCount / 1000).toStringAsFixed(1)}k'
                  : '$likeCount',
              color: liked ? theme.colorScheme.error : null,
              onTap: onToggleLike,
            ),
            const SizedBox(width: 20),
            _PostAction(
              icon: Icons.chat_bubble_outline,
              label: '${post.comments}',
              onTap: () => onSnack('Comments coming soon.'),
            ),
            const SizedBox(width: 20),
            _PostAction(
              icon: Icons.ios_share,
              label: 'Invite to Trip',
              onTap: () => onSnack('Invite sent.'),
            ),
          ],
        ),
      ],
    );
  }
}

class _PostAction extends StatelessWidget {
  const _PostAction({
    required this.icon,
    required this.label,
    required this.onTap,
    this.color,
  });

  final IconData icon;
  final String label;
  final VoidCallback onTap;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 20, color: color ?? theme.colorScheme.onSurfaceVariant),
            const SizedBox(width: 6),
            Text(
              label,
              style: theme.textTheme.labelMedium?.copyWith(
                color: color ?? theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ItineraryPostBody extends StatelessWidget {
  const _ItineraryPostBody({required this.post, required this.onSnack});

  final SocialPost post;
  final ValueChanged<String> onSnack;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final steps = post.itinerarySteps;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(post.itineraryTitle ?? '', style: theme.textTheme.headlineSmall),
        const SizedBox(height: 14),
        for (var i = 0; i < steps.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 14),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Column(
                  children: [
                    Container(
                      width: 12,
                      height: 12,
                      decoration: BoxDecoration(
                        color: theme.colorScheme.primary,
                        shape: BoxShape.circle,
                      ),
                    ),
                    if (i != steps.length - 1)
                      Container(
                        width: 2,
                        height: 32,
                        color: theme.colorScheme.primary.withValues(alpha: 0.3),
                      ),
                  ],
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(steps[i].title, style: theme.textTheme.titleSmall),
                      Text(
                        steps[i].subtitle,
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
        const Divider(),
        Row(
          children: [
            SizedBox(
              height: 32,
              child: Stack(
                children: [
                  for (var i = 0; i < post.contributorAvatarUrls.length; i++)
                    Padding(
                      padding: EdgeInsets.only(left: i * 22.0),
                      child: CircleAvatar(
                        radius: 16,
                        backgroundColor: theme.colorScheme.surface,
                        child: CircleAvatar(
                          radius: 14,
                          backgroundImage: NetworkImage(
                            post.contributorAvatarUrls[i],
                          ),
                        ),
                      ),
                    ),
                  Padding(
                    padding: EdgeInsets.only(
                      left: post.contributorAvatarUrls.length * 22.0,
                    ),
                    child: CircleAvatar(
                      radius: 16,
                      backgroundColor: theme.colorScheme.surfaceContainerHighest,
                      child: const Text('+12', style: TextStyle(fontSize: 10)),
                    ),
                  ),
                ],
              ),
            ),
            const Spacer(),
            TextButton(
              onPressed: () => onSnack('Full itinerary coming soon.'),
              child: const Text('View Itinerary Details'),
            ),
          ],
        ),
      ],
    );
  }
}

class _FriendsPanel extends StatelessWidget {
  const _FriendsPanel({
    required this.friends,
    required this.query,
    required this.onQueryChanged,
    required this.onInvite,
  });

  final AsyncValue<List<Friend>> friends;
  final String query;
  final ValueChanged<String> onQueryChanged;
  final ValueChanged<String> onInvite;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return SoftCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Friends', style: theme.textTheme.headlineSmall),
              Icon(Icons.person_add_alt, color: theme.colorScheme.onSurfaceVariant),
            ],
          ),
          const SizedBox(height: 12),
          TextField(
            onChanged: onQueryChanged,
            decoration: const InputDecoration(
              hintText: 'Search friends...',
              prefixIcon: Icon(Icons.search),
              isDense: true,
            ),
          ),
          const SizedBox(height: 12),
          AsyncValueView<List<Friend>>(
            value: friends,
            data: (list) {
              final filtered = query.isEmpty
                  ? list
                  : list
                      .where(
                        (f) => f.name.toLowerCase().contains(query.toLowerCase()),
                      )
                      .toList();
              if (filtered.isEmpty) {
                return Padding(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  child: Text(
                    'No friends match "$query".',
                    style: theme.textTheme.bodySmall,
                  ),
                );
              }
              return Column(
                children: [for (final f in filtered) _FriendTile(friend: f, onInvite: onInvite)],
              );
            },
          ),
          const SizedBox(height: 8),
          SizedBox(
            width: double.infinity,
            child: OutlinedButton.icon(
              onPressed: () => onInvite('your contacts'),
              icon: const Icon(Icons.group_add_outlined),
              label: const Text('Invite More Friends'),
            ),
          ),
        ],
      ),
    );
  }
}

class _FriendTile extends StatelessWidget {
  const _FriendTile({required this.friend, required this.onInvite});

  final Friend friend;
  final ValueChanged<String> onInvite;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final (Color dotColor, String statusText) = switch (friend.status) {
      FriendStatus.activeNow => (Colors.green, 'Active now'),
      FriendStatus.readyToTravel => (Colors.blue, 'Ready to travel'),
      FriendStatus.away => (
          theme.colorScheme.outlineVariant,
          'Away${friend.statusDetail != null ? ' • ${friend.statusDetail}' : ''}',
        ),
    };

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        children: [
          Stack(
            children: [
              CircleAvatar(radius: 22, backgroundImage: NetworkImage(friend.avatarUrl)),
              Positioned(
                right: 0,
                bottom: 0,
                child: Container(
                  width: 12,
                  height: 12,
                  decoration: BoxDecoration(
                    color: dotColor,
                    shape: BoxShape.circle,
                    border: Border.all(color: Colors.white, width: 2),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(friend.name, style: theme.textTheme.titleSmall),
                Text(
                  statusText,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
          IconButton(
            onPressed: () => onInvite(friend.name),
            icon: const Icon(Icons.flight_takeoff),
            color: theme.colorScheme.primary,
          ),
        ],
      ),
    );
  }
}
