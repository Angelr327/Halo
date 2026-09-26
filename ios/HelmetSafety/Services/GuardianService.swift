import Combine
import Foundation

protocol GuardianProviding: AnyObject {
    var sessionPublisher: AnyPublisher<RideSession, Never> { get }
    var alertStatePublisher: AnyPublisher<GuardianAlertState, Never> { get }
    var session: RideSession { get }
    var alertState: GuardianAlertState { get }
    var emergencyContacts: [EmergencyContact] { get }
    func startRide()
    func pauseRide()
    func endRide()
    func confirmSafe()
    func requestHelp()
}

final class GuardianService: GuardianProviding {
    private let sessionSubject = CurrentValueSubject<RideSession, Never>(.empty)
    private let alertSubject = CurrentValueSubject<GuardianAlertState, Never>(.idle)
    private var cancellables = Set<AnyCancellable>()
    private var previousLocation: RideLocation?
    private var wasHighRisk = false
    private let rideHistoryKey = "HelmetSafety.savedRideSessions"

    let emergencyContacts = [
        EmergencyContact(id: UUID(), name: "Jordan", relationship: "Emergency contact", phoneNumber: "(555) 010-2026", isPrimary: true)
    ]

    init(helmetService: HelmetDataProviding, locationService: LocationProviding) {
        helmetService.helmetStatePublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.applyHelmetState($0) }
            .store(in: &cancellables)
        locationService.locationPublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.applyLocation($0) }
            .store(in: &cancellables)
    }

    var sessionPublisher: AnyPublisher<RideSession, Never> { sessionSubject.eraseToAnyPublisher() }
    var alertStatePublisher: AnyPublisher<GuardianAlertState, Never> { alertSubject.eraseToAnyPublisher() }
    var session: RideSession { sessionSubject.value }
    var alertState: GuardianAlertState { alertSubject.value }

    func startRide() {
        var session = sessionSubject.value
        if session.rideStatus == .ended || session.rideStatus == .notStarted {
            session = .empty
            session.id = UUID()
            session.startTime = Date()
        }
        session.rideStatus = .active
        session.endTime = nil
        session.lastUpdate = Date()
        previousLocation = session.currentLocation
        sessionSubject.send(session)
    }

    func pauseRide() {
        var session = sessionSubject.value
        guard session.rideStatus == .active else { return }
        session.rideStatus = .paused
        session.lastUpdate = Date()
        sessionSubject.send(session)
    }

    func endRide() {
        var session = sessionSubject.value
        session.rideStatus = .ended
        session.endTime = Date()
        session.currentSpeed = 0
        session.lastUpdate = Date()
        sessionSubject.send(session)
        saveCompletedRide(session)
    }

    private func saveCompletedRide(_ session: RideSession) {
        let defaults = UserDefaults.standard
        let decoder = JSONDecoder()
        var rides = defaults.data(forKey: rideHistoryKey).flatMap { try? decoder.decode([RideSession].self, from: $0) } ?? []
        rides.removeAll { $0.id == session.id }
        rides.insert(session, at: 0)
        if let data = try? JSONEncoder().encode(rides) { defaults.set(data, forKey: rideHistoryKey) }
    }

    func confirmSafe() {
        var session = sessionSubject.value
        session.possibleCrash = false
        session.rideStatus = .active
        session.lastUpdate = Date()
        sessionSubject.send(session)
        alertSubject.send(.riderConfirmedSafe)
    }

    func requestHelp() {
        var session = sessionSubject.value
        session.possibleCrash = true
        session.rideStatus = .possibleEmergency
        session.lastUpdate = Date()
        sessionSubject.send(session)
        alertSubject.send(.helpRequested)
    }

    private func applyHelmetState(_ helmet: HelmetState) {
        var session = sessionSubject.value
        session.helmetBattery = helmet.batteryPercentage
        session.helmetConnected = helmet.isConnected
        session.totalHazards = helmet.rideHazardCount
        let isHighRisk = helmet.severity == .high || helmet.severity == .critical
        if isHighRisk && !wasHighRisk { session.highRiskEvents += 1 }
        wasHighRisk = isHighRisk

        if helmet.severity == .critical && !session.possibleCrash {
            session.possibleCrash = true
            session.rideStatus = .possibleEmergency
            session.lastUpdate = Date()
            alertSubject.send(.checkingIn)
        }
        sessionSubject.send(session)
    }

    private func applyLocation(_ location: LocationSnapshot) {
        guard let latitude = location.latitude, let longitude = location.longitude else { return }
        var session = sessionSubject.value
        let newLocation = RideLocation(latitude: latitude, longitude: longitude)
        if session.rideStatus == .active, let previousLocation {
            session.distanceTravelled += distanceMeters(from: previousLocation, to: newLocation) / 1_609.344
        }
        self.previousLocation = newLocation
        session.currentLocation = newLocation
        session.currentSpeed = location.speedMPH
        session.lastUpdate = Date()
        sessionSubject.send(session)
    }

    private func distanceMeters(from start: RideLocation, to end: RideLocation) -> Double {
        let earthRadius = 6_371_000.0
        let latitudeDelta = (end.latitude - start.latitude) * .pi / 180
        let longitudeDelta = (end.longitude - start.longitude) * .pi / 180
        let a = sin(latitudeDelta / 2) * sin(latitudeDelta / 2)
            + cos(start.latitude * .pi / 180) * cos(end.latitude * .pi / 180)
            * sin(longitudeDelta / 2) * sin(longitudeDelta / 2)
        return earthRadius * 2 * atan2(sqrt(a), sqrt(1 - a))
    }
}
