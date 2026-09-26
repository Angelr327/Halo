import SwiftUI

struct EmergencyCheckInPresenter: View {
    @StateObject private var viewModel: EmergencyCheckInViewModel

    init(service: GuardianProviding) {
        _viewModel = StateObject(wrappedValue: EmergencyCheckInViewModel(service: service))
    }

    var body: some View {
        Color.clear
            .fullScreenCover(isPresented: Binding(get: { viewModel.isPresented }, set: { _ in })) {
                EmergencyCheckInView(viewModel: viewModel)
                    .interactiveDismissDisabled()
            }
    }
}

struct EmergencyCheckInView: View {
    @ObservedObject var viewModel: EmergencyCheckInViewModel

    var body: some View {
        ZStack {
            AppTheme.background.ignoresSafeArea()
            VStack(spacing: 28) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 72, weight: .bold)).foregroundStyle(AppTheme.danger)
                VStack(spacing: 10) {
                    Text("ARE YOU OKAY?").font(.largeTitle.weight(.black)).tracking(1.5)
                    Text("A possible collision was detected. Confirm your status so Guardian knows what to do.")
                        .multilineTextAlignment(.center).foregroundStyle(AppTheme.secondaryText)
                }
                Button(action: viewModel.confirmSafe) {
                    Label("I'M OK", systemImage: "checkmark.circle.fill")
                        .font(.title3.weight(.black)).frame(maxWidth: .infinity).padding(.vertical, 18)
                        .background(AppTheme.safe, in: RoundedRectangle(cornerRadius: 18)).foregroundStyle(AppTheme.background)
                }
                Button(action: viewModel.requestHelp) {
                    Label("I NEED HELP", systemImage: "sos.circle.fill")
                        .font(.title3.weight(.black)).frame(maxWidth: .infinity).padding(.vertical, 18)
                        .background(AppTheme.danger, in: RoundedRectangle(cornerRadius: 18)).foregroundStyle(.white)
                }
                Text("MVP simulation only. No emergency services or contacts will be called.")
                    .font(.caption).multilineTextAlignment(.center).foregroundStyle(AppTheme.secondaryText)
            }.padding(28)
        }
    }
}
