import Foundation

enum SafetyLevel: String, CaseIterable, Codable {
    case safe = "SAFE"
    case caution = "CAUTION"
    case danger = "DANGER"
}

enum GPSStatus: String, Codable {
    case searching = "Searching"
    case locked = "GPS Locked"
    case unavailable = "Unavailable"
}

enum HazardSeverity: String, CaseIterable, Codable {
    case safe
    case low
    case medium
    case high
    case critical
}

struct HelmetState: Equatable {
    var isConnected: Bool
    var batteryPercentage: Int
    var gpsStatus: GPSStatus
    var speedMPH: Double
    var safetyStatus: SafetyLevel
    var leftHazard: Bool
    var rightHazard: Bool
    var detectedObject: String?
    var estimatedDistance: Double?
    var severity: HazardSeverity
    var navigationInstruction: String
    var distanceToTurnFeet: Int
    var rideHazardCount: Int
}
