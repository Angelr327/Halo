import Combine
import CoreLocation
import MapKit
import SwiftUI

@MainActor
final class DestinationSearchService: NSObject, ObservableObject {
    @Published var query = "" { didSet { updateSuggestions() } }
    @Published private(set) var suggestions: [DestinationSuggestion] = []
    @Published private(set) var selectedDestination: Destination?
    @Published private(set) var isResolving = false
    @Published private(set) var errorMessage: String?
    @Published private(set) var recentDestinations: [Destination] = []

    private let completer = MKLocalSearchCompleter()
    private let defaults: UserDefaults
    private static let recentsKey = "HelmetSafety.recentDestinations"

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        if let data = defaults.data(forKey: Self.recentsKey) {
            recentDestinations = (try? JSONDecoder().decode([Destination].self, from: data)) ?? []
        }
        super.init()
        completer.delegate = self
        completer.resultTypes = [.address, .pointOfInterest]
    }

    func updateRegion(for snapshot: LocationSnapshot) {
        guard let latitude = snapshot.latitude, let longitude = snapshot.longitude else { return }
        completer.region = MKCoordinateRegion(center: CLLocationCoordinate2D(latitude: latitude, longitude: longitude), latitudinalMeters: 50_000, longitudinalMeters: 50_000)
    }

    func select(_ suggestion: DestinationSuggestion) async throws -> Destination {
        isResolving = true
        errorMessage = nil
        defer { isResolving = false }
        do {
            let response = try await MKLocalSearch(request: MKLocalSearch.Request(completion: suggestion.completion)).start()
            guard let item = response.mapItems.first else { throw NavigationError.destinationNotFound }
            let destination = Destination(name: item.name ?? suggestion.title, subtitle: formattedAddress(for: item), coordinate: item.placemark.coordinate)
            selectedDestination = destination
            query = destination.name
            suggestions = []
            saveRecent(destination)
            return destination
        } catch {
            errorMessage = Self.message(for: error)
            throw error
        }
    }

    func selectRecent(_ destination: Destination) -> Destination {
        selectedDestination = destination
        query = destination.name
        suggestions = []
        saveRecent(destination)
        return destination
    }

    func destination(for feature: MapFeature) async -> Destination {
        isResolving = true
        errorMessage = nil
        defer { isResolving = false }
        let fallback = Destination(name: feature.title ?? "Map location", subtitle: nil, coordinate: feature.coordinate)
        let destination: Destination
        do {
            let item = try await MKMapItemRequest(feature: feature).mapItem
            destination = Destination(name: item.name ?? fallback.name, subtitle: formattedAddress(for: item), coordinate: item.placemark.coordinate)
        } catch {
            destination = fallback
        }
        selectedDestination = destination
        query = destination.name
        suggestions = []
        saveRecent(destination)
        return destination
    }

    func clear() {
        query = ""
        selectedDestination = nil
        suggestions = []
        errorMessage = nil
    }

    private func updateSuggestions() {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        errorMessage = nil
        guard !trimmed.isEmpty else {
            suggestions = []
            completer.queryFragment = ""
            return
        }
        if selectedDestination?.name != query { selectedDestination = nil }
        completer.queryFragment = trimmed
    }

    private func formattedAddress(for item: MKMapItem) -> String? {
        let placemark = item.placemark
        let parts = [placemark.subThoroughfare, placemark.thoroughfare, placemark.locality, placemark.administrativeArea].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: ", ")
    }

    private func saveRecent(_ destination: Destination) {
        let location = CLLocation(latitude: destination.coordinate.latitude, longitude: destination.coordinate.longitude)
        recentDestinations.removeAll {
            location.distance(from: CLLocation(latitude: $0.coordinate.latitude, longitude: $0.coordinate.longitude)) < 25
        }
        recentDestinations.insert(destination, at: 0)
        recentDestinations = Array(recentDestinations.prefix(5))
        if let data = try? JSONEncoder().encode(recentDestinations) { defaults.set(data, forKey: Self.recentsKey) }
    }

    private static func message(for error: Error) -> String {
        if let navigationError = error as? NavigationError { return navigationError.localizedDescription }
        return "Location search is unavailable right now. Check your connection and try again."
    }
}

extension DestinationSearchService: MKLocalSearchCompleterDelegate {
    nonisolated func completerDidUpdateResults(_ completer: MKLocalSearchCompleter) {
        let results = completer.results
        Task { @MainActor in
            suggestions = results.prefix(8).map {
                DestinationSuggestion(id: "\($0.title)|\($0.subtitle)", title: $0.title, subtitle: $0.subtitle, completion: $0)
            }
            if suggestions.isEmpty, !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                errorMessage = "No destinations found. Try a broader search."
            } else {
                errorMessage = nil
            }
        }
    }

    nonisolated func completer(_ completer: MKLocalSearchCompleter, didFailWithError error: Error) {
        Task { @MainActor in
            suggestions = []
            errorMessage = Self.message(for: error)
        }
    }
}
