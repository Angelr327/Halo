import SwiftUI

struct SafetyView: View {
    @StateObject private var viewModel: SafetyViewModel
    private let repository: SafetyEventStoring
    private let analysisService: IncidentAnalysisProviding

    init(repository: SafetyEventStoring, analysisService: IncidentAnalysisProviding) {
        self.repository = repository
        self.analysisService = analysisService
        _viewModel = StateObject(wrappedValue: SafetyViewModel(repository: repository))
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    VStack(spacing: 14) {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text("RIDE SAFETY SUMMARY").font(.caption.weight(.bold)).tracking(1).foregroundStyle(AppTheme.secondaryText)
                                Text(viewModel.highRiskCount == 0 ? "All clear" : "Review incidents").font(.title2.weight(.bold))
                            }
                            Spacer()
                            ZStack {
                                Circle().stroke(AppTheme.divider, lineWidth: 8)
                                Circle().trim(from: 0, to: Double(viewModel.safetyScore) / 100).stroke(AppTheme.accent, style: StrokeStyle(lineWidth: 8, lineCap: .round)).rotationEffect(.degrees(-90))
                                Image(systemName: "shield.checkered").foregroundStyle(AppTheme.accent)
                            }.frame(width: 72, height: 72)
                        }
                        Divider()
                        HStack {
                            summaryMetric("\(viewModel.closeCallCount)", "Close calls")
                            summaryMetric("\(viewModel.highRiskCount)", "High risk")
                            summaryMetric("\(viewModel.possibleCollisionCount)", "Collisions")
                        }
                    }
                    .padding(16)
                    .background(AppTheme.surface, in: RoundedRectangle(cornerRadius: 18))
                    .listRowInsets(EdgeInsets()).listRowBackground(Color.clear)
                }

                Section("Incidents") {
                    if viewModel.events.isEmpty {
                        ContentUnavailableView("No incidents", systemImage: "shield.checkered", description: Text("Recorded helmet safety events will appear here."))
                    }
                    ForEach(viewModel.events) { event in
                        NavigationLink {
                            SafetyEventDetailView(event: event, repository: repository, analysisService: analysisService)
                        } label: {
                            SafetyEventCard(event: event)
                        }
                        .foregroundStyle(AppTheme.primaryText)
                        .listRowBackground(AppTheme.surface)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(AppTheme.background)
            .navigationTitle("Safety")
        }
    }

    private func summaryMetric(_ value: String, _ label: String) -> some View {
        VStack(spacing: 3) {
            Text(value).font(.title2.weight(.bold))
            Text(label).font(.caption2).foregroundStyle(AppTheme.secondaryText)
        }.frame(maxWidth: .infinity)
    }
}
