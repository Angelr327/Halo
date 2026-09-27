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
    // Live Pi diagnostics. Defaults preserve every existing mock/call site.
    var detectedObjectCount: Int = 0
    var cameraFPS: Double = 0
    var detectionLatencyMS: Double = 0
    var cameraFault: Bool = false
    var serialStatus: String = ""
    var sensitivityProfile: String = ""
    var sonarDistances: [String: Double?] = [:]
    var connectionStatus: String = "Offline"
    var estimatedTTC: Double? = nil
    var currentVehicleID: String? = nil
    var alertTier: Int = 0
    var cameraShaky: Bool = false
    var rearLightLevel: Int = 0
    var latestCaption: String? = nil
    var sceneDescription: String? = nil
    var corridorHalfWidthMeters: Double = 0
    var demoPersonMode: Bool = false
    var hudAlertTiers: [String: Int] = [:]
    var sonarMounts: [String: SonarMountState] = [:]
    var frontBrakeActive: Bool = false
    var frontWarningState: String = "CLEAR"
    var frontTargetID: String? = nil
    var frontDetectedObject: String? = nil
    var frontDistanceMeters: Double? = nil
    var frontTTCSeconds: Double? = nil
    var frontReason: String? = nil
    var frontObstacleCount: Int = 0
}

struct SonarMountState: Equatable {
    var zone: String
    var yawDegrees: Double
    var offsetMeters: Double
}
