import Combine
import Foundation

final class SafetyViewModel: ObservableObject {
    @Published private(set) var events: [SafetyEvent] = []
    private var cancellables = Set<AnyCancellable>()

    init(repository: SafetyEventStoring) {
        repository.eventsPublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.events = $0 }
            .store(in: &cancellables)
    }

    var closeCallCount: Int { events.filter { $0.eventType == .closePass }.count }
    var highRiskCount: Int { events.filter { $0.severity == .high || $0.severity == .critical }.count }
    var possibleCollisionCount: Int { events.filter { $0.eventType == .possibleCollision || $0.eventType == .collision }.count }
    var safetyScore: Int { max(0, 100 - closeCallCount * 5 - highRiskCount * 12 - possibleCollisionCount * 18) }
}
