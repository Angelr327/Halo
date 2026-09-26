import Combine
import Foundation

final class EmergencyCheckInViewModel: ObservableObject {
    @Published private(set) var isPresented = false
    private let service: GuardianProviding
    private var cancellables = Set<AnyCancellable>()

    init(service: GuardianProviding) {
        self.service = service
        service.alertStatePublisher.receive(on: DispatchQueue.main).sink { [weak self] state in
            self?.isPresented = state == .checkingIn
        }.store(in: &cancellables)
    }

    func confirmSafe() { service.confirmSafe() }
    func requestHelp() { service.requestHelp() }
}
