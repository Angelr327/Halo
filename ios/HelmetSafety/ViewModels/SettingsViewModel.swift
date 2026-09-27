import Foundation

final class SettingsViewModel: ObservableObject {
    @Published var audioAlerts = true
    @Published var hapticAlerts = true
    @Published var ledAlerts = true
    @Published var voiceAssistant = true
}
