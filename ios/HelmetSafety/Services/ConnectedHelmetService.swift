import Combine
import Foundation

/// Switches between the existing simulator and the Pi's `/state` Server-Sent Events stream.
@MainActor
final class ConnectedHelmetService: HelmetSimulationProviding {
    private let settings: AppSettings
    private let mock = MockHelmetService()
    private let stateSubject = CurrentValueSubject<HelmetState, Never>(.disconnected)
    private let eventSubject = PassthroughSubject<SafetyEvent, Never>()
    private let scenarioSubject = CurrentValueSubject<HelmetSimulationScenario, Never>(.noHazard)
    private var cancellables = Set<AnyCancellable>()
    private var streamClient: SSEStreamClient?
    private var reconnectTask: Task<Void, Never>?
    private var watchdogTask: Task<Void, Never>?
    private var incidentSyncTask: Task<Void, Never>?
    private var priorTiers: [String: Int] = [:]
    private var emittedAt: [String: Date] = [:]
    private var priorFrontState = "CLEAR"
    private var priorFrontTargetID: String?
    private var lastSyncedIncidentID: String?
    private var hasSyncedIncidentHistory = false
    private var lastIncidentSyncAttempt = Date.distantPast
    private var lastTelemetryAt: ContinuousClock.Instant?
    private let telemetryTimeout: Duration = .seconds(4)
    private var retryDelay = 1.0
    private var streamGeneration = UUID()

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

    deinit {
        streamClient?.cancel()
        reconnectTask?.cancel()
        watchdogTask?.cancel()
        incidentSyncTask?.cancel()
    }

    func startRide() { if settings.dataMode == .mock { mock.startRide() } }
    func endRide() { if settings.dataMode == .mock { mock.endRide() } }
    func trigger(_ scenario: HelmetSimulationScenario) { mock.trigger(scenario) }

    private func configure(mode: HelmetDataMode) {
        streamGeneration = UUID()
        streamClient?.cancel()
        streamClient = nil
        reconnectTask?.cancel()
        watchdogTask?.cancel()
        incidentSyncTask?.cancel()
        priorTiers.removeAll()
        priorFrontState = "CLEAR"
        priorFrontTargetID = nil
        lastSyncedIncidentID = nil
        hasSyncedIncidentHistory = false
        lastIncidentSyncAttempt = .distantPast
        guard mode == .real else {
            // Re-publish the simulator's current state after switching modes.
            mock.trigger(.noHazard)
            return
        }
        var state = HelmetState.disconnected
        state.connectionStatus = "Reconnecting"
        stateSubject.send(state)
        lastTelemetryAt = nil
        startWatchdog()
        startStream()
    }

    private func startStream() {
        guard settings.dataMode == .real, let url = settings.piURL(path: "/state") else {
            markDisconnected(status: "Invalid endpoint")
            return
        }
        let generation = UUID()
        streamGeneration = generation
        let client = SSEStreamClient(
            url: url,
            onData: { [weak self] data in
                Task { @MainActor in self?.handleSSEData(data, generation: generation) }
            },
            onRetry: { [weak self] seconds in
                Task { @MainActor in self?.retryDelay = seconds }
            },
            onDisconnect: { [weak self] error in
                Task { @MainActor in self?.streamDisconnected(error, generation: generation) }
            }
        )
        streamClient = client
        client.start()
    }

    private func startWatchdog() {
        watchdogTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard let self, settings.dataMode == .real, let lastTelemetryAt else { continue }
                if lastTelemetryAt.duration(to: .now) > telemetryTimeout {
                    self.lastTelemetryAt = nil
                    markDisconnected(status: "Reconnecting")
                    streamClient?.cancel()
                }
            }
        }
    }

    private func handleSSEData(_ data: Data, generation: UUID) {
        guard generation == streamGeneration,
              let snapshot = try? JSONDecoder().decode(PiSnapshot.self, from: data) else { return }
        consume(snapshot)
    }

    private func streamDisconnected(_ error: Error?, generation: UUID) {
        guard generation == streamGeneration, settings.dataMode == .real else { return }
        streamClient = nil
        markDisconnected(status: "Reconnecting")
        reconnectTask?.cancel()
        reconnectTask = Task { [weak self] in
            guard let self else { return }
            try? await Task.sleep(for: .seconds(retryDelay))
            guard !Task.isCancelled, settings.dataMode == .real else { return }
            startStream()
        }
    }

    private func markDisconnected(status: String) {
        var state = stateSubject.value
        state.isConnected = false
        state.connectionStatus = status
        stateSubject.send(state)
    }

    private func consume(_ snapshot: PiSnapshot) {
        lastTelemetryAt = .now
        let hazards = snapshot.cars.filter { $0.tier > 0 && $0.live }
        let top = hazards.max { $0.tier < $1.tier }
        let maxTier = top?.tier ?? 0
        let frontState = snapshot.collision?.state ?? "CLEAR"
        let frontBrake = frontState == "BRAKE"
        let frontTier = frontBrake ? 3 : frontState == "CAUTION" ? 2 : 0
        let combinedTier = max(maxTier, frontTier)
        let left = hazards.contains { $0.zone == "LEFT" }
        let right = hazards.contains { $0.zone == "RIGHT" }
        let severity: HazardSeverity = combinedTier >= 3 ? .high : combinedTier == 2 ? .medium : combinedTier == 1 ? .low : .safe
        let safety: SafetyLevel = combinedTier >= 3 ? .danger : combinedTier > 0 ? .caution : .safe
        var state = stateSubject.value
        state.isConnected = true
        state.connectionStatus = "Online"
        state.gpsStatus = .unavailable
        state.safetyStatus = safety
        state.leftHazard = left
        state.rightHazard = right
        state.detectedObject = frontBrake ? snapshot.collision?.detectedLabel?.capitalized : top?.label.capitalized
        state.estimatedDistance = frontBrake ? snapshot.collision?.distanceM : top?.measured ?? top?.z
        state.estimatedTTC = frontBrake ? snapshot.collision?.ttcS : top?.ttc
        state.currentVehicleID = frontBrake ? snapshot.collision?.targetID : top.map { String(describing: $0.id) }
        state.alertTier = combinedTier
        state.severity = severity
        state.detectedObjectCount = snapshot.cars.filter(\.live).count
        state.cameraFPS = snapshot.fps
        state.detectionLatencyMS = snapshot.detMs
        state.cameraFault = snapshot.fault
        state.serialStatus = snapshot.serial
        state.sensitivityProfile = snapshot.profile
        state.sonarDistances = snapshot.sonar
        state.cameraShaky = snapshot.shaky
        state.rearLightLevel = snapshot.light
        state.latestCaption = snapshot.caption?.text
        state.sceneDescription = snapshot.scene
        state.corridorHalfWidthMeters = snapshot.corridorHalfM
        state.demoPersonMode = snapshot.demoPerson
        state.hudAlertTiers = snapshot.hud
        state.sonarMounts = snapshot.sonarMount.mapValues {
            SonarMountState(zone: $0.zone, yawDegrees: $0.yaw, offsetMeters: $0.offset)
        }
        state.frontBrakeActive = frontBrake
        state.frontWarningState = frontState
        state.frontTargetID = snapshot.collision?.targetID
        state.frontDetectedObject = snapshot.collision?.detectedLabel?.capitalized
        state.frontDistanceMeters = snapshot.collision?.distanceM
        state.frontTTCSeconds = snapshot.collision?.ttcS
        state.frontReason = snapshot.collision?.reason
        state.frontObstacleCount = snapshot.frontObstacles.count
        let events = newlyRaisedEvents(in: snapshot) + newlyRaisedFrontEvents(in: snapshot)
        state.rideHazardCount += events.count
        stateSubject.send(state)
        syncIncidentsIfNeeded(latestID: snapshot.incidents?.latest)
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
            // Every positive tier is an actual safety classification produced by the Pi.
            // Tier 1 is a low-risk approaching vehicle, while tiers 2 and 3 are the
            // progressively more urgent close-pass/imminent states. Plain tracking
            // remains tier 0 and must not create an incident.
            guard car.tier >= 1, car.tier > previous,
                  now.timeIntervalSince(emittedAt[key] ?? .distantPast) > 5 else { continue }
            emittedAt[key] = now
            let side: SafetyEventSide = car.zone == "LEFT" ? .left : car.zone == "RIGHT" ? .right : .rear
            let incident = snapshot.incidents?.current.flatMap { ref in
                ref.trackID.map(String.init) == key ? ref : nil
            }
            let videoURL = incident.flatMap { settings.piURL(path: "/api/v1/incidents/\($0.id)/clip.mp4") }
            let event = SafetyEvent(
                id: UUID(), timestamp: now, eventType: car.measured != nil ? .closePass : .vehicleApproach,
                severity: eventSeverity(for: car.tier), cameraId: "rear", side: side,
                detectedObject: car.label.capitalized, estimatedDistanceMeters: car.measured ?? car.z,
                confidence: car.confidence, latitude: nil, longitude: nil, speed: nil,
                videoPath: incident?.id, videoURL: videoURL, aiSummary: nil,
                aiAnalysisState: .notAnalyzed, notes: eventNotes(for: car), objectID: key,
                zone: car.zone, tier: car.tier, ttcSeconds: car.ttc, incidentID: incident?.id)
            result.append(event)
            eventSubject.send(event)
        }
        priorTiers = priorTiers.filter { active.contains($0.key) }
        return result
    }

    private func newlyRaisedFrontEvents(in snapshot: PiSnapshot) -> [SafetyEvent] {
        guard let collision = snapshot.collision else {
            priorFrontState = "CLEAR"
            priorFrontTargetID = nil
            return []
        }
        defer {
            priorFrontState = collision.state
            priorFrontTargetID = collision.targetID
        }
        guard collision.state == "BRAKE",
              priorFrontState != "BRAKE" || priorFrontTargetID != collision.targetID else { return [] }
        let now = Date()
        let target = collision.targetID ?? "front:unknown"
        let key = "front-brake:\(target)"
        guard now.timeIntervalSince(emittedAt[key] ?? .distantPast) > 4 else { return [] }
        emittedAt[key] = now
        let event = SafetyEvent(
            id: UUID(), timestamp: now, eventType: .emergencyBrakeWarning, severity: .critical,
            cameraId: "front", side: .front, detectedObject: collision.detectedLabel?.capitalized,
            estimatedDistanceMeters: collision.distanceM, confidence: collision.confidence,
            latitude: nil, longitude: nil, speed: nil, videoPath: nil, videoURL: nil,
            aiSummary: nil, aiAnalysisState: .notAnalyzed, notes: collision.reason,
            objectID: target, zone: "FRONT", tier: 3, ttcSeconds: collision.ttcS, incidentID: nil)
        eventSubject.send(event)
        return [event]
    }

    private func syncIncidentsIfNeeded(latestID: String?) {
        let needsRefresh = !hasSyncedIncidentHistory || (latestID != nil && latestID != lastSyncedIncidentID)
        guard needsRefresh, incidentSyncTask == nil,
              Date().timeIntervalSince(lastIncidentSyncAttempt) >= 2,
              let url = settings.piURL(path: "/api/v1/incidents") else { return }
        lastIncidentSyncAttempt = Date()
        incidentSyncTask = Task { [weak self] in
            guard let self else { return }
            defer { incidentSyncTask = nil }
            do {
                let (data, response) = try await URLSession.shared.data(from: url)
                guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { return }
                let list = try JSONDecoder().decode(PiIncidentList.self, from: data)
                guard settings.dataMode == .real else { return }
                for incident in list.incidents.reversed() {
                    eventSubject.send(incident.safetyEvent(baseURL: settings.normalizedBaseURL))
                }
                hasSyncedIncidentHistory = true
                lastSyncedIncidentID = latestID ?? list.incidents.first?.id
            } catch {
                // SSE remains authoritative for live safety. A later state message retries
                // this optional history sync if the incident file is still being written.
            }
        }
    }

    private func eventSeverity(for tier: Int) -> SafetyEventSeverity {
        switch tier {
        case 3...: .high
        case 2: .medium
        default: .low
        }
    }

    private func eventNotes(for car: PiCar) -> String {
        guard let ttc = car.ttc else { return car.reason }
        return car.reason.isEmpty ? String(format: "TTC %.1f seconds", ttc)
            : "\(car.reason) · TTC \(String(format: "%.1f", ttc)) seconds"
    }
}

/// Incremental SSE transport. URLSession delivers each HTTP body chunk here without waiting
/// for the never-ending response to complete, which is required for the Pi's HTTP/1.0 stream.
private final class SSEStreamClient: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private let url: URL
    private let onData: @Sendable (Data) -> Void
    private let onRetry: @Sendable (Double) -> Void
    private let onDisconnect: @Sendable (Error?) -> Void
    private var buffer = Data()
    private var session: URLSession?
    private var task: URLSessionDataTask?
    private var finished = false

    init(url: URL, onData: @escaping @Sendable (Data) -> Void,
         onRetry: @escaping @Sendable (Double) -> Void,
         onDisconnect: @escaping @Sendable (Error?) -> Void) {
        self.url = url
        self.onData = onData
        self.onRetry = onRetry
        self.onDisconnect = onDisconnect
    }

    func start() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 10
        configuration.timeoutIntervalForResource = 60 * 60 * 24
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 1
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: queue)
        var request = URLRequest(url: url)
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
        self.session = session
        let task = session.dataTask(with: request)
        self.task = task
        task.resume()
    }

    func cancel() {
        task?.cancel()
        session?.invalidateAndCancel()
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            completionHandler(.cancel)
            return
        }
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        drainEvents()
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didCompleteWithError error: Error?) {
        guard !finished else { return }
        finished = true
        onDisconnect(error)
        session.finishTasksAndInvalidate()
    }

    private func drainEvents() {
        let lf = Data([0x0A, 0x0A])
        let crlf = Data([0x0D, 0x0A, 0x0D, 0x0A])
        while true {
            let lfRange = buffer.range(of: lf)
            let crlfRange = buffer.range(of: crlf)
            guard let boundary = [lfRange, crlfRange].compactMap({ $0 }).min(by: { $0.lowerBound < $1.lowerBound }) else { return }
            let block = buffer.subdata(in: buffer.startIndex..<boundary.lowerBound)
            buffer.removeSubrange(buffer.startIndex..<boundary.upperBound)
            parseEvent(block)
        }
    }

    private func parseEvent(_ block: Data) {
        guard let text = String(data: block, encoding: .utf8) else { return }
        var dataLines: [String] = []
        for rawLine in text.components(separatedBy: .newlines) {
            let line = rawLine.hasSuffix("\r") ? String(rawLine.dropLast()) : rawLine
            if line.hasPrefix(":") || line.isEmpty { continue }
            let pieces = line.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
            let field = String(pieces[0])
            let value = pieces.count > 1 ? String(pieces[1]).trimmingCharacters(in: .whitespaces) : ""
            if field == "data" {
                dataLines.append(value)
            } else if field == "retry", let milliseconds = Double(value), milliseconds >= 0 {
                onRetry(max(0.25, milliseconds / 1_000))
            }
        }
        guard !dataLines.isEmpty, let data = dataLines.joined(separator: "\n").data(using: .utf8) else { return }
        onData(data)
    }
}

private struct PiSnapshot: Decodable {
    let t: Double
    let cars: [PiCar]
    let hud: [String: Int]
    let fault: Bool
    let shaky: Bool
    let light: Int
    let sonar: [String: Double?]
    let fps: Double
    let detMs: Double
    let serial: String
    let profile: String
    let caption: PiCaption?
    let scene: String?
    let corridorHalfM: Double
    let demoPerson: Bool
    let sonarMount: [String: PiSonarMount]
    let frontObstacles: [PiFrontObstacle]
    let collision: PiCollision?
    let incidents: PiIncidents?

    enum CodingKeys: String, CodingKey {
        case t, cars, hud, fault, shaky, light, sonar, fps, serial, profile, caption, scene
        case detMs = "det_ms"
        case corridorHalfM = "corridor_half_m"
        case demoPerson = "demo_person"
        case sonarMount = "sonar_mount"
        case frontObstacles = "front_obstacles"
        case collision, incidents
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        t = try c.decode(Double.self, forKey: .t)
        cars = try c.decodeIfPresent([PiCar].self, forKey: .cars) ?? []
        hud = try c.decodeIfPresent([String: Int].self, forKey: .hud) ?? [:]
        fault = try c.decodeIfPresent(Bool.self, forKey: .fault) ?? false
        shaky = try c.decodeIfPresent(Bool.self, forKey: .shaky) ?? false
        light = try c.decodeIfPresent(Int.self, forKey: .light) ?? 0
        sonar = try c.decodeIfPresent([String: Double?].self, forKey: .sonar) ?? [:]
        fps = try c.decodeIfPresent(Double.self, forKey: .fps) ?? 0
        detMs = try c.decodeIfPresent(Double.self, forKey: .detMs) ?? 0
        serial = try c.decodeIfPresent(String.self, forKey: .serial) ?? ""
        profile = try c.decodeIfPresent(String.self, forKey: .profile) ?? ""
        caption = try c.decodeIfPresent(PiCaption.self, forKey: .caption)
        scene = try c.decodeIfPresent(String.self, forKey: .scene)
        corridorHalfM = try c.decodeIfPresent(Double.self, forKey: .corridorHalfM) ?? 0
        demoPerson = try c.decodeIfPresent(Bool.self, forKey: .demoPerson) ?? false
        sonarMount = try c.decodeIfPresent([String: PiSonarMount].self, forKey: .sonarMount) ?? [:]
        frontObstacles = try c.decodeIfPresent([PiFrontObstacle].self, forKey: .frontObstacles) ?? []
        collision = try c.decodeIfPresent(PiCollision.self, forKey: .collision)
        incidents = try c.decodeIfPresent(PiIncidents.self, forKey: .incidents)
    }
}

private struct PiCollision: Decodable {
    let state: String
    let reason: String?
    let targetID: String?
    let detectedLabel: String?
    let distanceM: Double?
    let ttcS: Double?
    let confidence: Double?

    enum CodingKeys: String, CodingKey {
        case state, reason, confidence
        case targetID = "target_id"
        case detectedLabel = "detected_label"
        case distanceM = "distance_m"
        case ttcS = "ttc_s"
    }
}

private struct PiFrontObstacle: Decodable {
    let id: String
    let label: String
    let tier: Int
    let live: Bool
    let ttc: Double?
    let confidence: Double?
}

private struct PiIncidents: Decodable {
    let latest: String?
    let recording: Bool
    let current: PiIncidentReference?
}

private struct PiIncidentReference: Decodable {
    let id: String
    let trackID: Int?
    enum CodingKeys: String, CodingKey { case id; case trackID = "track_id" }
}

private struct PiIncidentList: Decodable {
    let incidents: [PiIncident]
}

private struct PiIncident: Decodable {
    let id: String
    let time: String
    let kind: String
    let tier: Int
    let severity: String
    let zone: String
    let side: String
    let label: String
    let reason: String
    let trackID: Int?
    let measuredClearanceM: Double?
    let minTTCS: Double?
    let ttcAtAlertS: Double?
    let localSummary: String?
    let clipURL: String?
    let analysis: PiIncidentAnalysis?
    let analysisStatus: String?

    enum CodingKeys: String, CodingKey {
        case id, time, kind, tier, severity, zone, side, label, reason, analysis
        case trackID = "track_id"
        case measuredClearanceM = "measured_clearance_m"
        case minTTCS = "min_ttc_s"
        case ttcAtAlertS = "ttc_at_alert_s"
        case localSummary = "local_summary"
        case clipURL = "clip_url"
        case analysisStatus = "analysis_status"
    }

    func safetyEvent(baseURL: URL?) -> SafetyEvent {
        let eventType: SafetyEventType
        if kind == "manual" { eventType = .manualRecording }
        else if measuredClearanceM != nil || zone == "LEFT" || zone == "RIGHT" { eventType = .closePass }
        else { eventType = .vehicleApproach }
        let eventSide: SafetyEventSide = switch zone {
        case "LEFT": .left
        case "RIGHT": .right
        default: .rear
        }
        let eventSeverity: SafetyEventSeverity = switch severity.uppercased() {
        case "HIGH": .high
        case "MED", "MEDIUM": .medium
        case "LOW": .low
        default: tier >= 3 ? .high : tier == 2 ? .medium : .low
        }
        let analysisState: IncidentAnalysisState = switch analysisStatus {
        case "done": .available
        case "pending": .analyzing
        case "failed", "budget_exhausted": .failed
        default: .notAnalyzed
        }
        let videoURL: URL?
        if let clipURL, let baseURL {
            videoURL = URL(string: clipURL, relativeTo: baseURL)?.absoluteURL
        } else {
            videoURL = nil
        }
        return SafetyEvent(
            id: UUID(), timestamp: Self.parseTimestamp(time), eventType: eventType,
            severity: eventSeverity, cameraId: "rear", side: eventSide,
            detectedObject: label.capitalized, estimatedDistanceMeters: measuredClearanceM,
            confidence: nil, latitude: nil, longitude: nil, speed: nil,
            videoPath: id, videoURL: videoURL, aiSummary: analysis?.summary,
            aiAnalysisState: analysisState, notes: localSummary ?? reason,
            objectID: trackID.map(String.init), zone: zone, tier: tier,
            ttcSeconds: ttcAtAlertS ?? minTTCS, incidentID: id, isHistorical: true)
    }

    private static func parseTimestamp(_ value: String) -> Date {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return formatter.date(from: value) ?? Date()
    }
}

private struct PiIncidentAnalysis: Decodable {
    let summary: String?
}

private struct PiCaption: Decodable {
    let text: String
    let src: String
    let age: Double
}

private struct PiSonarMount: Decodable {
    let zone: String
    let yaw: Double
    let offset: Double
}

private struct PiCar: Decodable {
    let id: PiID
    let label: String
    let confidence: Double?
    let x: Double
    let zone: String
    let tier: Int
    let z: Double
    let ttc: Double?
    let path: Double?
    let onPath: Bool
    let alongside: Bool
    let measured: Double?
    let live: Bool
    let reason: String

    enum CodingKeys: String, CodingKey {
        case id, label, confidence, x, zone, tier, z, ttc, path, alongside, measured, live, reason
        case onPath = "on_path"
    }
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
        distanceToTurnFeet: 0, rideHazardCount: 0, connectionStatus: "Offline")
}
