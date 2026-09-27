import Combine
import CoreLocation
import Foundation

enum RideScreenMode {
    case preRide
    case activeRide
    case summary
}

@MainActor
final class RideViewModel: ObservableObject {
    @Published private(set) var helmetState: HelmetState
    @Published private(set) var location = LocationSnapshot.initial
    @Published private(set) var navigation = NavigationState()
    @Published private(set) var rideSession = RideSession.empty
    @Published private(set) var now = Date()
    @Published private(set) var isRideActive = false
    @Published private(set) var screenMode: RideScreenMode = .preRide
    @Published private(set) var isRouting = false
    @Published var routeError: String?
    @Published private(set) var safetyEvents: [SafetyEvent] = []
    @Published private(set) var maximumSpeedMPH = 0.0
    @Published private(set) var speedSampleTotal = 0.0
    @Published private(set) var speedSampleCount = 0
    private let service: HelmetDataProviding
    private let locationService: LocationProviding
    private let navigationService: NavigationProviding
    let destinationSearchService: DestinationSearchService
    private let safetyRepository: SafetyEventStoring
    private var cancellables = Set<AnyCancellable>()
    private var previousRideLocation: CLLocation?

    init(service: HelmetDataProviding, locationService: LocationProviding, navigationService: NavigationProviding, destinationSearchService: DestinationSearchService, safetyRepository: SafetyEventStoring = SafetyEventRepository()) {
        self.service = service
        self.locationService = locationService
        self.navigationService = navigationService
        self.destinationSearchService = destinationSearchService
        self.safetyRepository = safetyRepository
        self.helmetState = HelmetState(isConnected: false, batteryPercentage: 0, gpsStatus: .searching, speedMPH: 0, safetyStatus: .safe, leftHazard: false, rightHazard: false, detectedObject: nil, estimatedDistance: nil, severity: .safe, navigationInstruction: "No active route", distanceToTurnFeet: 0, rideHazardCount: 0)
        service.helmetStatePublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.helmetState = $0 }
            .store(in: &cancellables)
        locationService.locationPublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] snapshot in
                self?.location = snapshot
                self?.destinationSearchService.updateRegion(for: snapshot)
                if self?.screenMode == .activeRide {
                    self?.maximumSpeedMPH = max(self?.maximumSpeedMPH ?? 0, snapshot.speedMPH)
                    self?.speedSampleTotal += snapshot.speedMPH
                    self?.speedSampleCount += 1
                    if let latitude = snapshot.latitude, let longitude = snapshot.longitude {
                        let current = CLLocation(latitude: latitude, longitude: longitude)
                        if let previous = self?.previousRideLocation {
                            self?.rideSession.distanceTravelled += current.distance(from: previous) / 1_609.344
                        }
                        self?.previousRideLocation = current
                        self?.rideSession.currentLocation = RideLocation(latitude: latitude, longitude: longitude)
                    }
                    self?.rideSession.currentSpeed = snapshot.speedMPH
                }
                if let latitude = snapshot.latitude, let longitude = snapshot.longitude {
                    self?.navigationService.updateLocation(CLLocationCoordinate2D(latitude: latitude, longitude: longitude))
                }
            }
            .store(in: &cancellables)
        safetyRepository.eventsPublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.safetyEvents = $0 }
            .store(in: &cancellables)
        locationService.requestAuthorization()
        locationService.startUpdating()
        navigationService.navigationPublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.navigation = $0 }
            .store(in: &cancellables)
        Timer.publish(every: 1, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] in self?.now = $0 }
            .store(in: &cancellables)
    }

    func calculateRoute() async {
        guard let latitude = location.latitude, let longitude = location.longitude else {
            routeError = NavigationError.locationUnavailable.localizedDescription
            return
        }
        let destination = destinationSearchService.query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !destination.isEmpty else { return }
        isRouting = true
        routeError = nil
        defer { isRouting = false }
        do {
            try await navigationService.calculateRoute(to: destination, from: CLLocationCoordinate2D(latitude: latitude, longitude: longitude))
        } catch {
            routeError = error.localizedDescription
        }
    }

    func selectDestination(_ suggestion: DestinationSuggestion) async {
        do {
            let destination = try await destinationSearchService.select(suggestion)
            await route(to: destination)
        } catch {
            routeError = error.localizedDescription
        }
    }

    func selectRecentDestination(_ destination: Destination) async {
        await route(to: destinationSearchService.selectRecent(destination))
    }

    private func route(to destination: Destination) async {
        guard let latitude = location.latitude, let longitude = location.longitude else {
            routeError = NavigationError.locationUnavailable.localizedDescription
            return
        }
        isRouting = true
        routeError = nil
        defer { isRouting = false }
        do {
            try await navigationService.calculateRoute(to: destination, from: CLLocationCoordinate2D(latitude: latitude, longitude: longitude))
        } catch {
            routeError = error.localizedDescription
        }
    }

    func startRide() {
        maximumSpeedMPH = 0
        speedSampleTotal = 0
        speedSampleCount = 0
        previousRideLocation = nil
        service.startRide()
        rideSession = .empty
        rideSession.id = UUID()
        rideSession.startTime = Date()
        rideSession.rideStatus = .active
        navigationService.startNavigation()
        isRideActive = true
        screenMode = .activeRide
    }

    func endRide() {
        service.endRide()
        rideSession.endTime = Date()
        rideSession.rideStatus = .ended
        rideSession.currentSpeed = 0
        navigationService.stopNavigation()
        isRideActive = false
        screenMode = .summary
    }

    func finishSummary() {
        screenMode = .preRide
    }

    func setVoiceMuted(_ muted: Bool) {
        navigationService.setVoiceMuted(muted)
    }

    var headingText: String {
        guard let heading = location.headingDegrees else { return "—" }
        let directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        let index = Int((heading + 22.5) / 45).quotientAndRemainder(dividingBy: 8).remainder
        return "\(Int(heading.rounded()))° \(directions[index])"
    }

    var maneuverDistanceFeet: Int {
        Int(((navigation.maneuver?.distanceMeters ?? 0) * 3.28084).rounded())
    }

    var elapsedTimeText: String {
        guard let start = rideSession.startTime else { return "00:00" }
        let end = rideSession.endTime ?? now
        let seconds = max(0, Int(end.timeIntervalSince(start)))
        return String(format: "%02d:%02d:%02d", seconds / 3600, (seconds % 3600) / 60, seconds % 60)
    }

    var routeDistanceText: String {
        guard let meters = navigation.route?.distance else { return "—" }
        return String(format: "%.1f mi", meters / 1_609.344)
    }

    var routeDurationText: String {
        guard let seconds = navigation.route?.expectedTravelTime else { return "—" }
        return "\(max(1, Int((seconds / 60).rounded()))) min"
    }

    var averageSpeedMPH: Double {
        speedSampleCount == 0 ? 0 : speedSampleTotal / Double(speedSampleCount)
    }

    var currentRideEvents: [SafetyEvent] {
        guard let start = rideSession.startTime else { return [] }
        return safetyEvents.filter { $0.timestamp >= start }
    }

    var closeCallCount: Int { currentRideEvents.filter { $0.eventType == .closePass }.count }
    var highRiskEventCount: Int { currentRideEvents.filter { $0.severity == .high || $0.severity == .critical }.count }

    var safetyLabel: String {
        switch helmetState.safetyStatus {
        case .safe: "SAFE"
        case .caution: "CAUTION"
        case .danger: "HIGH RISK"
        }
    }
}
