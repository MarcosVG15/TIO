// Basic smoke test: the app boots to the Welcome screen without throwing.

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:tio_app/app.dart';

void main() {
  testWidgets('App boots to the Welcome screen', (WidgetTester tester) async {
    await tester.pumpWidget(
      const ProviderScope(child: TioApp()),
    );
    await tester.pump();

    expect(find.text('Start Your Journey'), findsOneWidget);
  });
}
