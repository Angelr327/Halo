import Combine
import Foundation

protocol SafetyEventStoring: AnyObject {
    var eventsPublisher: AnyPublisher<[SafetyEvent], Never> { get }
    func add(_ event: SafetyEvent)
    func update(_ event: SafetyEvent)
    func setMockMode(_ enabled: Bool)
    func event(id: UUID) -> SafetyEvent?
}

final class SafetyEventRepository: SafetyEventStoring {
    private let subject: CurrentValueSubject<[SafetyEvent], Never>
    private let fileURL: URL
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(fileManager: FileManager = .default, includeMockEvents: Bool = true) {
        let directory = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? fileManager.temporaryDirectory
        let appDirectory = directory.appendingPathComponent("HelmetSafety", isDirectory: true)
        try? fileManager.createDirectory(at: appDirectory, withIntermediateDirectories: true)
        fileURL = appDirectory.appendingPathComponent("safety-events.json")

        let configuredEncoder = JSONEncoder()
        configuredEncoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        configuredEncoder.dateEncodingStrategy = .iso8601
        encoder = configuredEncoder
        let configuredDecoder = JSONDecoder()
        configuredDecoder.dateDecodingStrategy = .iso8601
        decoder = configuredDecoder

        let storedEvents = (try? Data(contentsOf: fileURL)).flatMap { data in
            try? configuredDecoder.decode([SafetyEvent].self, from: data)
        }
        let mockEvents = storedEvents.flatMap { $0.isEmpty ? nil : $0 } ?? Self.mockEvents
        let initialEvents = includeMockEvents ? mockEvents : []
        subject = CurrentValueSubject(initialEvents.sorted { $0.timestamp > $1.timestamp })
        if storedEvents == nil { persist(Self.mockEvents) }
    }

    var eventsPublisher: AnyPublisher<[SafetyEvent], Never> { subject.eraseToAnyPublisher() }

    func add(_ event: SafetyEvent) {
        var events = subject.value
        events.append(event)
        events.sort { $0.timestamp > $1.timestamp }
        subject.send(events)
        persist(events)
    }

    func update(_ event: SafetyEvent) {
        var events = subject.value
        guard let index = events.firstIndex(where: { $0.id == event.id }) else { return }
        events[index] = event
        subject.send(events)
        persist(events)
    }

    func event(id: UUID) -> SafetyEvent? { subject.value.first { $0.id == id } }

    func setMockMode(_ enabled: Bool) {
        if enabled {
            let stored = (try? Data(contentsOf: fileURL)).flatMap { try? decoder.decode([SafetyEvent].self, from: $0) }
            let events = stored.flatMap { $0.isEmpty ? nil : $0 } ?? Self.mockEvents
            subject.send(events.sorted { $0.timestamp > $1.timestamp })
        } else {
            subject.send([])
        }
    }

    private func persist(_ events: [SafetyEvent]) {
        guard let data = try? encoder.encode(events) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }

    static let mockEvents: [SafetyEvent] = [
        SafetyEvent(id: UUID(), timestamp: Date().addingTimeInterval(-180), eventType: .closePass, severity: .high, cameraId: "helmet-camera", side: .left, detectedObject: "SUV", estimatedDistanceMeters: 0.8, confidence: 0.91, latitude: 28.5384, longitude: -81.3789, speed: 16.4, videoPath: nil, videoURL: nil, aiSummary: "This appears to be a close-pass event involving a vehicle approaching from the rear-left.", aiAnalysisState: .available, notes: "Vehicle passed inside the configured safety zone."),
        SafetyEvent(id: UUID(), timestamp: Date().addingTimeInterval(-620), eventType: .vehicleApproach, severity: .medium, cameraId: "helmet-camera", side: .right, detectedObject: "Sedan", estimatedDistanceMeters: 3.2, confidence: 0.84, latitude: 28.5411, longitude: -81.3812, speed: 13.8, videoPath: nil, videoURL: nil, aiSummary: nil, aiAnalysisState: .notAnalyzed, notes: "Fast approach detected from the right rear quarter."),
        SafetyEvent(id: UUID(), timestamp: Date().addingTimeInterval(-1_460), eventType: .hardBrake, severity: .low, cameraId: nil, side: nil, detectedObject: nil, estimatedDistanceMeters: nil, confidence: nil, latitude: nil, longitude: nil, speed: 11.1, videoPath: nil, videoURL: nil, aiSummary: nil, aiAnalysisState: .notAnalyzed, notes: "Sudden deceleration detected during this mock ride."),
        SafetyEvent(id: UUID(), timestamp: Date().addingTimeInterval(-3_400), eventType: .possibleCollision, severity: .critical, cameraId: "helmet-camera", side: .rear, detectedObject: "Pickup truck", estimatedDistanceMeters: 0.4, confidence: 0.94, latitude: 28.5358, longitude: -81.3750, speed: 18.2, videoPath: "rec_mock_001.mp4", videoURL: nil, aiSummary: nil, aiAnalysisState: .notAnalyzed, notes: "Mock incident demonstrates a recording reference that the current Pi cannot serve over HTTP.")
    ]
}
