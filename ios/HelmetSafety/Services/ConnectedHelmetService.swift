import Combine
import Foundation

/// Switches between the existing simulator and live Raspberry Pi data without leaking
/// transport details into SwiftUI. The Pi endpoint is intentionally polled: each request is
/// independent, recovers cleanly after Wi-Fi/Pi restarts, and uses the existing HTTP server.
@MainActor
final class ConnectedHelmetService: HelmetSimulationProviding {
    private let settings: AppSettings
    private let mock = MockHelmetService()
    private let stateSubject = CurrentValueSubject<HelmetState, Never>(.disconnected)
    private let eventSubject = PassthroughSubject<SafetyEvent, Never>()
    private let scenarioSubject = CurrentValueSubject<HelmetSimulationScenario, Never>(.noHazard)
    private var cancellables = Set<AnyCancellable>()
    private var pollTask: Task<Void, Never>?
    private var priorTiers: [String: Int] = [:]
    private var emittedAt: [String: Date] = [:]

    var helmetStatePublisher: AnyPublisher<HelmetState, Never> { stateSubject.eraseToAnyPublisher() }
    var safetyEventPublisher: AnyPublisher<SafetyEvent, Never> { eventSubject.eraseToAnyPublisher() }
    var activeScenarioPublisher: AnyPublisher<HelmetSimulationScenario, Never> { scenarioSubject.eraseToAnyPublisher() }

    init(settings: AppSettings) {
        self.settings = settings
        mock.helmetStatePublisher.sink { [weak self] state in
            guard self?.settings.dataMode == .mock else { return }
            self?.stateSubject.send(state)
        }.store(in: &cancellables)
        mock.activeScenarioPublisher.sink { [weak self] in self?.scenarioSubject.send($0) }.store(in: &cancellables)
        Publishers.CombineLatest(settings.$dataMode, settings.$piEndpoint)
            .removeDuplicates { $0.0 == $1.0 && $0.1 == $1.1 }
            .sink { [weak self] mode, _ in self?.configure(mode: mode) }
            .store(in: &cancellables)
    }

    deinit { pollTask?.cancel() }

    func startRide() { if settings.dataMode == .mock { mock.startRide() } }
    func endRide() { if settings.dataMode == .mock { mock.endRide() } }
    func trigger(_ scenario: HelmetSimulationScenario) { mock.trigger(scenario) }

    private func configure(mode: HelmetDataMode) {
        pollTask?.cancel()
        priorTiers.removeAll()
        guard mode == .real else {
            // Re-publish the simulator's current state after switching modes.
            mock.trigger(.noHazard)
            return
        }
        stateSubject.send(.disconnected)
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.pollOnce()
                try? await Task.sleep(for: .milliseconds(250))
            }
        }
    }

    private func pollOnce() async {
        guard let url = settings.piURL(path: "/api/v1/state") else { return }
        do {
            var request = URLRequest(url: url)
            request.cachePolicy = .reloadIgnoringLocalCacheData
            request.timeoutInterval = 2
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            let snapshot = try JSONDecoder().decode(PiSnapshot.self, from: data)
            consume(snapshot)
        } catch {
            var state = stateSubject.value
            state.isConnected = false
            state.gpsStatus = .unavailable
            stateSubject.send(state)
        }
    }

    private func consume(_ snapshot: PiSnapshot) {
        let hazards = snapshot.cars.filter { $0.tier > 0 && $0.live }
        let top = hazards.max { $0.tier < $1.tier }
        let maxTier = top?.tier ?? 0
        let left = hazards.contains { $0.zone == "LEFT" }
        let right = hazards.contains { $0.zone == "RIGHT" }
        let severity: HazardSeverity = maxTier >= 3 ? .high : maxTier == 2 ? .medium : maxTier == 1 ? .low : .safe
        let safety: SafetyLevel = maxTier >= 3 ? .danger : maxTier > 0 ? .caution : .safe
        var state = stateSubject.value
        state.isConnected = true
        state.gpsStatus = .unavailable
        state.safetyStatus = safety
        state.leftHazard = left
        state.rightHazard = right
        state.detectedObject = top?.label.capitalized
        state.estimatedDistance = top?.measured ?? top?.z
        state.severity = severity
        state.detectedObjectCount = snapshot.cars.filter(\.live).count
        state.cameraFPS = snapshot.fps
        state.detectionLatencyMS = snapshot.detMs
        state.cameraFault = snapshot.fault
        state.serialStatus = snapshot.serial
        state.sensitivityProfile = snapshot.profile
        state.sonarDistances = snapshot.sonar
        state.rideHazardCount += newlyRaisedEvents(in: snapshot).count
        stateSubject.send(state)
    }

    private func newlyRaisedEvents(in snapshot: PiSnapshot) -> [SafetyEvent] {
        let now = Date()
        var result: [SafetyEvent] = []
        var active = Set<String>()
        for car in snapshot.cars where car.live {
            let key = String(describing: car.id)
            active.insert(key)
            let previous = priorTiers[key] ?? 0
            priorTiers[key] = car.tier
            guard car.tier >= 2, car.tier > previous,
                  now.timeIntervalSince(emittedAt[key] ?? .distantPast) > 5 else { continue }
            emittedAt[key] = now
            let side: SafetyEventSide = car.zone == "LEFT" ? .left : car.zone == "RIGHT" ? .right : .rear
            let event = SafetyEvent(
                id: UUID(), timestamp: now, eventType: car.measured != nil ? .closePass : .vehicleApproach,
                severity: car.tier >= 3 ? .high : .medium, cameraId: "helmet-camera", side: side,
                detectedObject: car.label.capitalized, estimatedDistanceMeters: car.measured ?? car.z,
                confidence: car.confidence, latitude: nil, longitude: nil, speed: nil,
                videoPath: nil, videoURL: nil, aiSummary: nil,
                aiAnalysisState: .notAnalyzed, notes: car.reason)
            result.append(event)
            eventSubject.send(event)
        }
        priorTiers = priorTiers.filter { active.contains($0.key) }
        return result
    }
}

private struct PiSnapshot: Decodable {
    let cars: [PiCar]
    let fault: Bool
    let sonar: [String: Double?]
    let fps: Double
    let detMs: Double
    let serial: String
    let profile: String

    enum CodingKeys: String, CodingKey { case cars, fault, sonar, fps, serial, profile; case detMs = "det_ms" }
}

private struct PiCar: Decodable {
    let id: PiID
    let label: String
    let confidence: Double?
    let zone: String
    let tier: Int
    let z: Double
    let measured: Double?
    let live: Bool
    let reason: String
}

private enum PiID: Decodable, CustomStringConvertible {
    case integer(Int), string(String)
    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if let number = try? value.decode(Int.self) { self = .integer(number) }
        else { self = .string(try value.decode(String.self)) }
    }
    var description: String { switch self { case .integer(let value): "\(value)"; case .string(let value): value } }
}

private extension HelmetState {
    static let disconnected = HelmetState(
        isConnected: false, batteryPercentage: 0, gpsStatus: .searching, speedMPH: 0,
        safetyStatus: .safe, leftHazard: false, rightHazard: false, detectedObject: nil,
        estimatedDistance: nil, severity: .safe, navigationInstruction: "No active route",
        distanceToTurnFeet: 0, rideHazardCount: 0)
}
