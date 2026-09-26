import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appViewModel: AppViewModel
    @StateObject private var viewModel = SettingsViewModel()

    var body: some View {
        NavigationStack {
            Form {
                Section("Helmet alerts") {
                    Toggle(isOn: $viewModel.audioAlerts) { Label("Audio alerts", systemImage: "speaker.wave.2.fill") }
                    Toggle(isOn: $viewModel.hapticAlerts) { Label("Haptic alerts", systemImage: "iphone.radiowaves.left.and.right") }
                    Toggle(isOn: $viewModel.ledAlerts) { Label("LED alerts", systemImage: "lightbulb.fill") }
                }
                Section("Assistant") {
                    Toggle(isOn: $viewModel.voiceAssistant) { Label("Voice assistant", systemImage: "waveform") }
                }
                Section("Guardian") {
                    Toggle(isOn: $viewModel.guardianLocationSharing) { Label("Location sharing", systemImage: "location.fill") }
                }
                Section {
                    LabeledContent("Data source", value: "Mock Mode")
                    LabeledContent("Helmet link", value: "Not configured")
                    NavigationLink {
                        HelmetSimulatorView(service: appViewModel.helmetService)
                    } label: {
                        Label("Developer Helmet Simulator", systemImage: "hammer.fill")
                    }
                } header: { Text("Development") }
            }
            .scrollContentBackground(.hidden)
            .background(AppTheme.background)
            .tint(AppTheme.accent)
            .listStyle(.insetGrouped)
            .navigationTitle("Settings")
        }
    }
}
