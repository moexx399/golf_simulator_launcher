# Changelog

All notable changes to the golf simulator launcher are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Entries are managed with [changie](https://changie.dev): add one with `changie new`.

## v0.2.0 - 2026-11-15
### Added
* Pureplay recipe with a Driving Range shortcut (Practice, Choose a range, Mountain Range, Start Range Session), replacing the placeholder
* waitText step and clickText confirmText option, for games that ignore the first click or keep the clicked text on screen
### Dependencies
* Update pyside6-essentials requirement from <7,>=6.6 to >=6.12.0,<7
* Bump actions/setup-go from 6 to 7
* Update psutil requirement from >=5.9 to >=7.2.2

## v0.1.0 - 2026-11-02
### Added
* Prebuilt macOS and Linux downloads and a Homebrew formula: brew install moexx399/tap/golf_simulator_launcher
* Linux and macOS support: a Python launcher using the same config and step types as the Windows version
