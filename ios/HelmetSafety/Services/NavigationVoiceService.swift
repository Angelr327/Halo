import AVFoundation
import Foundation

protocol NavigationVoiceProviding: AnyObject {
    func speak(_ text: String)
    func stop()
}

/// Uses ElevenLabs when an ephemeral/server-issued key is supplied at runtime.
/// Falls back to Apple's on-device voice so navigation never becomes silent.
final class NavigationVoiceService: NSObject, NavigationVoiceProviding, AVAudioPlayerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private var player: AVAudioPlayer?
    private var speechTask: Task<Void, Never>?

    func speak(_ text: String) {
        stop()
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        speechTask = Task { [weak self] in
            guard let self else { return }
            if let configuration = ElevenLabsConfiguration.runtime {
                do {
                    let audio = try await self.fetchElevenLabsSpeech(text, configuration: configuration)
                    guard !Task.isCancelled else { return }
                    try await MainActor.run { try self.play(audio) }
                    return
                } catch {
                    // Network, quota, and configuration failures gracefully use the device voice.
                }
            }
            guard !Task.isCancelled else { return }
            await MainActor.run { self.speakOnDevice(text) }
        }
    }

    func stop() {
        speechTask?.cancel()
        speechTask = nil
        player?.stop()
        player = nil
        synthesizer.stopSpeaking(at: .immediate)
    }

    private func fetchElevenLabsSpeech(_ text: String, configuration: ElevenLabsConfiguration) async throws -> Data {
        var components = URLComponents(string: "https://api.elevenlabs.io/v1/text-to-speech/\(configuration.voiceID)")!
        components.queryItems = [URLQueryItem(name: "output_format", value: "mp3_44100_128")]
        var request = URLRequest(url: components.url!)
        request.httpMethod = "POST"
        request.setValue(configuration.apiKey, forHTTPHeaderField: "xi-api-key")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(ElevenLabsRequest(text: text, modelID: "eleven_flash_v2_5"))
        request.timeoutInterval = 8
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
            throw URLError(.badServerResponse)
        }
        return data
    }

    @MainActor
    private func play(_ data: Data) throws {
        try AVAudioSession.sharedInstance().setCategory(.playback, mode: .voicePrompt, options: [.duckOthers])
        try AVAudioSession.sharedInstance().setActive(true)
        player = try AVAudioPlayer(data: data)
        player?.delegate = self
        player?.prepareToPlay()
        player?.play()
    }

    @MainActor
    private func speakOnDevice(_ text: String) {
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: Locale.current.language.languageCode?.identifier ?? "en-US")
        utterance.rate = 0.5
        synthesizer.speak(utterance)
    }
}

private struct ElevenLabsConfiguration {
    let apiKey: String
    let voiceID: String

    static var runtime: Self? {
        let environment = ProcessInfo.processInfo.environment
        let info = Bundle.main.infoDictionary ?? [:]
        let key = (environment["ELEVENLABS_API_KEY"] ?? info["ElevenLabsAPIKey"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard let key, !key.isEmpty, !key.hasPrefix("$(") else { return nil }
        let voice = (environment["ELEVENLABS_VOICE_ID"] ?? info["ElevenLabsVoiceID"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return Self(apiKey: key, voiceID: voice?.isEmpty == false ? voice! : "21m00Tcm4TlvDq8ikWAM")
    }
}

private struct ElevenLabsRequest: Encodable {
    let text: String
    let modelID: String

    enum CodingKeys: String, CodingKey {
        case text
        case modelID = "model_id"
    }
}
