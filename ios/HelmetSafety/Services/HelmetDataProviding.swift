import Combine
import Foundation

protocol HelmetDataProviding {
    var helmetStatePublisher: AnyPublisher<HelmetState, Never> { get }
    var safetyEventPublisher: AnyPublisher<SafetyEvent, Never> { get }
    func startRide()
    func endRide()
}

extension HelmetDataProviding {
    var safetyEventPublisher: AnyPublisher<SafetyEvent, Never> { Empty().eraseToAnyPublisher() }
}

protocol HelmetSimulationProviding: HelmetDataProviding {
    var activeScenarioPublisher: AnyPublisher<HelmetSimulationScenario, Never> { get }
    func trigger(_ scenario: HelmetSimulationScenario)
}
