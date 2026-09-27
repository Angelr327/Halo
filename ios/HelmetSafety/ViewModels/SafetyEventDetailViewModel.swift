import Foundation

@MainActor
final class SafetyEventDetailViewModel: ObservableObject {
    @Published private(set) var event: SafetyEvent
    @Published private(set) var analysisState: IncidentAnalysisState
    @Published private(set) var analysisError: String?
    private let repository: SafetyEventStoring
    private let analysisService: IncidentAnalysisProviding

    init(event: SafetyEvent, repository: SafetyEventStoring, analysisService: IncidentAnalysisProviding) {
        self.event = event
        self.repository = repository
        self.analysisService = analysisService
        analysisState = event.aiSummary == nil ? (event.aiAnalysisState ?? .notAnalyzed) : .available
    }

    func analyze() async {
        analysisState = .analyzing
        analysisError = nil
        do {
            event.aiSummary = try await analysisService.analyze(event)
            event.aiAnalysisState = .available
            analysisState = .available
        } catch {
            event.aiAnalysisState = .failed
            analysisState = .failed
            analysisError = error.localizedDescription
        }
        repository.update(event)
    }
}
