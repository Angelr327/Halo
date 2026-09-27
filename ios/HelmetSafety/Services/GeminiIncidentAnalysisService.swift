import Foundation

@MainActor
protocol IncidentAnalysisProviding {
    func analyze(_ event: SafetyEvent) async throws -> String
}

enum IncidentAnalysisError: LocalizedError {
    case unavailable(String)
    var errorDescription: String? {
        switch self { case .unavailable(let message): message }
    }
}

@MainActor
final class GeminiIncidentAnalysisService: IncidentAnalysisProviding {
    private weak var settings: AppSettings?
    init(settings: AppSettings? = nil) { self.settings = settings }

    func analyze(_ event: SafetyEvent) async throws -> String {
        if settings?.dataMode == .real {
            return try await analyzeWithPi(event)
        }
        try await Task.sleep(for: .milliseconds(250))
        return localSummary(event)
    }

    private func analyzeWithPi(_ event: SafetyEvent) async throws -> String {
        guard let incidentID = event.incidentID ?? event.videoPath,
              let analyzeURL = settings?.piURL(path: "/api/v1/incidents/\(incidentID)/analyze"),
              let detailURL = settings?.piURL(path: "/api/v1/incidents/\(incidentID)") else {
            return localSummary(event)
        }
        var request = URLRequest(url: analyzeURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 15
        let (_, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw IncidentAnalysisError.unavailable("The Pi returned an invalid response.")
        }
        guard (200..<300).contains(http.statusCode) else { return localSummary(event) }
        for _ in 0..<15 {
            try await Task.sleep(for: .seconds(1))
            let (data, detailResponse) = try await URLSession.shared.data(from: detailURL)
            guard let detailHTTP = detailResponse as? HTTPURLResponse, (200..<300).contains(detailHTTP.statusCode),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
            if let analysis = object["analysis"] as? [String: Any],
               let summary = analysis["summary"] as? String, !summary.isEmpty { return summary }
            if let status = object["analysis_status"] as? String,
               status == "not_configured" || status == "failed" { return localSummary(event) }
        }
        return localSummary(event)
    }

    private func localSummary(_ event: SafetyEvent) -> String {
        let object = event.detectedObject?.lowercased() ?? "object"
        let side = event.side.map { " from the \($0.rawValue)" } ?? ""
        let distance = event.estimatedDistanceMeters.map { String(format: " Minimum estimated separation was %.1f meters.", $0) } ?? ""
        let confidence = event.confidence.map { String(format: " Detection confidence was %.0f%%.", $0 * 100) } ?? ""
        let speed = event.speed.map { String(format: " Rider speed was %.1f mph.", $0) } ?? ""
        let telemetry = event.notes.flatMap { $0.isEmpty ? nil : " Helmet telemetry: \($0)." } ?? ""
        return "Helmet computer vision classified this as a \(event.severity.rawValue)-severity \(event.eventType.displayName.lowercased()) involving a \(object)\(side).\(distance)\(confidence)\(speed)\(telemetry) This is a telemetry summary, not a determination of fault."
    }
}
