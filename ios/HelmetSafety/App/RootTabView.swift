import SwiftUI

struct RootTabView: View {
    @EnvironmentObject private var appViewModel: AppViewModel
    @State private var selectedTab = 0
    @State private var rideStartRequest = 0

    var body: some View {
        TabView(selection: $selectedTab) {
            RideView(service: appViewModel.helmetService, locationService: appViewModel.locationService, navigationService: appViewModel.navigationService, destinationSearchService: appViewModel.destinationSearchService, safetyRepository: appViewModel.safetyEventRepository, startRequest: rideStartRequest)
                .tabItem { Label("Ride", systemImage: "bicycle") }
                .tag(0)
            NavigationMapView(
                navigationService: appViewModel.navigationService,
                destinationSearchService: appViewModel.destinationSearchService,
                locationService: appViewModel.locationService,
                safetyRepository: appViewModel.safetyEventRepository,
                onStartRide: {
                    rideStartRequest += 1
                    selectedTab = 0
                },
                onOpenRide: { selectedTab = 0 }
            )
                .tabItem { Label("Map", systemImage: "map.fill") }
                .tag(1)
            SafetyView(repository: appViewModel.safetyEventRepository, analysisService: appViewModel.incidentAnalysisService)
                .tabItem { Label("Safety", systemImage: "chart.bar.fill") }
                .tag(2)
            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape.fill") }
                .tag(3)
        }
        .tint(AppTheme.accent)
        .toolbarBackground(.visible, for: .tabBar)
        .toolbarBackground(AppTheme.surface, for: .tabBar)
    }
}
