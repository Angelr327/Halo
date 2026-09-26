import Combine
import CoreLocation
import Foundation

protocol LocationProviding: AnyObject {
    var authorizationStatus: CLAuthorizationStatus { get }
    var locationPublisher: AnyPublisher<LocationSnapshot, Never> { get }
    func requestAuthorization()
    func startUpdating()
    func stopUpdating()
}

final class LocationService: NSObject, LocationProviding, CLLocationManagerDelegate {
    private let manager = CLLocationManager()
    private let snapshotSubject = CurrentValueSubject<LocationSnapshot, Never>(.initial)

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.distanceFilter = 1
        manager.activityType = .fitness
    }

    var authorizationStatus: CLAuthorizationStatus { manager.authorizationStatus }
    var locationPublisher: AnyPublisher<LocationSnapshot, Never> { snapshotSubject.eraseToAnyPublisher() }

    func requestAuthorization() {
        manager.requestWhenInUseAuthorization()
    }

    func startUpdating() {
        updateAuthorizationState(manager.authorizationStatus)
        guard CLLocationManager.locationServicesEnabled() else { publish(status: .unavailable); return }
        guard manager.authorizationStatus == .authorizedAlways || manager.authorizationStatus == .authorizedWhenInUse else { return }
        manager.startUpdatingLocation()
        if CLLocationManager.headingAvailable() { manager.startUpdatingHeading() }
        publish(status: .searching)
    }

    func stopUpdating() {
        manager.stopUpdatingLocation()
        manager.stopUpdatingHeading()
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        updateAuthorizationState(manager.authorizationStatus)
        if manager.authorizationStatus == .authorizedAlways || manager.authorizationStatus == .authorizedWhenInUse { startUpdating() }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }
        var snapshot = snapshotSubject.value
        snapshot.latitude = location.coordinate.latitude
        snapshot.longitude = location.coordinate.longitude
        snapshot.speedMPH = location.speed >= 0 ? location.speed * 2.236_936_292_1 : 0
        snapshot.status = .connected
        snapshot.authorizationStatus = manager.authorizationStatus
        snapshotSubject.send(snapshot)
    }

    func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        guard newHeading.headingAccuracy >= 0 else { return }
        var snapshot = snapshotSubject.value
        snapshot.headingDegrees = newHeading.trueHeading >= 0 ? newHeading.trueHeading : newHeading.magneticHeading
        snapshotSubject.send(snapshot)
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        if let locationError = error as? CLError, locationError.code == .locationUnknown { publish(status: .searching) }
        else { publish(status: .unavailable) }
    }

    private func updateAuthorizationState(_ authorization: CLAuthorizationStatus) {
        var snapshot = snapshotSubject.value
        snapshot.authorizationStatus = authorization
        switch authorization {
        case .notDetermined: snapshot.status = .notDetermined
        case .restricted: snapshot.status = .restricted
        case .denied: snapshot.status = .denied
        case .authorizedAlways, .authorizedWhenInUse: snapshot.status = snapshot.latitude == nil ? .searching : .connected
        @unknown default: snapshot.status = .unavailable
        }
        snapshotSubject.send(snapshot)
    }

    private func publish(status: LocationConnectionStatus) {
        var snapshot = snapshotSubject.value
        snapshot.status = status
        snapshotSubject.send(snapshot)
    }
}
