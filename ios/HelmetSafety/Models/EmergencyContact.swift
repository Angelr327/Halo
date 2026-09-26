import Foundation

struct EmergencyContact: Identifiable, Codable, Equatable {
    var id: UUID
    var name: String
    var relationship: String
    var phoneNumber: String
    var isPrimary: Bool
}

enum GuardianAlertState: Equatable {
    case idle
    case checkingIn
    case riderConfirmedSafe
    case helpRequested
}
