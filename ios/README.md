# Helmet Safety

Helmet Safety is a hackathon MVP for a connected cycling helmet and companion iPhone app. The product combines activity tracking, navigation, incident history, and local Guardian monitoring with helmet-managed directional hazard warnings.

This repository currently contains the iOS application. It runs without a Raspberry Pi through a built-in helmet simulator. Raspberry Pi networking, computer vision, video capture, Gemini, ElevenLabs, and physical helmet control are planned integrations and are not implemented yet.

> **Safety notice:** This is a prototype, not a certified safety device. Do not rely on it for collision avoidance, emergency response, medical monitoring, or navigation in hazardous conditions. The current Guardian workflow does not contact emergency services or send real messages.

## Current status

Implemented:

- SwiftUI iPhone application using an MVVM-oriented structure
- Adaptive light/dark visual system and athletic orange accent
- Pre-ride setup, active navigation, and post-ride summary states
- CoreLocation position, speed, and heading updates
- CoreMotion device-motion sampling
- MapKit destination search and route calculation
- Route polyline, current location, destination, and incident pins
- Simplified navigation maneuvers for future helmet display
- Mock helmet state and developer simulation scenarios
- Persistent safety-event history
- Ride-session tracking and local completed-ride storage
- Guardian monitoring and simulated emergency check-in
- Safety analytics, incident feed, and event details

Not implemented:

- Raspberry Pi software or WebSocket communication
- Cameras, ultrasonic sensors, buzzers, LEDs, vibration, or OLED control
- Computer-vision object detection and tracking
- Real incident-video transfer and playback
- Automatic emergency calls or contact notifications
- Gemini or ElevenLabs integration
- Cloud synchronization or remote Guardian tracking
- Production-grade cycling routing

## Product boundaries

The intended system deliberately splits work between the helmet and phone.

### Helmet / Raspberry Pi

The helmet will eventually own the low-latency physical safety loop:

- Camera and ultrasonic-sensor acquisition
- Object detection and tracking
- Immediate left/right hazard classification
- Directional buzzers, LEDs, haptics, and OLED warnings
- Rolling incident-video buffers
- Basic safety behavior when the phone is unavailable

### iPhone

The phone owns rider-facing and internet-enabled features:

- GPS, speed, heading, and ride tracking
- Maps and route calculation
- Ride setup and live navigation
- Safety summaries and incident history
- Guardian monitoring
- Future AI and voice services
- Future communication with the helmet

The mobile UI intentionally does **not** show live left/right hazard warnings. Those warnings are time-sensitive and belong on the helmet hardware. The app shows overall safety state, counts, history, and post-ride insights.

## Engineering rationale

This section records design decisions and tradeoffs. It is an architectural explanation, not a transcript of private chain-of-thought.

### Protocol-oriented services

Platform and hardware dependencies are exposed through protocols:

- `HelmetDataProviding`
- `HelmetSimulationProviding`
- `LocationProviding`
- `MotionProviding`
- `NavigationProviding`
- `SafetyEventStoring`
- `GuardianProviding`

SwiftUI screens depend on view models; view models depend on service contracts. A future WebSocket helmet client can replace `MockHelmetService` without rewriting the Ride interface.

### Shared state sources

`AppViewModel` creates and injects shared services. Ride, Guardian, Map, Safety, and the developer simulator therefore observe consistent state.

For example:

1. The simulator publishes a critical helmet state.
2. Ride displays the updated overall safety status.
3. `GuardianService` receives the same event and triggers a check-in.
4. The rider resolves the check-in locally.

Simulation never manipulates a SwiftUI screen directly.

### Three-state Ride experience

Ride uses three presentation states:

```text
preRide -> activeRide -> summary -> preRide
```

- `preRide` provides helmet readiness, destination search, and route preview.
- `activeRide` makes the map primary and overlays glanceable metrics.
- `summary` freezes completed statistics and confirms local saving.

This avoids mixing setup controls, live navigation, and post-ride analytics on one crowded screen.

### Mock-first development

The app can be developed before helmet hardware is available. `MockHelmetService` implements the same interface expected from a real connection. Scenarios become ordinary `HelmetState` updates inside the service layer instead of UI-specific conditionals.

### Permission-denied behavior

Location denial is a supported state:

- The app remains usable.
- Simulator, Settings, Safety, and Guardian remain available.
- GPS status reports the denial.
- Routing explains that current location is unavailable.

### Routing tradeoff

MapKit’s public directions API has no dedicated cycling transport type. The MVP uses `.walking` as the closest approximation. That choice exists only inside `NavigationService`, allowing later replacement with a cycling-specific provider.

### Local persistence

Safety events are JSON-encoded into Application Support. Completed sessions are JSON-encoded into user defaults. These lightweight MVP choices sit behind services and can later be replaced with SwiftData or a remote store.

### Guardian behavior

A critical mock helmet state opens an app-wide “Are you okay?” check-in:

- **I’m OK** clears the possible-crash state.
- **I need help** simulates a Guardian alert and marks a possible emergency.

Neither action places calls, sends SMS messages, nor contacts emergency services.

## Technology

- Swift 5 language mode
- SwiftUI and Combine
- MapKit and CoreLocation
- CoreMotion
- Minimum deployment target: iOS 17.0
- Xcode project: `ios/HelmetSafety.xcodeproj`
- No third-party dependencies

## Repository structure

```text
Waymo-Helmet/
├── README.md
└── ios/
    ├── HelmetSafety.xcodeproj/
    └── HelmetSafety/
        ├── App/                 # Composition, tabs, theme, design system
        ├── Components/          # Reusable cards, metrics, event rows
        ├── Features/
        │   ├── Ride/            # Setup, live map, ride summary
        │   ├── Map/             # Standalone route screen
        │   ├── Safety/          # Analytics, history, event details
        │   ├── Guardian/        # Monitoring and emergency check-in
        │   └── Settings/        # Preferences and simulator
        ├── Models/              # Domain models and enums
        ├── Services/            # Platform APIs, simulation, persistence
        └── ViewModels/          # Feature state and commands
```

| Layer | Responsibility |
|---|---|
| `App` | Composition root, theme, reusable design tokens, tabs |
| `Features` | SwiftUI presentation grouped by product area |
| `ViewModels` | UI state, formatting, commands, service subscriptions |
| `Services` | Location, motion, routing, simulation, persistence, Guardian |
| `Models` | Shared domain and transport-independent data |
| `Components` | Reusable presentation building blocks |

## Requirements

- macOS with Xcode
- Xcode containing an iOS 17+ SDK
- iOS Simulator or iPhone running iOS 17+
- Internet access for destination search, routing, and map tiles
- Apple developer signing when installing on an iPhone

The repository was most recently verified with Xcode 26.6. Earlier versions may work if they contain the necessary iOS 17 SwiftUI and MapKit APIs.

## Open and run

1. Clone or download the repository.
2. Open `ios/HelmetSafety.xcodeproj` in Xcode.
3. Select the `HelmetSafety` scheme.
4. Choose an iPhone simulator or connected iPhone.
5. Press **Run**.

From Terminal:

```sh
cd /path/to/Waymo-Helmet
open ios/HelmetSafety.xcodeproj
```

## Command-line build

Build for a generic simulator without signing:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcodebuild \
  -project ios/HelmetSafety.xcodeproj \
  -scheme HelmetSafety \
  -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/HelmetSafetyDerivedData \
  CODE_SIGNING_ALLOWED=NO \
  build
```

Run static analysis:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcodebuild \
  -project ios/HelmetSafety.xcodeproj \
  -scheme HelmetSafety \
  -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath /tmp/HelmetSafetyDerivedData \
  CODE_SIGNING_ALLOWED=NO \
  analyze
```

If the active developer directory already points to Xcode, `DEVELOPER_DIR` can be omitted. To switch globally:

```sh
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
```

## Physical iPhone setup

GPS, heading, and motion are best tested on a device.

1. Connect the iPhone.
2. Open the project in Xcode.
3. Select the `HelmetSafety` target.
4. Open **Signing & Capabilities**.
5. Select an Apple development team.
6. If necessary, change `com.waymohelmet.HelmetSafety` to a unique bundle ID.
7. Select the iPhone as the destination.
8. Build and run.
9. Accept location and motion permission prompts.

The generated property list includes:

- `NSLocationWhenInUseUsageDescription`
- `NSMotionUsageDescription`

Only when-in-use location permission is requested.

## Using the application

### Tabs

1. **Ride** — setup, active navigation, and ride summary
2. **Map** — route-focused MapKit screen and incident markers
3. **Safety** — post-ride insights and incident history
4. **Guardian** — local ride and emergency monitoring
5. **Settings** — preferences and development tools

### Plan and start a ride

1. Open **Ride**.
2. Verify helmet connection and battery. Mock Mode is connected by default.
3. Grant location access when prompted.
4. Enter a destination.
5. Submit the search.
6. Review the route, distance, and duration.
7. Tap **Start Ride**.

A destination is optional, but maneuver guidance requires a calculated route.

### Active ride

Active mode provides:

- Current position, route, and destination
- Upcoming maneuver, street, and distance
- Elapsed time, distance, and current speed
- Overall safety status and event count
- Helmet connection and battery
- Heading-aware recentering
- Local voice-mute control

The mute control is UI state only until voice output is integrated.

### End and save

1. Tap **End Ride**.
2. Confirm **End & Save Ride**.
3. Review the route, time, distance, average/max speed, safety counts, and helmet status.
4. Tap **Done** to return to setup.

Completed `RideSession` values are saved locally, and the next action becomes **Start New Ride**.

### Map

The standalone Map tab can calculate and display routes independently:

1. Enter a destination.
2. Review route distance, duration, and safety pins.
3. Tap **Start Navigation**.

Search requires current location and internet access.

### Safety

Safety presents ride insights rather than live alarms:

- Safety score
- Close calls
- High-risk events
- Possible collisions
- Recent incident feed

Tap an event for metadata, coordinates, map location, notes, video placeholder, and AI-summary placeholder.

### Guardian

Guardian displays ride status, coordinates, speed, distance, helmet health, hazard totals, last update, emergency state, and the mock emergency contact. It remains local-only.

### Settings

Settings contains toggles for audio, haptics, helmet LEDs, voice assistant, and Guardian location sharing. Some toggles are foundations for future integrations and do not yet control hardware or external services.

## Developer Helmet Simulator

Open:

```text
Settings -> Development -> Developer Helmet Simulator
```

| Scenario | Result |
|---|---|
| No hazard | Restores safe state |
| Vehicle approaching from left | Medium-severity helmet state |
| Vehicle approaching from right | Medium-severity helmet state |
| High-risk vehicle from left | High-severity helmet state |
| High-risk vehicle from right | High-severity helmet state |
| Possible collision | Critical state and Guardian check-in |
| Helmet disconnected | Disconnected helmet state |
| Low battery | Battery becomes 8%; status becomes caution |

Directional data remains in the domain model for future helmet output, but it is not presented as live directional warnings in the app.

### Test the emergency check-in

1. Select **Possible collision** in the simulator.
2. The full-screen check-in appears.
3. Choose **I’m OK** or **I need help**.

The second option only simulates a Guardian alert.

## Important models

### `HelmetState`

Contains connection, battery, overall safety, internal hazard direction, detected object, estimated distance, and severity.

### `NavigationManeuver`

Provides a compact instruction suitable for a future helmet message:

```json
{
  "maneuverType": "turn",
  "direction": "left",
  "streetName": "University Blvd",
  "distanceMeters": 250
}
```

### `SafetyEvent`

Stores event type, timestamp, severity, side, detected object, estimated distance, location, speed, optional video path, and notes. Supported types include vehicle approach, close pass, hard brake, possible collision, collision, and manual recording.

### `RideSession`

Tracks start/end time, location, speed, distance, helmet state, ride status, last update, possible crash, hazard total, and high-risk total. Status can be `notStarted`, `active`, `paused`, `ended`, or `possibleEmergency`.

## Navigation details and limitations

`NavigationService` uses:

1. `MKLocalSearch` to resolve destination text.
2. `MKDirections` to calculate a route.
3. Location updates to estimate remaining distance and advance steps.

Apple instruction strings are simplified into maneuver type, direction, street name, and distance.

Known limitations:

- Walking directions approximate cycling directions.
- No automatic rerouting or off-route detection.
- Maneuver parsing depends on Apple instruction text.
- Spoken navigation is not connected.
- Simulator GPS must be configured in Xcode.

## Local data

Safety events are stored in the app sandbox at:

```text
Application Support/HelmetSafety/safety-events.json
```

Mock events seed the store on first launch. Completed rides are stored under the user-defaults key:

```text
HelmetSafety.savedRideSessions
```

There is not yet a completed-ride history screen.

## Future Raspberry Pi integration

The recommended path is a WebSocket-backed service conforming to `HelmetDataProviding`. It should:

- Decode versioned messages into domain models
- Publish snapshots through `helmetStatePublisher`
- Send navigation maneuvers to the Pi
- Reconnect with bounded backoff
- Expose connection health
- Keep Mock Mode available

Suggested envelope:

```json
{
  "version": 1,
  "type": "hazard.detected",
  "id": "event-uuid",
  "timestamp": "2026-09-26T14:30:00Z",
  "payload": {}
}
```

WebSocket parsing should remain outside SwiftUI views.

## Development workflow

1. Make one scoped change.
2. Keep platform/hardware APIs behind services.
3. Keep UI state in view models or coordinators.
4. Preserve Mock Mode when adding real integrations.
5. Build the generic simulator target.
6. Run static analysis.
7. Test denied permissions and disconnected state.
8. Test the critical collision and both check-in responses.
9. Validate speed, heading, and motion on an iPhone.

## Troubleshooting

### `xcodebuild` says Xcode is required

Use the `DEVELOPER_DIR` prefix shown above or select Xcode with `xcode-select`.

### CoreSimulator services are unavailable

Messages mentioning `CoreSimulatorService` or `simdiskimaged` indicate a local simulator-service problem, not necessarily a compilation failure. Restart Xcode or macOS, verify installed runtimes, and retry.

### Routing reports unavailable location

- Grant permission.
- Configure a simulated location in Xcode.
- Confirm Location Services are enabled.

### Maps load but routing fails

- Confirm internet access.
- Use a more specific destination.
- Ensure the current/simulated location is geographically reasonable.

### Mock events do not reset

Safety events persist. Delete the app from the simulator/device to clear its sandbox.

## Roadmap

1. Define a versioned Swift/Python WebSocket protocol.
2. Build the Raspberry Pi FastAPI server.
3. Add a `WebSocketHelmetClient` implementing `HelmetDataProviding`.
4. Add explicit real/mock source selection.
5. Send navigation maneuvers to the OLED.
6. Attach real incident recordings.
7. Fuse helmet and phone signals for collision scoring.
8. Add completed-ride history.
9. Add consent-based remote Guardian sharing.
10. Integrate Gemini and ElevenLabs behind service protocols.

## Verification

At the time of this README update:

```text
** BUILD SUCCEEDED **
** ANALYZE SUCCEEDED **
```
