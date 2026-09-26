import Combine
import CoreLocation
import MapKit

protocol NavigationProviding: AnyObject {
    var navigationPublisher: AnyPublisher<NavigationState, Never> { get }
    func calculateRoute(to destination: String, from origin: CLLocationCoordinate2D) async throws
    func startNavigation()
    func stopNavigation()
    func updateLocation(_ coordinate: CLLocationCoordinate2D)
}

final class NavigationService: NavigationProviding {
    private let stateSubject = CurrentValueSubject<NavigationState, Never>(NavigationState())
    private var steps: [MKRoute.Step] = []
    private var currentStepIndex = 0

    var navigationPublisher: AnyPublisher<NavigationState, Never> {
        stateSubject.eraseToAnyPublisher()
    }

    func calculateRoute(to destination: String, from origin: CLLocationCoordinate2D) async throws {
        let request = MKLocalSearch.Request()
        request.naturalLanguageQuery = destination
        request.resultTypes = [.address, .pointOfInterest]
        request.region = MKCoordinateRegion(center: origin, latitudinalMeters: 50_000, longitudinalMeters: 50_000)

        let response = try await MKLocalSearch(request: request).start()
        guard let destinationItem = response.mapItems.first else { throw NavigationError.destinationNotFound }

        let directionsRequest = MKDirections.Request()
        directionsRequest.source = MKMapItem(placemark: MKPlacemark(coordinate: origin))
        directionsRequest.destination = destinationItem
        // MapKit has no public cycling transport type, so walking is the safest
        // available approximation for an MVP bicycle route.
        directionsRequest.transportType = .walking
        directionsRequest.requestsAlternateRoutes = false

        let directions = try await MKDirections(request: directionsRequest).calculate()
        guard let route = directions.routes.first else { throw NavigationError.routeUnavailable }

        steps = route.steps.filter { !$0.instructions.isEmpty && $0.distance > 0 }
        currentStepIndex = 0
        var state = stateSubject.value
        state.destinationName = destinationItem.name ?? destination
        state.destinationCoordinate = destinationItem.placemark.coordinate
        state.route = route
        state.maneuver = steps.first.map(Self.simplify)
        state.isNavigating = false
        state.statusMessage = "Route ready"
        stateSubject.send(state)
    }

    func startNavigation() {
        guard stateSubject.value.route != nil else { return }
        var state = stateSubject.value
        state.isNavigating = true
        state.statusMessage = "Navigation active"
        stateSubject.send(state)
    }

    func stopNavigation() {
        var state = stateSubject.value
        state.isNavigating = false
        state.statusMessage = state.route == nil ? "Enter a destination" : "Route ready"
        stateSubject.send(state)
    }

    func updateLocation(_ coordinate: CLLocationCoordinate2D) {
        guard stateSubject.value.isNavigating, currentStepIndex < steps.count else { return }
        let step = steps[currentStepIndex]
        let target = Self.lastCoordinate(of: step.polyline)
        let remaining = CLLocation(latitude: coordinate.latitude, longitude: coordinate.longitude)
            .distance(from: CLLocation(latitude: target.latitude, longitude: target.longitude))

        if remaining < 22, currentStepIndex < steps.count - 1 {
            currentStepIndex += 1
        }

        var maneuver = Self.simplify(steps[currentStepIndex])
        maneuver = NavigationManeuver(maneuverType: maneuver.maneuverType, direction: maneuver.direction, streetName: maneuver.streetName, distanceMeters: max(0, remaining))
        var state = stateSubject.value
        state.maneuver = maneuver
        stateSubject.send(state)
    }

    static func simplify(_ step: MKRoute.Step) -> NavigationManeuver {
        let instruction = step.instructions.lowercased()
        let direction: NavigationDirection
        if instruction.contains("left") { direction = .left }
        else if instruction.contains("right") { direction = .right }
        else if instruction.contains("arrive") || instruction.contains("destination") { direction = .arrive }
        else if instruction.contains("continue") || instruction.contains("straight") || instruction.contains("proceed") { direction = .straight }
        else { direction = .unknown }

        let type: ManeuverType
        if instruction.contains("u-turn") { type = .uTurn }
        else if instruction.contains("slight") { type = .slightTurn }
        else if instruction.contains("sharp") { type = .sharpTurn }
        else if instruction.contains("roundabout") { type = .roundabout }
        else if instruction.contains("merge") { type = .merge }
        else if direction == .left || direction == .right { type = .turn }
        else if direction == .arrive { type = .arrive }
        else if direction == .straight { type = .continueStraight }
        else { type = .unknown }

        return NavigationManeuver(
            maneuverType: type,
            direction: direction,
            streetName: streetName(from: step.instructions),
            distanceMeters: step.distance
        )
    }

    private static func streetName(from instruction: String) -> String {
        let markers = [" onto ", " on ", " toward "]
        for marker in markers {
            if let range = instruction.range(of: marker, options: .caseInsensitive) {
                return String(instruction[range.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }
        return instruction.isEmpty ? "Continue" : instruction
    }

    private static func lastCoordinate(of polyline: MKPolyline) -> CLLocationCoordinate2D {
        guard polyline.pointCount > 0 else { return kCLLocationCoordinate2DInvalid }
        var coordinate = CLLocationCoordinate2D()
        polyline.getCoordinates(&coordinate, range: NSRange(location: polyline.pointCount - 1, length: 1))
        return coordinate
    }
}
