import Combine
import Foundation

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
        self.navigationService = NavigationService()
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
                enriched.latitude = latestLocation.latitude
                enriched.longitude = latestLocation.longitude
                enriched.speed = latestLocation.speedMPH
                safetyEventRepository.add(enriched)
            }
            .store(in: &cancellables)
    }

    private var cancellables = Set<AnyCancellable>()
}
