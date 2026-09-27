import Foundation

enum HelmetDataMode: String, CaseIterable, Identifiable {
    case mock = "Mock"
    case real = "Real"
    var id: String { rawValue }
}

@MainActor
final class AppSettings: ObservableObject {
    @Published var dataMode: HelmetDataMode { didSet { defaults.set(dataMode.rawValue, forKey: Keys.mode) } }
    @Published var piEndpoint: String { didSet { defaults.set(piEndpoint, forKey: Keys.endpoint) } }
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        dataMode = HelmetDataMode(rawValue: defaults.string(forKey: Keys.mode) ?? "") ?? .mock
        piEndpoint = defaults.string(forKey: Keys.endpoint) ?? "http://helmet.local:8080"
    }

    var normalizedBaseURL: URL? {
        var value = piEndpoint.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return nil }
        if !value.contains("://") { value = "http://" + value }
        guard var components = URLComponents(string: value), components.host != nil else { return nil }
        components.path = ""
        components.fragment = nil
        return components.url
    }

    var liveViewURL: URL? { normalizedBaseURL?.appendingPathComponent("view") }
    var cameraDebugURL: URL? { normalizedBaseURL }

    func cameraStreamURL(_ camera: String) -> URL? {
        guard let base = piURL(path: "/stream.mjpg"),
              var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return nil }
        components.queryItems = [URLQueryItem(name: "camera", value: camera)]
        return components.url
    }

    func piURL(path: String) -> URL? {
        guard let base = normalizedBaseURL, var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return nil }
        components.path = path.hasPrefix("/") ? path : "/" + path
        return components.url
    }

    private enum Keys {
        static let mode = "HelmetSafety.dataMode"
        static let endpoint = "HelmetSafety.piEndpoint"
    }
}
