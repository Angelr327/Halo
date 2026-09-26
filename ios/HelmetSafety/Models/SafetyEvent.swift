import Foundation

enum SafetyEventType: String, Codable, CaseIterable {
    case vehicleApproach
    case closePass
    case hardBrake
    case possibleCollision
    case collision
    case manualRecording

    var displayName: String {
        switch self {
        case .vehicleApproach: "Vehicle Approach"
        case .closePass: "Close Pass"
        case .hardBrake: "Hard Brake"
        case .possibleCollision: "Possible Collision"
        case .collision: "Collision"
        case .manualRecording: "Manual Recording"
        }
    }
}

enum SafetyEventSide: String, Codable, CaseIterable {
    case left
    case right
    case rear
    case unknown
}

enum SafetyEventSeverity: String, Codable, CaseIterable {
    case low
    case medium
    case high
    case critical
}

struct SafetyEvent: Identifiable, Codable, Equatable {
    var id: UUID
    var timestamp: Date
    var eventType: SafetyEventType
    var severity: SafetyEventSeverity
    var side: SafetyEventSide
    var detectedObject: String?
    var estimatedDistanceMeters: Double?
    var latitude: Double?
    var longitude: Double?
    var speed: Double?
    var videoPath: String?
    var notes: String?

    var hasLocation: Bool { latitude != nil && longitude != nil }
}
