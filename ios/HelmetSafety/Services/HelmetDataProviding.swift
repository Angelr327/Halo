import Combine
import Foundation

protocol HelmetDataProviding {
    var helmetStatePublisher: AnyPublisher<HelmetState, Never> { get }
    func startRide()
    func endRide()
}

protocol HelmetSimulationProviding: HelmetDataProviding {
    var activeScenarioPublisher: AnyPublisher<HelmetSimulationScenario, Never> { get }
    func trigger(_ scenario: HelmetSimulationScenario)
}
