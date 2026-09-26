import Foundation

enum HelmetSimulationScenario: String, CaseIterable, Identifiable {
    case noHazard = "No hazard"
    case vehicleApproachingLeft = "Vehicle approaching from left"
    case vehicleApproachingRight = "Vehicle approaching from right"
    case highRiskVehicleLeft = "High-risk vehicle from left"
    case highRiskVehicleRight = "High-risk vehicle from right"
    case possibleCollision = "Possible collision"
    case helmetDisconnected = "Helmet disconnected"
    case lowBattery = "Low battery"

    var id: Self { self }

    var systemImage: String {
        switch self {
        case .noHazard: "checkmark.shield.fill"
        case .vehicleApproachingLeft: "arrow.left.circle.fill"
        case .vehicleApproachingRight: "arrow.right.circle.fill"
        case .highRiskVehicleLeft: "exclamationmark.arrow.triangle.2.circlepath"
        case .highRiskVehicleRight: "exclamationmark.arrow.triangle.2.circlepath"
        case .possibleCollision: "exclamationmark.octagon.fill"
        case .helmetDisconnected: "wifi.slash"
        case .lowBattery: "battery.25percent"
        }
    }
}
