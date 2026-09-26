import SwiftUI

struct HelmetSimulatorView: View {
    @StateObject private var viewModel: HelmetSimulatorViewModel

    init(service: HelmetSimulationProviding) {
        _viewModel = StateObject(wrappedValue: HelmetSimulatorViewModel(service: service))
    }

    var body: some View {
        List {
            Section("Current simulation") {
                LabeledContent("Scenario", value: viewModel.activeScenario.rawValue)
                LabeledContent("Connection", value: viewModel.helmetState.isConnected ? "Connected" : "Disconnected")
                LabeledContent("Battery", value: "\(viewModel.helmetState.batteryPercentage)%")
                LabeledContent("Severity", value: viewModel.helmetState.severity.rawValue.capitalized)
            }

            Section("Trigger event") {
                ForEach(viewModel.scenarios) { scenario in
                    Button {
                        viewModel.trigger(scenario)
                    } label: {
                        HStack {
                            Label(scenario.rawValue, systemImage: scenario.systemImage)
                            Spacer()
                            if scenario == viewModel.activeScenario {
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundStyle(AppTheme.accent)
                            }
                        }
                    }
                    .foregroundStyle(.primary)
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background(AppTheme.background)
        .navigationTitle("Developer Helmet Simulator")
        .navigationBarTitleDisplayMode(.inline)
    }
}
