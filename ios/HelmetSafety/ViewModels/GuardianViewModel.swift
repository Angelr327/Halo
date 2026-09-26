import Combine
import Foundation

final class GuardianViewModel: ObservableObject {
    @Published private(set) var session = RideSession.empty
    @Published private(set) var alertState: GuardianAlertState = .idle
    let emergencyContacts: [EmergencyContact]
    private var cancellables = Set<AnyCancellable>()

    init(service: GuardianProviding) {
        emergencyContacts = service.emergencyContacts
        service.sessionPublisher.receive(on: DispatchQueue.main).sink { [weak self] in self?.session = $0 }.store(in: &cancellables)
        service.alertStatePublisher.receive(on: DispatchQueue.main).sink { [weak self] in self?.alertState = $0 }.store(in: &cancellables)
    }
}
