import MapKit
import SwiftUI

struct NavigationMapView: View {
    @StateObject private var viewModel: MapViewModel
    @State private var cameraPosition: MapCameraPosition = .automatic
    @State private var selectedMapFeature: MapFeature?
    let onStartRide: () -> Void
    let onOpenRide: () -> Void

    init(navigationService: NavigationProviding, destinationSearchService: DestinationSearchService, locationService: LocationProviding, safetyRepository: SafetyEventStoring, onStartRide: @escaping () -> Void = {}, onOpenRide: @escaping () -> Void = {}) {
        _viewModel = StateObject(wrappedValue: MapViewModel(navigationService: navigationService, destinationSearchService: destinationSearchService, locationService: locationService, safetyRepository: safetyRepository))
        self.onStartRide = onStartRide
        self.onOpenRide = onOpenRide
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Map(position: $cameraPosition, selection: $selectedMapFeature) {
                        UserAnnotation()
                        if let route = viewModel.navigationState.route {
                            MapPolyline(route.polyline).stroke(AppTheme.accent, lineWidth: 7)
                        }
                        if let coordinate = viewModel.navigationState.destinationCoordinate {
                            Marker(viewModel.navigationState.destinationName ?? "Destination", coordinate: coordinate).tint(AppTheme.accent)
                        }
                        ForEach(viewModel.safetyEvents) { event in
                            if let latitude = event.latitude, let longitude = event.longitude {
                                Annotation(event.eventType.displayName, coordinate: CLLocationCoordinate2D(latitude: latitude, longitude: longitude)) {
                                    Image(systemName: "exclamationmark.circle.fill")
                                        .font(.title2).foregroundStyle(event.severity == .critical || event.severity == .high ? AppTheme.danger : AppTheme.caution)
                                        .padding(5).background(.thinMaterial, in: Circle())
                                }
                            }
                        }
                }
                .mapControls { MapCompass(); MapUserLocationButton(); MapScaleView() }

                VStack(spacing: AppSpacing.medium) {
                    DestinationSearchField(service: viewModel.destinationSearchService, placeholder: "Enter destination") {
                        await viewModel.selectDestination($0)
                    } onRecentSelect: {
                        await viewModel.selectRecentDestination($0)
                    }
                    .padding(14).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
                    .shadow(color: .black.opacity(0.12), radius: 14, y: 6)

                    if let error = viewModel.errorMessage {
                        Text(error).font(.caption.weight(.medium)).foregroundStyle(AppTheme.danger).padding(10).frame(maxWidth: .infinity, alignment: .leading).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                    }

                    Spacer()

                    if viewModel.navigationState.route != nil {
                        VStack(alignment: .leading, spacing: 12) {
                            HStack {
                                Image(systemName: "mappin.circle.fill").foregroundStyle(AppTheme.accent)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("ROUTE READY").font(.caption2.weight(.bold)).tracking(0.8).foregroundStyle(AppTheme.secondaryText)
                                    Text(viewModel.navigationState.destinationName ?? "Selected destination").font(.headline).lineLimit(1)
                                }
                            }
                            HStack(spacing: 24) {
                                routeStat(viewModel.routeDistanceText, "Distance")
                                Divider().frame(height: 34)
                                routeStat(viewModel.routeTimeText, "Est. time")
                                Divider().frame(height: 34)
                                routeStat("\(viewModel.safetyEvents.count)", "Safety pins")
                            }
                        }
                        .padding(14).frame(maxWidth: .infinity)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
                        Button(action: viewModel.navigationState.isNavigating ? onOpenRide : onStartRide) {
                            Text(viewModel.navigationState.isNavigating ? "RETURN TO RIDE" : "START RIDE")
                                .font(.headline.weight(.bold)).frame(maxWidth: .infinity).padding(.vertical, 16)
                                .background(AppTheme.accent, in: Capsule())
                                .foregroundStyle(AppTheme.background)
                        }
                    }

                    if viewModel.navigationState.isNavigating {
                        HStack {
                            Spacer()
                            VStack(spacing: AppSpacing.small) {
                                mapControlButton("location.fill", action: recenterMap)
                                mapControlButton(viewModel.navigationState.isVoiceMuted ? "speaker.slash.fill" : "speaker.wave.2.fill") {
                                    viewModel.setVoiceMuted(!viewModel.navigationState.isVoiceMuted)
                                }
                            }
                        }
                    }
                }
                .padding(AppSpacing.screen)
            }
            .toolbar(.hidden, for: .navigationBar)
            .onChange(of: viewModel.navigationState.destinationName) { _, _ in cameraPosition = .automatic }
            .onChange(of: viewModel.navigationState.isNavigating) { _, navigating in
                if navigating { recenterMap() }
            }
            .onChange(of: selectedMapFeature) { _, feature in
                guard let feature else { return }
                Task {
                    await viewModel.selectMapFeature(feature)
                    selectedMapFeature = nil
                }
            }
        }
    }

    private func routeStat(_ value: String, _ label: String) -> some View {
        VStack(spacing: 3) {
            Text(value).font(.headline)
            Text(label).font(.caption2).foregroundStyle(AppTheme.secondaryText)
        }.frame(maxWidth: .infinity)
    }

    private func recenterMap() {
        guard let latitude = viewModel.location.latitude, let longitude = viewModel.location.longitude else {
            cameraPosition = .userLocation(followsHeading: true, fallback: .automatic)
            return
        }
        cameraPosition = .camera(
            MapCamera(
                centerCoordinate: CLLocationCoordinate2D(latitude: latitude, longitude: longitude),
                distance: 280,
                heading: viewModel.location.headingDegrees ?? 0,
                pitch: 55
            )
        )
    }

    private func mapControlButton(_ icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon)
                .font(.headline)
                .frame(width: 46, height: 46)
                .background(.regularMaterial, in: Circle())
                .shadow(color: .black.opacity(0.1), radius: 8, y: 3)
        }
        .foregroundStyle(AppTheme.primaryText)
    }
}
