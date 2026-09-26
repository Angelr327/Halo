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
    let distanceMeters: Double
}

struct NavigationState {
    var destinationName: String?
    var destinationCoordinate: CLLocationCoordinate2D?
    var route: MKRoute?
    var maneuver: NavigationManeuver?
    var isNavigating = false
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
