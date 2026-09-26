import Combine
import Foundation

final class MockHelmetService: HelmetDataProviding {
    private let stateSubject = CurrentValueSubject<HelmetState, Never>(
        HelmetState(
            isConnected: true,
            batteryPercentage: 86,
            gpsStatus: .locked,
            speedMPH: 14.8,
            safetyStatus: .caution,
            leftHazard: true,
            rightHazard: false,
            detectedObject: "Vehicle",
            estimatedDistance: 11.8,
            severity: .medium,
            navigationInstruction: "Turn right onto Riverside Drive",
            distanceToTurnFeet: 420,
            rideHazardCount: 3
        )
    )

    private let scenarioSubject = CurrentValueSubject<HelmetSimulationScenario, Never>(.vehicleApproachingLeft)

    var helmetStatePublisher: AnyPublisher<HelmetState, Never> {
        stateSubject.eraseToAnyPublisher()
    }

    var activeScenarioPublisher: AnyPublisher<HelmetSimulationScenario, Never> {
        scenarioSubject.eraseToAnyPublisher()
    }

    func startRide() {
        var state = stateSubject.value
        state.speedMPH = 12.4
        state.rideHazardCount = 0
        stateSubject.send(state)
    }

    func endRide() {
        var state = stateSubject.value
        state.speedMPH = 0
        state.leftHazard = false
        state.rightHazard = false
        state.safetyStatus = .safe
        state.detectedObject = nil
        state.estimatedDistance = nil
        state.severity = .safe
        stateSubject.send(state)
    }


    func trigger(_ scenario: HelmetSimulationScenario) {
        var state = stateSubject.value
        state.isConnected = true
        state.batteryPercentage = 86
        state.gpsStatus = .locked
        state.leftHazard = false
        state.rightHazard = false
        state.detectedObject = nil
        state.estimatedDistance = nil
        state.severity = .safe
        state.safetyStatus = .safe

        switch scenario {
        case .noHazard:
            break
        case .vehicleApproachingLeft:
            applyHazard(to: &state, side: .left, distance: 11.8, severity: .medium)
        case .vehicleApproachingRight:
            applyHazard(to: &state, side: .right, distance: 10.5, severity: .medium)
        case .highRiskVehicleLeft:
            applyHazard(to: &state, side: .left, distance: 4.2, severity: .high)
        case .highRiskVehicleRight:
            applyHazard(to: &state, side: .right, distance: 3.9, severity: .high)
        case .possibleCollision:
            state.leftHazard = true
            state.rightHazard = true
            state.detectedObject = "Vehicle"
            state.estimatedDistance = 1.2
            state.severity = .critical
            state.safetyStatus = .danger
            state.rideHazardCount += 1
        case .helmetDisconnected:
            state.isConnected = false
            state.gpsStatus = .unavailable
        case .lowBattery:
            state.batteryPercentage = 8
            state.severity = .low
            state.safetyStatus = .caution
        }

        scenarioSubject.send(scenario)
        stateSubject.send(state)
    }

    private func applyHazard(to state: inout HelmetState, side: SafetyEventSide, distance: Double, severity: HazardSeverity) {
        state.leftHazard = side == .left
        state.rightHazard = side == .right
        state.detectedObject = "Vehicle"
        state.estimatedDistance = distance
        state.severity = severity
        state.safetyStatus = severity == .high || severity == .critical ? .danger : .caution
        state.rideHazardCount += 1
    }
}

extension MockHelmetService: HelmetSimulationProviding {}
