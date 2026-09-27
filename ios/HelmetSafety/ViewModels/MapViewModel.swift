import Combine
import CoreLocation
import Foundation
import MapKit
import SwiftUI

@MainActor
final class MapViewModel: ObservableObject {
    @Published private(set) var navigationState = NavigationState()
    @Published private(set) var location = LocationSnapshot.initial
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?
    @Published private(set) var safetyEvents: [SafetyEvent] = []

    private let navigationService: NavigationProviding
    let destinationSearchService: DestinationSearchService
    private var cancellables = Set<AnyCancellable>()

    init(navigationService: NavigationProviding, destinationSearchService: DestinationSearchService, locationService: LocationProviding, safetyRepository: SafetyEventStoring) {
        self.navigationService = navigationService
        self.destinationSearchService = destinationSearchService
        navigationService.navigationPublisher.receive(on: DispatchQueue.main).sink { [weak self] in self?.navigationState = $0 }.store(in: &cancellables)
        locationService.locationPublisher.receive(on: DispatchQueue.main).sink { [weak self] snapshot in
            self?.location = snapshot
            self?.destinationSearchService.updateRegion(for: snapshot)
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
        let query = destinationSearchService.query.trimmingCharacters(in: .whitespacesAndNewlines)
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

    func selectDestination(_ suggestion: DestinationSuggestion) async {
        do {
            let destination = try await destinationSearchService.select(suggestion)
            await route(to: destination)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func selectRecentDestination(_ destination: Destination) async {
        await route(to: destinationSearchService.selectRecent(destination))
    }

    func selectMapFeature(_ feature: MapFeature) async {
        await route(to: destinationSearchService.destination(for: feature))
    }

    private func route(to destination: Destination) async {
        guard let latitude = location.latitude, let longitude = location.longitude else {
            errorMessage = NavigationError.locationUnavailable.localizedDescription
            return
        }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            try await navigationService.calculateRoute(to: destination, from: CLLocationCoordinate2D(latitude: latitude, longitude: longitude))
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func toggleNavigation() {
        navigationState.isNavigating ? navigationService.stopNavigation() : navigationService.startNavigation()
    }

    func setVoiceMuted(_ muted: Bool) {
        navigationService.setVoiceMuted(muted)
    }
}
