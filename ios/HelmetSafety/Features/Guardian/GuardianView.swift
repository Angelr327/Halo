import SwiftUI

struct GuardianView: View {
    @StateObject private var viewModel: GuardianViewModel

    init(service: GuardianProviding) {
        _viewModel = StateObject(wrappedValue: GuardianViewModel(service: service))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: AppSpacing.xLarge) {
                    HStack(spacing: AppSpacing.medium) {
                        Image(systemName: "shield.checkered").font(.title).foregroundStyle(AppTheme.accent)
                        VStack(alignment: .leading, spacing: 3) {
                            Text("Guardian").font(.largeTitle.weight(.bold))
                            Text("Private, local ride monitoring").font(.subheadline).foregroundStyle(AppTheme.secondaryText)
                        }
                        Spacer()
                    }
                    DashboardCard(title: "Current ride status") {
                        HStack {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(viewModel.session.rideStatus.displayName).font(.title2.weight(.bold))
                                Label(viewModel.session.possibleCrash ? "Emergency check-in required" : "Guardian monitoring local", systemImage: viewModel.session.possibleCrash ? "exclamationmark.triangle.fill" : "shield.checkered")
                                    .foregroundStyle(viewModel.session.possibleCrash ? AppTheme.danger : AppTheme.safe)
                            }
                            Spacer()
                            Circle().fill(statusColor).frame(width: 12, height: 12).shadow(color: statusColor, radius: 7)
                        }
                    }
                    DashboardCard(title: "Rider location") {
                        Label(locationText, systemImage: "mappin.and.ellipse").font(.headline)
                        Text(lastUpdateText).font(.caption).foregroundStyle(AppTheme.secondaryText)
                    }
                    HStack(spacing: 10) {
                        MetricTile(icon: "speedometer", label: "Current speed", value: String(format: "%.1f mph", viewModel.session.currentSpeed))
                        MetricTile(icon: "point.topleft.down.to.point.bottomright.curvepath", label: "Distance", value: String(format: "%.2f mi", viewModel.session.distanceTravelled))
                    }
                    HStack(spacing: 10) {
                        MetricTile(icon: "battery.75percent", label: "Helmet battery", value: "\(viewModel.session.helmetBattery)%", tint: viewModel.session.helmetBattery < 20 ? AppTheme.danger : AppTheme.safe)
                        MetricTile(icon: viewModel.session.helmetConnected ? "checkmark.circle.fill" : "xmark.circle.fill", label: "Helmet", value: viewModel.session.helmetConnected ? "Connected" : "Disconnected", tint: viewModel.session.helmetConnected ? AppTheme.safe : AppTheme.danger)
                    }
                    HStack(spacing: 10) {
                        MetricTile(icon: "exclamationmark.triangle.fill", label: "Total hazards", value: "\(viewModel.session.totalHazards)", tint: AppTheme.caution)
                        MetricTile(icon: "exclamationmark.octagon.fill", label: "High-risk events", value: "\(viewModel.session.highRiskEvents)", tint: AppTheme.danger)
                    }
                    DashboardCard(title: "Emergency state") {
                        Label(emergencyText, systemImage: emergencyIcon).foregroundStyle(viewModel.session.possibleCrash ? AppTheme.danger : AppTheme.safe)
                    }
                    DashboardCard(title: "Emergency contacts") {
                        ForEach(viewModel.emergencyContacts) { contact in
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(contact.name).fontWeight(.semibold)
                                    Text(contact.relationship).font(.caption).foregroundStyle(AppTheme.secondaryText)
                                }
                                Spacer()
                                Text(contact.phoneNumber).font(.caption)
                            }
                        }
                    }
                }.padding(AppSpacing.screen)
            }
            .background(AppTheme.background.ignoresSafeArea())
            .toolbar(.hidden, for: .navigationBar)
        }
    }

    private var locationText: String {
        guard let location = viewModel.session.currentLocation else { return "Location unavailable" }
        return String(format: "%.5f, %.5f", location.latitude, location.longitude)
    }

    private var lastUpdateText: String {
        guard let date = viewModel.session.lastUpdate else { return "No location update yet" }
        return "Last update \(date.formatted(.relative(presentation: .named)))"
    }

    private var statusColor: Color { viewModel.session.rideStatus == .possibleEmergency ? AppTheme.danger : viewModel.session.rideStatus == .active ? AppTheme.safe : AppTheme.caution }
    private var emergencyText: String {
        switch viewModel.alertState {
        case .idle: "No emergency detected"
        case .checkingIn: "Waiting for rider check-in"
        case .riderConfirmedSafe: "Rider confirmed safe"
        case .helpRequested: "Guardian alert simulated — no emergency call placed"
        }
    }
    private var emergencyIcon: String { viewModel.session.possibleCrash ? "exclamationmark.triangle.fill" : "checkmark.shield.fill" }
}
