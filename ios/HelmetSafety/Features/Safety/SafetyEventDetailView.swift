import MapKit
import SwiftUI

struct SafetyEventDetailView: View {
    let event: SafetyEvent

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                metadataCard
                locationCard
                if let coordinate = coordinate {
                    Map(initialPosition: .region(MKCoordinateRegion(center: coordinate, latitudinalMeters: 700, longitudinalMeters: 700))) {
                        Marker(event.eventType.displayName, coordinate: coordinate)
                    }
                    .frame(height: 220)
                    .clipShape(RoundedRectangle(cornerRadius: 18))
                    .accessibilityLabel("Safety event map location")
                }
                videoPlaceholder
                aiSummaryPlaceholder
            }
            .padding()
        }
        .background(AppTheme.background.ignoresSafeArea())
        .navigationTitle("Event Details")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var metadataCard: some View {
        DashboardCard(title: "Event metadata") {
            detailRow("Event", event.eventType.displayName)
            detailRow("Timestamp", event.timestamp.formatted(date: .long, time: .standard))
            detailRow("Severity", event.severity.rawValue.capitalized)
            detailRow("Side", event.side.rawValue.capitalized)
            detailRow("Object", event.detectedObject ?? "Unknown")
            detailRow("Distance", event.estimatedDistanceMeters.map { String(format: "%.1f meters", $0) } ?? "Unavailable")
            detailRow("Speed", event.speed.map { String(format: "%.1f mph", $0) } ?? "Unavailable")
            if let notes = event.notes { Divider(); Text(notes).font(.subheadline).foregroundStyle(AppTheme.secondaryText) }
        }
    }

    private var locationCard: some View {
        DashboardCard(title: "Location") {
            if let latitude = event.latitude, let longitude = event.longitude {
                detailRow("Latitude", String(format: "%.6f", latitude))
                detailRow("Longitude", String(format: "%.6f", longitude))
            } else {
                Label("Location unavailable", systemImage: "location.slash").foregroundStyle(AppTheme.secondaryText)
            }
        }
    }

    private var videoPlaceholder: some View {
        DashboardCard(title: "Incident video") {
            VStack(spacing: 12) {
                Image(systemName: "video.fill").font(.system(size: 38)).foregroundStyle(AppTheme.accent)
                Text(event.videoPath == nil ? "No video attached" : "Video playback coming soon")
                    .font(.headline)
                if let path = event.videoPath { Text(path).font(.caption).foregroundStyle(AppTheme.secondaryText) }
            }.frame(maxWidth: .infinity, minHeight: 130)
        }
    }

    private var aiSummaryPlaceholder: some View {
        DashboardCard(title: "AI summary") {
            Label("AI incident analysis will appear here.", systemImage: "sparkles")
                .foregroundStyle(AppTheme.secondaryText)
        }
    }

    private var coordinate: CLLocationCoordinate2D? {
        guard let latitude = event.latitude, let longitude = event.longitude else { return nil }
        return CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(label).foregroundStyle(AppTheme.secondaryText)
            Spacer()
            Text(value).multilineTextAlignment(.trailing).fontWeight(.semibold)
        }.font(.subheadline)
    }
}
