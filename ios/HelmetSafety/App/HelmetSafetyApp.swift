import SwiftUI

@main
struct HelmetSafetyApp: App {
    @StateObject private var appViewModel = AppViewModel()

    var body: some Scene {
        WindowGroup {
            RootTabView()
                .environmentObject(appViewModel)
        }
    }
}
