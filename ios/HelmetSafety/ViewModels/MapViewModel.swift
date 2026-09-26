import Combine
import CoreLocation
import Foundation
import MapKit

@MainActor
final class MapViewModel: ObservableObject {
    @Published var destinationQuery = ""
    @Published private(set) var navigationState = NavigationState()
    @Published private(set) var location = LocationSnapshot.initial
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?
    @Published private(set) var safetyEvents: [SafetyEvent] = []

    private let navigationService: NavigationProviding
    private var cancellables = Set<AnyCancellable>()

    init(navigationService: NavigationProviding, locationService: LocationProviding, safetyRepository: SafetyEventStoring) {
        self.navigationService = navigationService
        navigationService.navigationPublisher.receive(on: DispatchQueue.main).sink { [weak self] in self?.navigationState = $0 }.store(in: &cancellables)
        locationService.locationPublisher.receive(on: DispatchQueue.main).sink { [weak self] snapshot in
            self?.location = snapshot
            if let latitude = snapshot.latitude, let longitude = snapshot.longitude {
                self?.navigationService.updateLocation(CLLocationCoordinate2D(latitude: latitude, longitude: longitude))
            }
        }.store(in: &cancellables)
        safetyRepository.eventsPublisher.receive(on: DispatchQueue.main).sink { [weak self] in
            self?.safetyEvents = $0.filter(\.hasLocation)
        }.store(in: &cancellables)
    }

    var routeDistanceText: String {
        guard let distance = navigationState.route?.distance else { return "—" }
        return String(format: "%.1f mi", distance / 1_609.344)
    }

    var routeTimeText: String {
        guard let seconds = navigationState.route?.expectedTravelTime else { return "—" }
        return "\(max(1, Int((seconds / 60).rounded()))) min"
    }

    func search() async {
        guard let latitude = location.latitude, let longitude = location.longitude else {
            errorMessage = NavigationError.locationUnavailable.localizedDescription
            return
        }
        let query = destinationQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            try await navigationService.calculateRoute(to: query, from: CLLocationCoordinate2D(latitude: latitude, longitude: longitude))
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func toggleNavigation() {
        navigationState.isNavigating ? navigationService.stopNavigation() : navigationService.startNavigation()
    }
}
