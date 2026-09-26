import Combine
import CoreMotion
import Foundation

struct MotionSnapshot: Equatable {
    let accelerationX: Double
    let accelerationY: Double
    let accelerationZ: Double
    let rotationRate: Double

    static let zero = MotionSnapshot(accelerationX: 0, accelerationY: 0, accelerationZ: 0, rotationRate: 0)
}

protocol MotionProviding {
    var isMotionAvailable: Bool { get }
    var motionPublisher: AnyPublisher<MotionSnapshot, Never> { get }
    func startUpdates()
    func stopUpdates()
}

final class MotionService: MotionProviding {
    private let manager = CMMotionManager()
    private let subject = CurrentValueSubject<MotionSnapshot, Never>(.zero)
    private let queue: OperationQueue = {
        let queue = OperationQueue()
        queue.name = "HelmetSafety.MotionUpdates"
        queue.qualityOfService = .userInteractive
        return queue
    }()

    var isMotionAvailable: Bool { manager.isDeviceMotionAvailable }
    var motionPublisher: AnyPublisher<MotionSnapshot, Never> { subject.eraseToAnyPublisher() }

    func startUpdates() {
        guard manager.isDeviceMotionAvailable else { return }
        manager.deviceMotionUpdateInterval = 0.1
        manager.startDeviceMotionUpdates(to: queue) { [weak self] motion, _ in
            guard let motion else { return }
            let rotation = motion.rotationRate
            self?.subject.send(MotionSnapshot(
                accelerationX: motion.userAcceleration.x,
                accelerationY: motion.userAcceleration.y,
                accelerationZ: motion.userAcceleration.z,
                rotationRate: sqrt(rotation.x * rotation.x + rotation.y * rotation.y + rotation.z * rotation.z)
            ))
        }
    }

    func stopUpdates() {
        manager.stopDeviceMotionUpdates()
    }
}
