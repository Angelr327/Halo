import CoreLocation
import MapKit

enum NavigationDirection: String, Codable {
    case left
    case right
    case straight
    case arrive
    case unknown
}

enum ManeuverType: String, Codable {
    case turn
    case slightTurn
    case sharpTurn
    case uTurn
    case continueStraight
    case merge
    case roundabout
    case arrive
    case unknown
}

struct NavigationManeuver: Equatable, Codable {
    let maneuverType: ManeuverType
    let direction: NavigationDirection
    let streetName: String
    let instruction: String
    let distanceMeters: Double
}

struct NavigationState {
    var destinationName: String?
    var destinationCoordinate: CLLocationCoordinate2D?
    var route: MKRoute?
    var maneuver: NavigationManeuver?
    var isNavigating = false
    var isVoiceMuted = false
    var statusMessage = "Enter a destination"
}

enum NavigationError: LocalizedError {
    case locationUnavailable
    case destinationNotFound
    case routeUnavailable

    var errorDescription: String? {
        switch self {
        case .locationUnavailable: "Current location is unavailable. Check Location permissions."
        case .destinationNotFound: "No matching destination was found."
        case .routeUnavailable: "Apple Maps could not calculate a route."
        }
    }
}

enum RouteTimeFormatter {
    static func duration(_ seconds: TimeInterval) -> String {
        let totalMinutes = max(1, Int((seconds / 60).rounded()))
        guard totalMinutes >= 60 else { return "\(totalMinutes) min" }
        let hours = totalMinutes / 60
        let minutes = totalMinutes % 60
        return minutes == 0 ? "\(hours) hr" : "\(hours) hr \(minutes) min"
    }
}
