import MapKit
import SwiftUI

struct RideView: View {
    @StateObject private var viewModel: RideViewModel
    @State private var cameraPosition: MapCameraPosition = .automatic
    @State private var showingEndConfirmation = false
    @State private var isVoiceMuted = false

    init(service: HelmetDataProviding = MockHelmetService(), locationService: LocationProviding = LocationService(), navigationService: NavigationProviding = NavigationService(), guardianService: GuardianProviding? = nil, safetyRepository: SafetyEventStoring = SafetyEventRepository()) {
        _viewModel = StateObject(wrappedValue: RideViewModel(service: service, locationService: locationService, navigationService: navigationService, guardianService: guardianService, safetyRepository: safetyRepository))
    }

    var body: some View {
        Group {
            switch viewModel.screenMode {
            case .preRide: preRideView
            case .activeRide: activeRideView
            case .summary: summaryView
            }
        }
        .toolbar(viewModel.screenMode == .activeRide ? .hidden : .visible, for: .tabBar)
        .alert("End and save this ride?", isPresented: $showingEndConfirmation) {
            Button("Cancel", role: .cancel) {}
            Button("End & Save Ride", role: .destructive) { viewModel.endRide() }
        } message: {
            Text("Your ride statistics and Guardian session will be saved locally.")
        }
        .onChange(of: viewModel.screenMode) { _, mode in
            if mode == .activeRide { recenterMap() }
            else { cameraPosition = .automatic }
        }
    }

    private var preRideView: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: AppSpacing.xLarge) {
                    readinessHeader
                    destinationCard
                    routePreview
                    startButton
                }
                .padding(.horizontal, AppSpacing.screen)
                .padding(.bottom, AppSpacing.section)
            }
            .background(AppTheme.background.ignoresSafeArea())
            .navigationTitle("Ride")
        }
    }

    private var readinessHeader: some View {
        VStack(alignment: .leading, spacing: AppSpacing.large) {
            AppSectionHeader(title: "Ready to ride?", detail: viewModel.location.status.rawValue)
            HStack(spacing: AppSpacing.medium) {
                statusPill(viewModel.helmetState.isConnected ? "Helmet connected" : "Helmet offline", icon: viewModel.helmetState.isConnected ? "checkmark.circle.fill" : "xmark.circle.fill", color: viewModel.helmetState.isConnected ? AppTheme.safe : AppTheme.danger)
                statusPill("\(viewModel.helmetState.batteryPercentage)%", icon: "battery.75percent", color: viewModel.helmetState.batteryPercentage < 20 ? AppTheme.danger : AppTheme.primaryText)
            }
        }
    }

    private var destinationCard: some View {
        VStack(alignment: .leading, spacing: AppSpacing.medium) {
            Text("WHERE TO?").font(AppTypography.cardTitle).tracking(0.8).foregroundStyle(AppTheme.secondaryText)
            HStack(spacing: AppSpacing.medium) {
                Image(systemName: "magnifyingglass").foregroundStyle(AppTheme.secondaryText)
                TextField("Enter a destination", text: $viewModel.destinationQuery)
                    .submitLabel(.search)
                    .onSubmit { Task { await viewModel.calculateRoute() } }
                Button { Task { await viewModel.calculateRoute() } } label: {
                    if viewModel.isRouting { ProgressView() }
                    else { Image(systemName: "arrow.right.circle.fill").font(.title2) }
                }
                .disabled(viewModel.destinationQuery.trimmingCharacters(in: .whitespaces).isEmpty || viewModel.isRouting)
            }
            if let error = viewModel.routeError { Text(error).font(.caption).foregroundStyle(AppTheme.danger) }
        }
        .appCard()
    }

    @ViewBuilder private var routePreview: some View {
        if viewModel.navigation.route != nil {
            VStack(alignment: .leading, spacing: AppSpacing.medium) {
                AppSectionHeader(title: viewModel.navigation.destinationName ?? "Route preview", detail: "Apple Maps")
                rideMap(interactive: false).frame(height: 230).clipShape(RoundedRectangle(cornerRadius: 16))
                HStack {
                    routeStat(viewModel.routeDistanceText, "Distance")
                    Divider().frame(height: 34)
                    routeStat(viewModel.routeDurationText, "Est. duration")
                    Divider().frame(height: 34)
                    routeStat("\(viewModel.safetyEvents.filter(\.hasLocation).count)", "Safety pins")
                }
            }
        } else {
            VStack(spacing: AppSpacing.medium) {
                Image(systemName: "map.fill").font(.system(size: 38)).foregroundStyle(AppTheme.accent)
                Text("Plan your route").font(.headline)
                Text("Search for a destination to preview distance, duration, and the route before starting.")
                    .font(.subheadline).multilineTextAlignment(.center).foregroundStyle(AppTheme.secondaryText)
            }
            .frame(maxWidth: .infinity).appCard(padding: AppSpacing.xLarge)
        }
    }

    private var startButton: some View {
        Button(action: viewModel.startRide) {
            Label(viewModel.rideSession.rideStatus == .ended ? "START NEW RIDE" : "START RIDE", systemImage: "play.fill")
                .font(.headline.weight(.bold)).tracking(0.5)
                .frame(maxWidth: .infinity).padding(.vertical, 18)
                .background(AppTheme.accent, in: Capsule()).foregroundStyle(.white)
        }
    }

    private var activeRideView: some View {
        ZStack {
            rideMap(interactive: true).ignoresSafeArea()

            VStack(spacing: AppSpacing.medium) {
                activeStatusBar
                navigationInstructionCard
                Spacer()
                HStack(alignment: .bottom, spacing: AppSpacing.medium) {
                    liveMetricsCard
                    VStack(spacing: AppSpacing.small) {
                        mapControlButton("location.fill", action: recenterMap)
                        mapControlButton(isVoiceMuted ? "speaker.slash.fill" : "speaker.wave.2.fill") { isVoiceMuted.toggle() }
                    }
                }
                bottomControls
            }
            .padding(.horizontal, AppSpacing.large)
            .padding(.vertical, AppSpacing.small)
        }
    }

    private var activeStatusBar: some View {
        HStack {
            statusPill(viewModel.helmetState.isConnected ? "Helmet" : "Offline", icon: viewModel.helmetState.isConnected ? "checkmark.circle.fill" : "xmark.circle.fill", color: viewModel.helmetState.isConnected ? AppTheme.safe : AppTheme.danger)
            Spacer()
            statusPill("\(viewModel.helmetState.batteryPercentage)%", icon: "battery.75percent", color: viewModel.helmetState.batteryPercentage < 20 ? AppTheme.danger : AppTheme.primaryText)
        }
    }

    private var navigationInstructionCard: some View {
        HStack(spacing: AppSpacing.large) {
            Image(systemName: maneuverIcon).font(.system(size: 38, weight: .bold)).foregroundStyle(AppTheme.accent).frame(width: 46)
            VStack(alignment: .leading, spacing: 3) {
                Text(viewModel.navigation.maneuver.map { "\($0.direction.rawValue.capitalized) · \(viewModel.maneuverDistanceFeet) ft" } ?? "Continue riding")
                    .font(.title2.weight(.bold))
                Text(viewModel.navigation.maneuver?.streetName ?? viewModel.navigation.destinationName ?? "No active route")
                    .font(.subheadline.weight(.medium)).foregroundStyle(AppTheme.secondaryText).lineLimit(1)
            }
            Spacer()
        }
        .appCard(padding: 14)
    }

    private var liveMetricsCard: some View {
        VStack(alignment: .leading, spacing: AppSpacing.medium) {
            compactMetric("TIME", viewModel.elapsedTimeText)
            Divider()
            compactMetric("DISTANCE", String(format: "%.2f mi", viewModel.rideSession.distanceTravelled))
            Divider()
            compactMetric("SPEED", String(format: "%.1f mph", viewModel.location.speedMPH))
            Divider()
            compactMetric("SAFETY", viewModel.safetyLabel, color: safetyColor)
            Text("Safety Events: \(viewModel.currentRideEvents.count)")
                .font(.caption.weight(.semibold)).foregroundStyle(AppTheme.secondaryText)
        }
        .frame(width: 160).appCard(padding: 14)
    }

    private var bottomControls: some View {
        Button { showingEndConfirmation = true } label: {
            Label("End Ride", systemImage: "stop.fill")
                .font(.headline.weight(.bold)).frame(maxWidth: .infinity).padding(.vertical, 15)
                .background(AppTheme.primaryText, in: Capsule()).foregroundStyle(Color(uiColor: .systemBackground))
        }
    }

    private var summaryView: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: AppSpacing.xLarge) {
                    VStack(spacing: AppSpacing.small) {
                        Image(systemName: "checkmark.circle.fill").font(.system(size: 48)).foregroundStyle(AppTheme.safe)
                        Text("Ride complete").font(.largeTitle.weight(.bold))
                        Text("Saved locally").font(.subheadline).foregroundStyle(AppTheme.secondaryText)
                    }
                    if viewModel.navigation.route != nil {
                        rideMap(interactive: false).frame(height: 250).clipShape(RoundedRectangle(cornerRadius: 18))
                    }
                    summaryMetrics
                    DashboardCard(title: "Safety summary") {
                        summaryRow("Overall status", viewModel.safetyLabel)
                        summaryRow("Safety events", "\(viewModel.currentRideEvents.count)")
                        summaryRow("Close calls", "\(viewModel.closeCallCount)")
                        summaryRow("High-risk events", "\(viewModel.highRiskEventCount)")
                        summaryRow("Helmet at finish", viewModel.helmetState.isConnected ? "Connected · \(viewModel.helmetState.batteryPercentage)%" : "Disconnected")
                    }
                    Button(action: viewModel.finishSummary) {
                        Text("DONE").font(.headline.weight(.bold)).frame(maxWidth: .infinity).padding(.vertical, 17)
                            .background(AppTheme.accent, in: Capsule()).foregroundStyle(.white)
                    }
                }
                .padding(AppSpacing.screen)
            }
            .background(AppTheme.background.ignoresSafeArea())
            .navigationTitle("Ride Summary")
        }
    }

    private var summaryMetrics: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: AppSpacing.medium) {
            MetricTile(icon: "stopwatch.fill", label: "Ride time", value: viewModel.elapsedTimeText)
            MetricTile(icon: "point.topleft.down.to.point.bottomright.curvepath", label: "Distance", value: String(format: "%.2f mi", viewModel.rideSession.distanceTravelled))
            MetricTile(icon: "speedometer", label: "Average speed", value: String(format: "%.1f mph", viewModel.averageSpeedMPH))
            MetricTile(icon: "gauge.with.dots.needle.67percent", label: "Maximum speed", value: String(format: "%.1f mph", viewModel.maximumSpeedMPH))
        }
    }

    private func rideMap(interactive: Bool) -> some View {
        Map(position: $cameraPosition, interactionModes: interactive ? .all : []) {
            UserAnnotation()
            if let route = viewModel.navigation.route { MapPolyline(route.polyline).stroke(AppTheme.accent, lineWidth: 7) }
            if let destination = viewModel.navigation.destinationCoordinate {
                Marker(viewModel.navigation.destinationName ?? "Destination", coordinate: destination).tint(AppTheme.accent)
            }
            ForEach(viewModel.safetyEvents.filter(\.hasLocation)) { event in
                if let latitude = event.latitude, let longitude = event.longitude {
                    Annotation(event.eventType.displayName, coordinate: CLLocationCoordinate2D(latitude: latitude, longitude: longitude)) {
                        Image(systemName: "exclamationmark.circle.fill").foregroundStyle(event.severity == .high || event.severity == .critical ? AppTheme.danger : AppTheme.caution)
                    }
                }
            }
        }
        .mapStyle(.standard(elevation: .realistic))
    }

    private func recenterMap() {
        cameraPosition = .userLocation(followsHeading: viewModel.location.headingDegrees != nil, fallback: .automatic)
    }

    private func statusPill(_ title: String, icon: String, color: Color) -> some View {
        Label(title, systemImage: icon).font(.caption.weight(.semibold)).foregroundStyle(color)
            .padding(.horizontal, 11).padding(.vertical, 8).background(.regularMaterial, in: Capsule())
            .shadow(color: .black.opacity(0.06), radius: 6, y: 2)
    }

    private func compactMetric(_ label: String, _ value: String, color: Color = AppTheme.primaryText) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.caption2.weight(.bold)).tracking(0.8).foregroundStyle(AppTheme.secondaryText)
            Text(value).font(.headline.weight(.bold)).foregroundStyle(color).lineLimit(1).minimumScaleFactor(0.75)
        }
    }

    private func routeStat(_ value: String, _ label: String) -> some View {
        VStack(spacing: 3) { Text(value).font(.headline); Text(label).font(.caption2).foregroundStyle(AppTheme.secondaryText) }.frame(maxWidth: .infinity)
    }

    private func mapControlButton(_ icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: icon).font(.headline).frame(width: 46, height: 46).background(.regularMaterial, in: Circle()).shadow(color: .black.opacity(0.1), radius: 8, y: 3) }
        .foregroundStyle(AppTheme.primaryText)
    }

    private func summaryRow(_ label: String, _ value: String) -> some View {
        HStack { Text(label).foregroundStyle(AppTheme.secondaryText); Spacer(); Text(value).fontWeight(.semibold) }.font(.subheadline)
    }

    private var safetyColor: Color {
        switch viewModel.helmetState.safetyStatus {
        case .safe: AppTheme.safe
        case .caution: AppTheme.caution
        case .danger: AppTheme.danger
        }
    }

    private var maneuverIcon: String {
        switch viewModel.navigation.maneuver?.direction {
        case .left: "arrow.turn.up.left"
        case .right: "arrow.turn.up.right"
        case .straight: "arrow.up"
        case .arrive: "flag.checkered"
        case .unknown, .none: "location.north.fill"
        }
    }
}
