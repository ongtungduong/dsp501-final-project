import 'package:flutter/material.dart';

import 'screens/home_screen.dart';

void main() {
  runApp(const ShazamApp());
}

class ShazamApp extends StatelessWidget {
  const ShazamApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Shazam',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.indigo),
        useMaterial3: true,
      ),
      home: const HomeScreen(),
    );
  }
}
