import SwiftUI

struct SafetyEventCard: View {
    let event: SafetyEvent

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                Label(event.eventType.displayName, systemImage: icon)
                    .font(.headline)
                Spacer()
                Text(event.severity.rawValue.uppercased())
                    .font(.caption2.weight(.black)).tracking(0.8)
                    .foregroundStyle(severityColor)
            }
            HStack {
                Label(event.side?.rawValue.capitalized ?? "Unknown side", systemImage: sideIcon)
                Spacer()
                if let distance = event.estimatedDistanceMeters {
                    Label(String(format: "%.1f m", distance), systemImage: "ruler")
                }
            }
            .font(.subheadline).foregroundStyle(AppTheme.secondaryText)

            if event.hasLocation {
                Label(locationText, systemImage: "mappin.and.ellipse")
                    .font(.caption).foregroundStyle(AppTheme.secondaryText)
            }
            Text(event.timestamp.formatted(date: .abbreviated, time: .shortened))
                .font(.caption2).foregroundStyle(AppTheme.secondaryText)
        }
        .padding(.vertical, 9)
    }

    private var icon: String {
        switch event.eventType {
        case .vehicleApproach: "car.side.fill"
        case .closePass: "exclamationmark.triangle.fill"
        case .hardBrake: "brakesignal"
        case .frontObstacle: "car.front.waves.up"
        case .emergencyBrakeWarning: "exclamationmark.octagon.fill"
        case .possibleCollision, .collision: "exclamationmark.octagon.fill"
        case .manualRecording: "record.circle.fill"
        }
    }

    private var sideIcon: String {
        switch event.side {
        case .left?: "arrow.left"
        case .right?: "arrow.right"
        case .rear?: "arrow.down"
        case .front?: "arrow.up"
        case .unknown?, nil: "questionmark"
        }
    }

    private var locationText: String {
        String(format: "%.5f, %.5f", event.latitude ?? 0, event.longitude ?? 0)
    }

    private var severityColor: Color {
        switch event.severity {
        case .low: AppTheme.accent
        case .medium: AppTheme.caution
        case .high, .critical: AppTheme.danger
        }
    }
}
