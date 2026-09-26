import MapKit
import SwiftUI

struct NavigationMapView: View {
    @StateObject private var viewModel: MapViewModel
    @State private var cameraPosition: MapCameraPosition = .automatic

    init(navigationService: NavigationProviding, locationService: LocationProviding, safetyRepository: SafetyEventStoring) {
        _viewModel = StateObject(wrappedValue: MapViewModel(navigationService: navigationService, locationService: locationService, safetyRepository: safetyRepository))
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Map(position: $cameraPosition) {
                    UserAnnotation()
                    if let route = viewModel.navigationState.route {
                        MapPolyline(route.polyline).stroke(AppTheme.accent, lineWidth: 7)
                    }
                    if let coordinate = viewModel.navigationState.destinationCoordinate {
                        Marker(viewModel.navigationState.destinationName ?? "Destination", coordinate: coordinate)
                            .tint(AppTheme.accent)
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
                    HStack {
                        Image(systemName: "magnifyingglass").foregroundStyle(AppTheme.secondaryText)
                        TextField("Enter destination", text: $viewModel.destinationQuery)
                            .textFieldStyle(.plain).submitLabel(.search)
                            .onSubmit { Task { await viewModel.search() } }
                        Button { Task { await viewModel.search() } } label: {
                            if viewModel.isLoading { ProgressView() } else { Image(systemName: "arrow.right.circle.fill").font(.title2) }
                        }
                        .disabled(viewModel.isLoading)
                    }
                    .padding(14).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
                    .shadow(color: .black.opacity(0.12), radius: 14, y: 6)

                    if let error = viewModel.errorMessage {
                        Text(error).font(.caption.weight(.medium)).foregroundStyle(AppTheme.danger).padding(10).frame(maxWidth: .infinity, alignment: .leading).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
                    }

                    Spacer()

                    if viewModel.navigationState.route != nil {
                        HStack(spacing: 24) {
                            routeStat(viewModel.routeDistanceText, "Distance")
                            Divider().frame(height: 34)
                            routeStat(viewModel.routeTimeText, "Est. time")
                            Divider().frame(height: 34)
                            routeStat("\(viewModel.safetyEvents.count)", "Safety pins")
                        }
                        .padding(14).frame(maxWidth: .infinity)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
                        Button(action: viewModel.toggleNavigation) {
                            Text(viewModel.navigationState.isNavigating ? "STOP NAVIGATION" : "START NAVIGATION")
                                .font(.headline.weight(.bold)).frame(maxWidth: .infinity).padding(.vertical, 16)
                                .background(viewModel.navigationState.isNavigating ? AppTheme.primaryText : AppTheme.accent, in: Capsule())
                                .foregroundStyle(viewModel.navigationState.isNavigating ? .white : AppTheme.background)
                        }
                    }
                }
                .padding(AppSpacing.screen)
            }
            .toolbar(.hidden, for: .navigationBar)
            .onChange(of: viewModel.navigationState.destinationName) { _, _ in cameraPosition = .automatic }
        }
    }

    private func routeStat(_ value: String, _ label: String) -> some View {
        VStack(spacing: 3) {
            Text(value).font(.headline)
            Text(label).font(.caption2).foregroundStyle(AppTheme.secondaryText)
        }.frame(maxWidth: .infinity)
    }
}
