import Foundation

enum RideStatus: String, Codable {
    case notStarted
    case active
    case paused
    case ended
    case possibleEmergency

    var displayName: String {
        switch self {
        case .notStarted: "Not Started"
        case .active: "Active"
        case .paused: "Paused"
        case .ended: "Ended"
        case .possibleEmergency: "Possible Emergency"
        }
    }
}

struct RideLocation: Codable, Equatable {
    var latitude: Double
    var longitude: Double
}

struct RideSession: Identifiable, Codable, Equatable {
    var id: UUID
    var startTime: Date?
    var endTime: Date?
    var currentLocation: RideLocation?
    var currentSpeed: Double
    var distanceTravelled: Double
    var helmetBattery: Int
    var helmetConnected: Bool
    var rideStatus: RideStatus
    var lastUpdate: Date?
    var possibleCrash: Bool
    var totalHazards: Int
    var highRiskEvents: Int

    static let empty = RideSession(id: UUID(), startTime: nil, endTime: nil, currentLocation: nil, currentSpeed: 0, distanceTravelled: 0, helmetBattery: 0, helmetConnected: false, rideStatus: .notStarted, lastUpdate: nil, possibleCrash: false, totalHazards: 0, highRiskEvents: 0)
}
