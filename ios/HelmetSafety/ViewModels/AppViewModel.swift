import Foundation

final class AppViewModel: ObservableObject {
    let helmetService: HelmetSimulationProviding
    let locationService: LocationProviding
    let motionService: MotionProviding
    let navigationService: NavigationProviding
    let safetyEventRepository: SafetyEventStoring
    let guardianService: GuardianProviding

    init(
        helmetService: HelmetSimulationProviding = MockHelmetService(),
        locationService: LocationProviding = LocationService(),
        motionService: MotionProviding = MotionService()
    ) {
        self.helmetService = helmetService
        self.locationService = locationService
        self.motionService = motionService
        self.navigationService = NavigationService()
        self.safetyEventRepository = SafetyEventRepository()
        self.guardianService = GuardianService(helmetService: helmetService, locationService: locationService)
        self.motionService.startUpdates()
    }
}
