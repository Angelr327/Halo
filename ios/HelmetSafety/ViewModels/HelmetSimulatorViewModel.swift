import Combine
import Foundation

final class HelmetSimulatorViewModel: ObservableObject {
    @Published private(set) var activeScenario: HelmetSimulationScenario = .noHazard
    @Published private(set) var helmetState: HelmetState

    let scenarios = HelmetSimulationScenario.allCases
    private let service: HelmetSimulationProviding
    private var cancellables = Set<AnyCancellable>()

    init(service: HelmetSimulationProviding) {
        self.service = service
        self.helmetState = HelmetState(isConnected: false, batteryPercentage: 0, gpsStatus: .searching, speedMPH: 0, safetyStatus: .safe, leftHazard: false, rightHazard: false, detectedObject: nil, estimatedDistance: nil, severity: .safe, navigationInstruction: "No active route", distanceToTurnFeet: 0, rideHazardCount: 0)

        service.activeScenarioPublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.activeScenario = $0 }
            .store(in: &cancellables)
        service.helmetStatePublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.helmetState = $0 }
            .store(in: &cancellables)
    }

    func trigger(_ scenario: HelmetSimulationScenario) {
        service.trigger(scenario)
    }
}
