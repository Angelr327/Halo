import SwiftUI
import WebKit

struct HelmetCameraDebugView: View {
    @ObservedObject var settings: AppSettings
    @State private var reloadID = UUID()
    @State private var status = "Ready to connect"
    @State private var feed = CameraFeed.debug

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Circle().fill(status == "Connected" ? AppTheme.safe : AppTheme.caution).frame(width: 9, height: 9)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Helmet / Pi camera").font(.headline)
                    Text(status).font(.caption).foregroundStyle(AppTheme.secondaryText)
                }
                Spacer()
                Button { reloadID = UUID() } label: { Label("Reconnect", systemImage: "arrow.clockwise") }
            }.padding()

            Picker("Camera", selection: $feed) {
                ForEach(CameraFeed.allCases) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal)
            .padding(.bottom, 10)
            .onChange(of: feed) { _, _ in reloadID = UUID() }

            if let url = feed.url(settings: settings) {
                HelmetWebView(url: url, status: $status).id(reloadID)
            } else {
                ContentUnavailableView("Invalid Pi endpoint", systemImage: "wifi.exclamationmark", description: Text("Enter a hostname or URL in Settings."))
            }
        }
        .navigationTitle("Helmet Camera Debug")
        .navigationBarTitleDisplayMode(.inline)
        .background(AppTheme.background)
    }
}

private enum CameraFeed: String, CaseIterable, Identifiable {
    case debug, rear, front
    var id: String { rawValue }
    var label: String { rawValue.capitalized }
    @MainActor
    func url(settings: AppSettings) -> URL? {
        self == .debug ? settings.cameraDebugURL : settings.cameraStreamURL(rawValue)
    }
}

private struct HelmetWebView: UIViewRepresentable {
    let url: URL
    @Binding var status: String

    func makeCoordinator() -> Coordinator { Coordinator(status: $status) }

    func makeUIView(context: Context) -> WKWebView {
        let view = WKWebView()
        view.navigationDelegate = context.coordinator
        view.scrollView.contentInsetAdjustmentBehavior = .never
        status = "Connecting…"
        view.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 10))
        return view
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate {
        @Binding var status: String
        init(status: Binding<String>) { _status = status }
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { status = "Connected" }
        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { status = "Unavailable: \(error.localizedDescription)" }
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { status = "Unavailable: \(error.localizedDescription)" }
    }
}
