import CoreLocation
import Foundation

enum LocationConnectionStatus: String {
    case notDetermined = "Permission Required"
    case searching = "Searching for GPS"
    case connected = "GPS Connected"
    case denied = "Location Denied"
    case restricted = "Location Restricted"
    case unavailable = "GPS Unavailable"
}

struct LocationSnapshot: Equatable {
    var latitude: Double?
    var longitude: Double?
    var speedMPH: Double
    var headingDegrees: Double?
    var status: LocationConnectionStatus
    var authorizationStatus: CLAuthorizationStatus

    static let initial = LocationSnapshot(latitude: nil, longitude: nil, speedMPH: 0, headingDegrees: nil, status: .notDetermined, authorizationStatus: .notDetermined)
}
