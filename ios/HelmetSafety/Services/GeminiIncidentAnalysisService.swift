import Foundation

@MainActor
protocol IncidentAnalysisProviding {
    func analyze(_ event: SafetyEvent) async throws -> String
}

enum IncidentAnalysisError: LocalizedError {
    case unavailable
    var errorDescription: String? { "Secure Gemini analysis is not configured. Use mock mode for a development preview." }
}

@MainActor
final class GeminiIncidentAnalysisService: IncidentAnalysisProviding {
    private weak var settings: AppSettings?
    init(settings: AppSettings? = nil) { self.settings = settings }

    func analyze(_ event: SafetyEvent) async throws -> String {
        guard settings?.dataMode != .real else { throw IncidentAnalysisError.unavailable }
        try await Task.sleep(for: .milliseconds(700))
        let object = event.detectedObject?.lowercased() ?? "object"
        let side = event.side.map { " from the \($0.rawValue)" } ?? ""
        let distance = event.estimatedDistanceMeters.map { String(format: " Minimum estimated separation was %.1f meters.", $0) } ?? ""
        return "This appears to be a \(event.eventType.displayName.lowercased()) involving a \(object)\(side). Pi-generated tracking data indicates \(event.severity.rawValue) severity.\(distance) Review the available ride context before drawing conclusions."
    }
}
