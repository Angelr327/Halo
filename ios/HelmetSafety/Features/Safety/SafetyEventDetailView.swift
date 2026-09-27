import AVKit
import MapKit
import SwiftUI
import WebKit

struct SafetyEventDetailView: View {
    @StateObject private var viewModel: SafetyEventDetailViewModel

    init(event: SafetyEvent, repository: SafetyEventStoring, analysisService: IncidentAnalysisProviding) {
        _viewModel = StateObject(wrappedValue: SafetyEventDetailViewModel(event: event, repository: repository, analysisService: analysisService))
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                incidentHeader
                videoCard
                metadataCard
                if let coordinate {
                    Map(initialPosition: .region(MKCoordinateRegion(center: coordinate, latitudinalMeters: 700, longitudinalMeters: 700))) {
                        Marker(viewModel.event.eventType.displayName, coordinate: coordinate)
                    }
                    .frame(height: 210).clipShape(RoundedRectangle(cornerRadius: 18))
                }
                analysisCard
            }.padding()
        }
        .background(AppTheme.background.ignoresSafeArea())
        .navigationTitle("Incident Detail")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var incidentHeader: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(viewModel.event.eventType.displayName).font(.title.bold())
            Text(viewModel.event.timestamp.formatted(date: .abbreviated, time: .shortened)).foregroundStyle(AppTheme.secondaryText)
            Text(viewModel.event.severity.rawValue.uppercased()).font(.caption.weight(.black)).tracking(1)
                .foregroundStyle(viewModel.event.severity == .high || viewModel.event.severity == .critical ? AppTheme.danger : AppTheme.caution)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private var videoCard: some View {
        DashboardCard(title: "Camera evidence") {
            if let url = viewModel.event.videoURL, viewModel.event.hasPlayableVideo {
                VideoPlayer(player: AVPlayer(url: url)).frame(height: 210).clipShape(RoundedRectangle(cornerRadius: 12))
                Text("Dash-cam clip: approximately 15 seconds before and 15 seconds after the incident. Clips finish processing shortly after an event.")
                    .font(.caption).foregroundStyle(AppTheme.secondaryText)
            } else {
                VStack(spacing: 10) {
                    Image(systemName: "video.slash.fill").font(.system(size: 34)).foregroundStyle(AppTheme.secondaryText)
                    Text("Incident video unavailable from helmet").font(.headline).multilineTextAlignment(.center)
                    if let path = viewModel.event.videoPath {
                        Text("Incident recording ID: \(path). The clip may still be processing on the Pi.").font(.caption).multilineTextAlignment(.center).foregroundStyle(AppTheme.secondaryText)
                    } else {
                        Text("No retrievable clip URL was provided for this incident.").font(.caption).foregroundStyle(AppTheme.secondaryText)
                    }
                }.frame(maxWidth: .infinity, minHeight: 130)
            }
        }
    }

    private var metadataCard: some View {
        DashboardCard(title: "Incident information") {
            detailRow("Detected", viewModel.event.detectedObject ?? "Unavailable")
            detailRow("Camera", viewModel.event.cameraId ?? "Unavailable")
            detailRow("Side", viewModel.event.side?.rawValue.capitalized ?? "Unavailable")
            detailRow("Distance", viewModel.event.estimatedDistanceMeters.map { String(format: "%.1f meters", $0) } ?? "Unavailable")
            detailRow("Confidence", viewModel.event.confidence.map { String(format: "%.0f%%", $0 * 100) } ?? "Unavailable")
            detailRow("Speed", viewModel.event.speed.map { String(format: "%.1f mph", $0) } ?? "Unavailable")
            if let latitude = viewModel.event.latitude, let longitude = viewModel.event.longitude {
                detailRow("Location", String(format: "%.5f, %.5f", latitude, longitude))
            }
            if let notes = viewModel.event.notes { Divider(); Text(notes).font(.subheadline).foregroundStyle(AppTheme.secondaryText) }
        }
    }

    private var analysisCard: some View {
        DashboardCard(title: "CV incident analysis") {
            switch viewModel.analysisState {
            case .notAnalyzed:
                Text("Summarize the object, side, risk tier, distance, confidence, TTC, and ride context captured by the helmet.").font(.subheadline).foregroundStyle(AppTheme.secondaryText)
                analyzeButton("Analyze Incident")
            case .analyzing:
                HStack { ProgressView(); Text("Analyzing incident…") }
            case .available:
                Text(viewModel.event.aiSummary ?? "Analysis unavailable.").font(.subheadline)
                analyzeButton("Analyze Again")
            case .failed:
                Label(viewModel.analysisError ?? "Analysis failed.", systemImage: "exclamationmark.triangle.fill").font(.subheadline).foregroundStyle(AppTheme.danger)
                analyzeButton("Try Again")
            }
        }
    }

    private func analyzeButton(_ title: String) -> some View {
        Button(title) { Task { await viewModel.analyze() } }.buttonStyle(.borderedProminent).tint(AppTheme.accent)
    }

    private var coordinate: CLLocationCoordinate2D? {
        guard let latitude = viewModel.event.latitude, let longitude = viewModel.event.longitude else { return nil }
        return CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top) { Text(label).foregroundStyle(AppTheme.secondaryText); Spacer(); Text(value).multilineTextAlignment(.trailing).fontWeight(.semibold) }.font(.subheadline)
    }
}
