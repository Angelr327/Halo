import Combine
import Foundation
import UIKit

@MainActor
final class AppViewModel: ObservableObject {
    let helmetService: HelmetSimulationProviding
    let locationService: LocationProviding
    let motionService: MotionProviding
    let navigationService: NavigationProviding
    let destinationSearchService: DestinationSearchService
    let safetyEventRepository: SafetyEventStoring
    let incidentAnalysisService: IncidentAnalysisProviding
    let settings: AppSettings
    private let voiceService: NavigationVoiceProviding
    private var latestLocation = LocationSnapshot.initial

    init(
        helmetService: HelmetSimulationProviding? = nil,
        locationService: LocationProviding = LocationService(),
        motionService: MotionProviding = MotionService()
    ) {
        self.settings = AppSettings()
        self.helmetService = helmetService ?? ConnectedHelmetService(settings: settings)
        self.locationService = locationService
        self.motionService = motionService
        let voiceService = NavigationVoiceService()
        self.voiceService = voiceService
        self.navigationService = NavigationService(voice: voiceService)
        self.destinationSearchService = DestinationSearchService()
        self.safetyEventRepository = SafetyEventRepository(includeMockEvents: settings.dataMode == .mock)
        self.incidentAnalysisService = GeminiIncidentAnalysisService(settings: settings)
        self.motionService.startUpdates()
        self.locationService.locationPublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.latestLocation = $0 }
            .store(in: &cancellables)
        self.helmetService.safetyEventPublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] event in
                guard let self else { return }
                var enriched = event
                if event.isHistorical != true {
                    enriched.latitude = latestLocation.latitude
                    enriched.longitude = latestLocation.longitude
                    enriched.speed = latestLocation.speedMPH
                }
                safetyEventRepository.add(enriched)
                guard enriched.isHistorical != true else { return }
                Self.playHazardHaptic(for: enriched)
                voiceService.speak(
                    Self.hazardAnnouncement(for: enriched),
                    priority: enriched.severity == .critical || enriched.severity == .high ? .criticalHazard : .hazard
                )
            }
            .store(in: &cancellables)
    }

    private var cancellables = Set<AnyCancellable>()

    private static func hazardAnnouncement(for event: SafetyEvent) -> String {
        if event.eventType == .emergencyBrakeWarning {
            let object = event.detectedObject.map { " \($0.lowercased()) ahead." } ?? " Obstacle ahead."
            return "Brake!\(object)"
        }
        let object = event.detectedObject?.lowercased() ?? "vehicle"
        let side: String
        switch event.side {
        case .front?: side = "ahead of you"
        case .left?: side = "on your left"
        case .right?: side = "on your right"
        case .rear?: side = "behind you"
        case .unknown?, nil: side = "nearby"
        }
        let distance = event.estimatedDistanceMeters.map {
            String(format: ", %.0f feet", $0 * 3.28084)
        } ?? ""
        if event.severity == .critical || event.severity == .high {
            return "Warning. \(object) very close \(side)\(distance)."
        }
        return "\(object.capitalized) approaching \(side)\(distance)."
    }

    private static func playHazardHaptic(for event: SafetyEvent) {
        if event.eventType == .emergencyBrakeWarning {
            UINotificationFeedbackGenerator().notificationOccurred(.error)
            return
        }
        if event.side == .rear && event.eventType == .vehicleApproach {
            UIImpactFeedbackGenerator(style: event.severity == .high || event.severity == .critical ? .heavy : .medium)
                .impactOccurred()
            return
        }
        if (event.side == .left || event.side == .right) &&
            (event.severity == .high || event.severity == .critical) {
            UINotificationFeedbackGenerator().notificationOccurred(.warning)
        }
    }
}
