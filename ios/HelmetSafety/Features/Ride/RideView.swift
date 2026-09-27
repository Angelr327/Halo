import MapKit
import SwiftUI

struct RideView: View {
    @StateObject private var viewModel: RideViewModel
    @State private var cameraPosition: MapCameraPosition = .automatic
    @State private var showingEndConfirmation = false
    let startRequest: Int

    init(service: HelmetDataProviding, locationService: LocationProviding, navigationService: NavigationProviding, destinationSearchService: DestinationSearchService, safetyRepository: SafetyEventStoring, startRequest: Int = 0) {
        _viewModel = StateObject(wrappedValue: RideViewModel(service: service, locationService: locationService, navigationService: navigationService, destinationSearchService: destinationSearchService, safetyRepository: safetyRepository))
        self.startRequest = startRequest
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
            Text("Your ride statistics will be saved locally.")
        }
        .onChange(of: viewModel.screenMode) { _, mode in
            if mode == .activeRide { recenterMap() }
            else { cameraPosition = .automatic }
        }
        .onChange(of: startRequest) { _, _ in
            viewModel.startRide()
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
                statusPill(viewModel.helmetState.isConnected ? "Helmet online" : viewModel.helmetState.connectionStatus, icon: viewModel.helmetState.isConnected ? "checkmark.circle.fill" : "xmark.circle.fill", color: viewModel.helmetState.isConnected ? AppTheme.safe : AppTheme.danger)
                Spacer()
                HaloLogoView()
            }
        }
    }

    private var destinationCard: some View {
        VStack(alignment: .leading, spacing: AppSpacing.medium) {
            Text("WHERE TO?").font(AppTypography.cardTitle).tracking(0.8).foregroundStyle(AppTheme.secondaryText)
            DestinationSearchField(service: viewModel.destinationSearchService, placeholder: "Enter a destination") {
                await viewModel.selectDestination($0)
            } onRecentSelect: {
                await viewModel.selectRecentDestination($0)
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
                if viewModel.helmetState.leftHazard || viewModel.helmetState.rightHazard {
                    liveHazardCard
                }
                Spacer()
                HStack(alignment: .bottom, spacing: AppSpacing.medium) {
                    liveMetricsCard
                    VStack(spacing: AppSpacing.small) {
                        mapControlButton("location.fill", action: recenterMap)
                        mapControlButton(viewModel.navigation.isVoiceMuted ? "speaker.slash.fill" : "speaker.wave.2.fill") {
                            viewModel.setVoiceMuted(!viewModel.navigation.isVoiceMuted)
                        }
                    }
                }
                bottomControls
            }
            .padding(.horizontal, AppSpacing.large)
            .padding(.vertical, AppSpacing.small)

            if viewModel.helmetState.frontBrakeActive {
                brakeOverlay
                    .transition(.scale.combined(with: .opacity))
                    .allowsHitTesting(false)
            }
        }
        .animation(.easeInOut(duration: 0.18), value: viewModel.helmetState.frontBrakeActive)
    }

    private var brakeOverlay: some View {
        VStack(spacing: 8) {
            Image(systemName: "exclamationmark.octagon.fill").font(.system(size: 58, weight: .black))
            Text("BRAKE!").font(.system(size: 58, weight: .black, design: .rounded))
            if let object = viewModel.helmetState.frontDetectedObject {
                Text("\(object) ahead".uppercased()).font(.headline.weight(.black))
            }
            HStack(spacing: 14) {
                if let distance = viewModel.helmetState.frontDistanceMeters {
                    Text(String(format: "%.1f m", distance))
                }
                if let ttc = viewModel.helmetState.frontTTCSeconds {
                    Text(String(format: "%.1f s TTC", ttc))
                }
            }.font(.title3.weight(.bold))
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 34).padding(.vertical, 26)
        .background(AppTheme.danger.opacity(0.96), in: RoundedRectangle(cornerRadius: 28, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 28).stroke(.white.opacity(0.8), lineWidth: 3))
        .shadow(color: AppTheme.danger.opacity(0.55), radius: 24, y: 8)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Brake. \(viewModel.helmetState.frontReason ?? "Obstacle ahead")")
    }

    private var activeStatusBar: some View {
        HStack {
            statusPill(viewModel.helmetState.isConnected ? "Helmet online" : viewModel.helmetState.connectionStatus, icon: viewModel.helmetState.isConnected ? "checkmark.circle.fill" : "xmark.circle.fill", color: viewModel.helmetState.isConnected ? AppTheme.safe : AppTheme.danger)
            Spacer()
            HaloLogoView()
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

    private var liveHazardCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(hazardTitle, systemImage: "exclamationmark.triangle.fill")
                    .font(.headline.weight(.bold))
                Spacer()
                Text(viewModel.helmetState.severity.rawValue.uppercased())
                    .font(.caption2.weight(.black)).tracking(0.8)
            }
            HStack(spacing: 10) {
                liveSideIndicator(.left, active: viewModel.helmetState.leftHazard)
                liveSideIndicator(.right, active: viewModel.helmetState.rightHazard)
            }
            HStack {
                if let object = viewModel.helmetState.detectedObject {
                    Label(object, systemImage: "car.side.fill")
                }
                Spacer()
                if let distance = viewModel.helmetState.estimatedDistance {
                    Label(String(format: "%.1f m", distance), systemImage: "ruler")
                }
                if let ttc = viewModel.helmetState.estimatedTTC {
                    Label(String(format: "%.1f s", ttc), systemImage: "timer")
                }
            }
            .font(.caption.weight(.semibold))
        }
        .foregroundStyle(.white)
        .padding(14)
        .background(hazardColor.opacity(0.94), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .shadow(color: hazardColor.opacity(0.3), radius: 12, y: 4)
        .accessibilityElement(children: .combine)
    }

    private func liveSideIndicator(_ side: SafetyEventSide, active: Bool) -> some View {
        HStack(spacing: 6) {
            Image(systemName: side == .left ? "arrow.left" : "arrow.right")
            Text(side.rawValue.uppercased())
        }
        .font(.subheadline.weight(.black))
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(.white.opacity(active ? 0.22 : 0.06), in: RoundedRectangle(cornerRadius: 11))
        .opacity(active ? 1 : 0.45)
    }

    private var hazardTitle: String {
        switch (viewModel.helmetState.leftHazard, viewModel.helmetState.rightHazard) {
        case (true, true): "Vehicles approaching both sides"
        case (true, false): "Vehicle approaching left"
        case (false, true): "Vehicle approaching right"
        default: "Hazard detected"
        }
    }

    private var hazardColor: Color {
        viewModel.helmetState.safetyStatus == .danger ? AppTheme.danger : AppTheme.caution
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
                        summaryRow("Helmet at finish", viewModel.helmetState.isConnected ? "Connected" : "Disconnected")
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
        // A user-location camera remains attached to the blue dot as Core Location updates.
        // A fixed MapCamera only centers once and then leaves the rider behind.
        cameraPosition = .userLocation(followsHeading: true, fallback: .automatic)
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
