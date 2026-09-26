import Foundation

struct GuardianRide: Equatable {
    let riderName: String
    let locationName: String
    let lastUpdated: Date
    let distanceMiles: Double
    let durationMinutes: Int
    let isRideActive: Bool
}
