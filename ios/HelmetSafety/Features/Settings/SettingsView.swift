import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appViewModel: AppViewModel
    @StateObject private var viewModel = SettingsViewModel()

    var body: some View {
        NavigationStack {
            Form {
                Section("Helmet") {
                    Picker("Mode", selection: Binding(get: { appViewModel.settings.dataMode }, set: {
                        appViewModel.settings.dataMode = $0
                        appViewModel.safetyEventRepository.setMockMode($0 == .mock)
                    })) {
                        ForEach(HelmetDataMode.allCases) { Text($0.rawValue).tag($0) }
                    }
                    TextField("Pi endpoint", text: Binding(get: { appViewModel.settings.piEndpoint }, set: { appViewModel.settings.piEndpoint = $0 }))
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    LabeledContent("Connection", value: appViewModel.settings.dataMode == .mock ? "Mock connected" : "Pi live data")
                    if appViewModel.settings.dataMode == .real {
                        Text("CV telemetry, safety status, sonar data, incidents, and camera debug are supplied by the Pi endpoint.").font(.caption).foregroundStyle(AppTheme.secondaryText)
                    }
                }
                Section("Alerts") {
                    Toggle(isOn: $viewModel.audioAlerts) { Label("Audio alerts", systemImage: "speaker.wave.2.fill") }
                    Toggle(isOn: $viewModel.hapticAlerts) { Label("Haptic alerts", systemImage: "iphone.radiowaves.left.and.right") }
                    Toggle(isOn: $viewModel.ledAlerts) { Label("LED alerts", systemImage: "lightbulb.fill") }
                }
                Section("AI") {
                    Toggle(isOn: $viewModel.voiceAssistant) { Label("Voice assistant", systemImage: "waveform") }
                    LabeledContent("Incident analysis", value: "Development mock")
                    Text("No Gemini key is stored in the app.").font(.caption).foregroundStyle(AppTheme.secondaryText)
                }
                Section {
                    NavigationLink {
                        HelmetCameraDebugView(settings: appViewModel.settings)
                    } label: {
                        Label("Helmet Camera Debug", systemImage: "video.fill")
                    }
                    NavigationLink {
                        HelmetSimulatorView(service: appViewModel.helmetService)
                    } label: {
                        Label("Developer Helmet Simulator", systemImage: "hammer.fill")
                    }
                } header: { Text("Developer") }
            }
            .scrollContentBackground(.hidden)
            .background(AppTheme.background)
            .tint(AppTheme.accent)
            .listStyle(.insetGrouped)
            .navigationTitle("Settings")
        }
    }
}
