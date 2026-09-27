import Foundation

enum SafetyEventType: String, Codable, CaseIterable {
    case vehicleApproach
    case closePass
    case hardBrake
    case possibleCollision
    case collision
    case manualRecording
    case frontObstacle
    case emergencyBrakeWarning

    var displayName: String {
        switch self {
        case .vehicleApproach: "Vehicle Approach"
        case .closePass: "Close Pass"
        case .hardBrake: "Hard Brake"
        case .possibleCollision: "Possible Collision"
        case .collision: "Collision"
        case .manualRecording: "Manual Recording"
        case .frontObstacle: "Front Obstacle"
        case .emergencyBrakeWarning: "Emergency Brake Warning"
        }
    }
}

enum SafetyEventSide: String, Codable, CaseIterable {
    case left
    case right
    case rear
    case front
    case unknown
}

enum SafetyEventSeverity: String, Codable, CaseIterable {
    case low
    case medium
    case high
    case critical
}

enum IncidentAnalysisState: String, Codable {
    case notAnalyzed
    case analyzing
    case available
    case failed
}

struct SafetyEvent: Identifiable, Codable, Equatable {
    var id: UUID
    var timestamp: Date
    var eventType: SafetyEventType
    var severity: SafetyEventSeverity
    var cameraId: String?
    var side: SafetyEventSide?
    var detectedObject: String?
    var estimatedDistanceMeters: Double?
    var confidence: Double?
    var latitude: Double?
    var longitude: Double?
    var speed: Double?
    var videoPath: String?
    var videoURL: URL?
    var aiSummary: String?
    var aiAnalysisState: IncidentAnalysisState?
    var notes: String?
    var objectID: String? = nil
    var zone: String? = nil
    var tier: Int? = nil
    var ttcSeconds: Double? = nil
    var incidentID: String? = nil
    var isHistorical: Bool? = nil

    var hasLocation: Bool { latitude != nil && longitude != nil }
    var hasPlayableVideo: Bool { videoURL?.scheme != nil }
}
