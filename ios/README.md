# Halo iPhone app (HelmetSafety)

Halo is the iPhone app for the blind-spot helmet. It handles the parts that need a phone: GPS
and ride tracking, maps and navigation, incident history, and the connection to the helmet's
Raspberry Pi. The helmet does the time-critical work (detection and left/right warnings) on its
own, so a ride stays protected if the phone disconnects.

The app has two data sources, chosen in Settings. Mock mode uses a built-in helmet simulator,
so you can run the app without any hardware. Real mode polls the Pi's `/api/v1/state` endpoint
about four times a second and shows what the helmet sees.

> Safety notice: this is a prototype, not a certified safety device. Don't rely on it for
> collision avoidance, emergency response, medical monitoring, or navigation in hazardous
> conditions. It doesn't contact emergency services or send messages.

## What works today

- A SwiftUI app with an MVVM structure, for iOS 17 and later
- Ride setup, active navigation and a post-ride summary
- Position, speed and heading from CoreLocation, and motion sampling from CoreMotion
- MapKit destination search (recent destinations are remembered) and route calculation
- A route line, your location, the destination and incident pins on the map
- Spoken navigation through ElevenLabs when a key is configured, otherwise the iPhone's own voice
- Live helmet data in Real mode: connection, safety level, left and right hazards, the nearest
  object and its distance, camera frame rate and latency, camera faults and sonar readings
- A safety event whenever a helmet alert rises to medium or high (a close pass when the side
  sensor measured the gap, otherwise a vehicle approach)
- The Pi's camera debug page, embedded in Settings
- A developer helmet simulator for Mock mode
- A Safety tab with a safety score, close calls, high-risk events, possible collisions, an
  incident feed and event details
- Safety events saved on the phone

## Not done yet

- Downloading the Pi's incident clips and Gemini reports. The Pi serves them at
  `/api/v1/incidents` (see the main README), but the app doesn't fetch them yet.
- Real incident analysis in the app. No Gemini key is stored in the app; in Mock mode the event
  detail shows a local placeholder summary.
- Attaching the phone's GPS position to events that come from the Pi.
- Saving completed rides. The summary goes away once you leave it.
- Cycling-specific routing and automatic rerouting.
- Emergency calls or contact notifications.

## How the work is split

The helmet owns the fast safety loop: camera and ultrasonic readings, object detection and
tracking, left/right hazard decisions, the beeps, lights, haptics and OLED, the incident video
buffer, and staying safe when the phone isn't there.

The phone owns the features that face the rider or need the internet: GPS, speed and heading,
maps and routing, ride setup and navigation, safety summaries and incident history, and voice.

The app doesn't show live left/right hazard warnings. Those are time-sensitive and belong on the
helmet. The app shows the overall safety state, counts, history and post-ride insights instead.

## Design decisions

### Services behind protocols

Platform and hardware code sits behind protocols:

- `HelmetDataProviding`
- `HelmetSimulationProviding`
- `LocationProviding`
- `MotionProviding`
- `NavigationProviding`
- `NavigationVoiceProviding`
- `SafetyEventStoring`
- `IncidentAnalysisProviding`

SwiftUI screens depend on view models, and view models depend on these contracts.
`ConnectedHelmetService` implements the helmet protocols and switches between the simulator and
the Pi, so no screen needs to know where the data comes from.

### Polling the Pi

In Real mode, `ConnectedHelmetService` requests `/api/v1/state` every 250 ms with a 2 s timeout.
Each request stands alone, so the app recovers by itself after the Wi-Fi or the Pi restarts, and
the Pi only needs the HTTP server it already runs. The service turns each snapshot into a
`HelmetState`, and it creates a `SafetyEvent` when a tracked object's alert tier rises to medium
or high, at most once every 5 s per object.

### Shared state

`AppViewModel` creates the shared services and hands them to each screen, so Ride, Map, Safety
and the simulator all see the same helmet state and the same events, whichever data source is
active. The simulator never changes a screen directly; it publishes ordinary `HelmetState`
updates.

### Three ride states

Ride moves through three states:

```text
preRide -> activeRide -> summary -> preRide
```

`preRide` shows helmet readiness, destination search and a route preview. `activeRide` makes the
map the main view with glanceable metrics on top. `summary` freezes the finished ride's
statistics. Keeping them separate avoids one crowded screen that mixes setup, live navigation
and analytics.

### Mock first

You can work on the app without helmet hardware. The simulator publishes the same kind of
updates the Pi does, so its scenarios don't need special cases in the UI.

### When location is denied

The app keeps working without location permission. The simulator, Settings and Safety stay
available, the GPS status reports the denial, and routing explains that your location is
unavailable.

### Routing

MapKit's public directions API has no cycling transport type, so the app asks for walking
directions as the closest match. That choice lives only in `NavigationService`, so a
cycling-specific provider can replace it later.

### Local storage

Safety events are saved as JSON in Application Support. Settings and recent destinations are
kept in user defaults. Both sit behind services, so they can move to SwiftData or a remote store
later.

## Technology

- Swift 5 language mode
- SwiftUI and Combine
- MapKit and CoreLocation
- CoreMotion
- WebKit (for the camera debug page)
- Minimum deployment target: iOS 17.0
- Xcode project: `ios/HelmetSafety.xcodeproj`
- No third-party dependencies

## Repository structure

```text
blindspot-helmet/
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
        │   └── Settings/        # Data source, camera debug, simulator
        ├── Models/              # Domain models and enums
        ├── Services/            # Platform APIs, Pi connection, simulation, storage
        └── ViewModels/          # Feature state and commands
```

| Layer | Responsibility |
|---|---|
| `App` | Composition root, theme, design tokens, tabs |
| `Features` | SwiftUI screens grouped by product area |
| `ViewModels` | UI state, formatting, commands, service subscriptions |
| `Services` | Location, motion, routing, voice, the Pi connection, simulation, storage |
| `Models` | Shared data types that don't depend on the transport |
| `Components` | Reusable UI pieces |

## Requirements

- A Mac with Xcode that includes the iOS 17 SDK or later
- The iOS Simulator, or an iPhone running iOS 17 or later
- Internet access for destination search, routing and map tiles
- Apple developer signing to install on an iPhone
- For Real mode, the phone and the Pi on the same network

The project was last checked with Xcode 26.6. Earlier versions may work if they have the iOS 17
SwiftUI and MapKit APIs.

## Open and run

1. Clone or download the repository.
2. Open `ios/HelmetSafety.xcodeproj` in Xcode.
3. Select the `HelmetSafety` scheme.
4. Choose an iPhone simulator or a connected iPhone.
5. Press Run.

From Terminal:

```sh
cd /path/to/blindspot-helmet
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

Both should end with `** BUILD SUCCEEDED **` and `** ANALYZE SUCCEEDED **`. If your active
developer directory already points to Xcode, you can leave out `DEVELOPER_DIR`. To switch it
globally:

```sh
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
```

## Running on an iPhone

GPS, heading and motion are best tested on a real device.

1. Connect the iPhone.
2. Open the project in Xcode.
3. Select the `HelmetSafety` target.
4. Open Signing & Capabilities.
5. Select an Apple development team.
6. If needed, change `com.waymohelmet.HelmetSafety` to a unique bundle ID.
7. Select the iPhone as the destination.
8. Build and run.
9. Accept the location and motion permission prompts.

The generated property list includes `NSLocationWhenInUseUsageDescription` and
`NSMotionUsageDescription`. The app only asks for location while it's in use.

## Connecting to the helmet

1. Start the helmet software on the Pi (see the main README). It serves port 8080.
2. Put the phone on the same network as the Pi. Hackathon Wi-Fi often isolates devices, so a
   phone hotspot for both is the safest option.
3. In Settings, under Helmet, set Mode to Real.
4. Set the Pi endpoint. The default is `http://helmet.local:8080`; an IP address such as
   `http://192.168.1.20:8080` also works.

Connection then reads "Pi live data", and the Ride and Safety tabs follow the helmet. Settings >
Developer > Helmet Camera Debug shows the Pi's camera debug page. To go back to the simulator,
set Mode to Mock.

## Using the app

### Tabs

1. Ride: setup, active navigation and the ride summary
2. Map: a route-focused MapKit screen with incident markers
3. Safety: post-ride insights and incident history
4. Settings: data source, alerts, AI and developer tools

### Plan and start a ride

1. Open Ride.
2. Check the helmet connection and battery. Mock mode starts connected.
3. Allow location access when asked.
4. Enter a destination and submit the search.
5. Review the route, distance and duration.
6. Tap START RIDE.

A destination is optional, but turn-by-turn guidance needs a calculated route.

### During a ride

The active ride screen shows your position, the route and destination, the next maneuver with
its street and distance, elapsed time, distance and speed, the overall safety status and event
count, and the helmet's connection and battery. The map recenters using your heading, and you
can mute the voice guidance.

### End a ride

1. Tap End Ride.
2. Confirm with End & Save Ride.
3. Review the route, time, distance, average and top speed, safety counts and helmet status.
4. Tap DONE to return to setup, where the button now reads START NEW RIDE.

The summary isn't stored after you leave it; saving rides is on the roadmap.

### Map

The Map tab can calculate and show a route on its own:

1. Enter a destination.
2. Review the route distance, duration and safety pins.
3. Tap START RIDE (or RETURN TO RIDE if a ride is already going).

Search needs your current location and internet access.

### Safety

Safety shows insights after the fact, not live alarms: a safety score, close calls, high-risk
events, possible collisions and a recent incident feed. Tap an event to see its details,
coordinates, map location, notes, and placeholders for video and an AI summary.

### Settings

- Helmet: the Mock or Real data source, the Pi endpoint, and the connection status
- Alerts: toggles for audio, haptic and LED alerts
- AI: the voice assistant toggle and the incident analysis status
- Developer: the helmet camera debug page and the helmet simulator

Some toggles are placeholders for future integrations and don't control hardware yet.

## Developer helmet simulator

Open it from Settings > Developer > Developer Helmet Simulator (Mock mode).

| Scenario | Result |
|---|---|
| No hazard | Safe state |
| Vehicle approaching from left | Medium-severity hazard on the left |
| Vehicle approaching from right | Medium-severity hazard on the right |
| High-risk vehicle from left | High-severity hazard on the left |
| High-risk vehicle from right | High-severity hazard on the right |
| Possible collision | Critical state with hazards on both sides |
| Helmet disconnected | Helmet shown as disconnected |
| Low battery | Battery drops to 8% and the status becomes caution |

The helmet state keeps the hazard's direction for the helmet's own outputs, but the app doesn't
show it as a live directional warning.

## Main models

`HelmetState` holds the connection, battery, GPS status, speed, overall safety level, left and
right hazards, the detected object and its estimated distance, severity, navigation text, and,
in Real mode, the camera frame rate, detection latency, camera fault, serial status, sensitivity
profile and sonar distances.

`NavigationManeuver` is a compact instruction meant for a future helmet message:

```json
{
  "maneuverType": "turn",
  "direction": "left",
  "streetName": "University Blvd",
  "distanceMeters": 250
}
```

`SafetyEvent` stores the event type, time, severity, side, detected object, estimated distance,
location, speed, an optional video reference and notes. The types are vehicle approach, close
pass, hard brake, possible collision, collision and manual recording.

`RideSession` tracks start and end times, location, speed, distance, helmet state, ride status,
the last update, and hazard counts. Its status is `notStarted`, `active`, `paused`, `ended` or
`possibleEmergency`.

## Navigation details and limits

`NavigationService` resolves the destination text with `MKLocalSearch`, calculates a route with
`MKDirections`, and uses location updates to estimate the remaining distance and move through the
steps. Apple's instruction strings are simplified into a maneuver type, direction, street name
and distance.

Known limits:

- Walking directions stand in for cycling directions.
- There's no automatic rerouting or off-route detection.
- Maneuver parsing depends on Apple's instruction text.
- In the iOS Simulator you have to set a simulated location in Xcode.

## Local data

Safety events are stored in the app sandbox at:

```text
Application Support/HelmetSafety/safety-events.json
```

On first launch the store is filled with sample events, which show in Mock mode. In Real mode the
list starts empty and fills with events from the helmet. Settings and recent destinations are
kept in user defaults. There's no ride history screen yet.

## Development workflow

1. Make one focused change at a time.
2. Keep platform and hardware APIs behind services.
3. Keep UI state in view models.
4. Keep Mock mode working when you add real integrations.
5. Build the generic simulator target and run static analysis.
6. Test with location permission denied and with the helmet disconnected.
7. Test the Possible collision scenario.
8. Check speed, heading and motion on an iPhone, and Real mode against a running Pi.

## Troubleshooting

### `xcodebuild` says Xcode is required

Use the `DEVELOPER_DIR` prefix shown above, or select Xcode with `xcode-select`.

### CoreSimulator services are unavailable

Messages about `CoreSimulatorService` or `simdiskimaged` point to a local simulator problem, not
necessarily a build failure. Restart Xcode or macOS, check the installed runtimes, and try again.

### Real mode shows the helmet as disconnected

Check that the phone and the Pi are on the same network and that the endpoint in Settings
matches the Pi's address and port 8080. Opening `http://<pi-ip>:8080/api/v1/state` in Safari on
the phone should return JSON.

### Routing reports that your location is unavailable

Allow location access, set a simulated location in Xcode, and make sure Location Services are on.

### Maps load but routing fails

Check the internet connection, try a more specific destination, and make sure the current or
simulated location is somewhere sensible.

### Sample events don't reset

Safety events persist. Delete the app from the simulator or device to clear its sandbox.

## Roadmap

1. Fetch the Pi's incident list, clips and Gemini reports, and play the clips in the event detail.
2. Attach the phone's GPS position to each incident from the Pi.
3. Save completed rides and add a ride history screen.
4. Send navigation maneuvers to the helmet's OLED.
5. Use a cycling-specific routing provider, with rerouting.
6. Combine helmet and phone signals for collision scoring.
