import Combine
import Foundation

protocol SafetyEventStoring: AnyObject {
    var eventsPublisher: AnyPublisher<[SafetyEvent], Never> { get }
    func add(_ event: SafetyEvent)
    func event(id: UUID) -> SafetyEvent?
}

final class SafetyEventRepository: SafetyEventStoring {
    private let subject: CurrentValueSubject<[SafetyEvent], Never>
    private let fileURL: URL
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(fileManager: FileManager = .default) {
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
        let initialEvents = storedEvents?.isEmpty == false ? storedEvents! : Self.mockEvents
        subject = CurrentValueSubject(initialEvents.sorted { $0.timestamp > $1.timestamp })
        if storedEvents == nil { persist(initialEvents) }
    }

    var eventsPublisher: AnyPublisher<[SafetyEvent], Never> { subject.eraseToAnyPublisher() }

    func add(_ event: SafetyEvent) {
        var events = subject.value
        events.append(event)
        events.sort { $0.timestamp > $1.timestamp }
        subject.send(events)
        persist(events)
    }

    func event(id: UUID) -> SafetyEvent? { subject.value.first { $0.id == id } }

    private func persist(_ events: [SafetyEvent]) {
        guard let data = try? encoder.encode(events) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }

    static let mockEvents: [SafetyEvent] = [
        SafetyEvent(id: UUID(), timestamp: Date().addingTimeInterval(-180), eventType: .closePass, severity: .high, side: .left, detectedObject: "SUV", estimatedDistanceMeters: 0.8, latitude: 28.5384, longitude: -81.3789, speed: 16.4, videoPath: nil, notes: "Vehicle passed inside the configured safety zone."),
        SafetyEvent(id: UUID(), timestamp: Date().addingTimeInterval(-620), eventType: .vehicleApproach, severity: .medium, side: .right, detectedObject: "Sedan", estimatedDistanceMeters: 3.2, latitude: 28.5411, longitude: -81.3812, speed: 13.8, videoPath: nil, notes: "Fast approach detected from the right rear quarter."),
        SafetyEvent(id: UUID(), timestamp: Date().addingTimeInterval(-1_460), eventType: .hardBrake, severity: .low, side: .unknown, detectedObject: nil, estimatedDistanceMeters: nil, latitude: nil, longitude: nil, speed: 11.1, videoPath: nil, notes: "Sudden deceleration detected by phone motion sensors."),
        SafetyEvent(id: UUID(), timestamp: Date().addingTimeInterval(-3_400), eventType: .possibleCollision, severity: .critical, side: .rear, detectedObject: "Pickup truck", estimatedDistanceMeters: 0.4, latitude: 28.5358, longitude: -81.3750, speed: 18.2, videoPath: "incidents/mock-incident-001.mp4", notes: "Mock incident generated for development and UI testing.")
    ]
}
