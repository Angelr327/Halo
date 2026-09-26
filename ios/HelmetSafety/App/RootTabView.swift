import SwiftUI

struct RootTabView: View {
    @EnvironmentObject private var appViewModel: AppViewModel

    var body: some View {
        TabView {
            RideView(service: appViewModel.helmetService, locationService: appViewModel.locationService, navigationService: appViewModel.navigationService, guardianService: appViewModel.guardianService, safetyRepository: appViewModel.safetyEventRepository)
                .tabItem { Label("Ride", systemImage: "bicycle") }
            NavigationMapView(navigationService: appViewModel.navigationService, locationService: appViewModel.locationService, safetyRepository: appViewModel.safetyEventRepository)
                .tabItem { Label("Map", systemImage: "map.fill") }
            SafetyView(repository: appViewModel.safetyEventRepository)
                .tabItem { Label("Safety", systemImage: "chart.bar.fill") }
            GuardianView(service: appViewModel.guardianService)
                .tabItem { Label("Guardian", systemImage: "person.2.fill") }
            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape.fill") }
        }
        .tint(AppTheme.accent)
        .toolbarBackground(.visible, for: .tabBar)
        .toolbarBackground(AppTheme.surface, for: .tabBar)
        .background(EmergencyCheckInPresenter(service: appViewModel.guardianService))
    }
}
