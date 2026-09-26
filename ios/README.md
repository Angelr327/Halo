# Helmet Safety MVP

The iOS foundation lives in `ios/HelmetSafety.xcodeproj`. It is a SwiftUI MVVM application using mock helmet data; Raspberry Pi networking is intentionally not implemented yet.

## Build

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcodebuild -project ios/HelmetSafety.xcodeproj \
  -scheme HelmetSafety \
  -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/HelmetSafetyDerivedData \
  CODE_SIGNING_ALLOWED=NO build
```
