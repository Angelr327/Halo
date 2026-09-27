import CoreLocation
import Foundation
import MapKit

struct Destination: Equatable, Codable, Identifiable {
    let name: String
    let subtitle: String?
    let coordinate: CLLocationCoordinate2D
    var id: String { "\(coordinate.latitude),\(coordinate.longitude)" }

    static func == (lhs: Destination, rhs: Destination) -> Bool {
        lhs.name == rhs.name && lhs.subtitle == rhs.subtitle &&
        lhs.coordinate.latitude == rhs.coordinate.latitude &&
        lhs.coordinate.longitude == rhs.coordinate.longitude
    }

    private enum CodingKeys: String, CodingKey { case name, subtitle, latitude, longitude }

    init(name: String, subtitle: String?, coordinate: CLLocationCoordinate2D) {
        self.name = name
        self.subtitle = subtitle
        self.coordinate = coordinate
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        name = try values.decode(String.self, forKey: .name)
        subtitle = try values.decodeIfPresent(String.self, forKey: .subtitle)
        coordinate = CLLocationCoordinate2D(latitude: try values.decode(Double.self, forKey: .latitude), longitude: try values.decode(Double.self, forKey: .longitude))
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(name, forKey: .name)
        try values.encodeIfPresent(subtitle, forKey: .subtitle)
        try values.encode(coordinate.latitude, forKey: .latitude)
        try values.encode(coordinate.longitude, forKey: .longitude)
    }
}

struct DestinationSuggestion: Identifiable, Equatable {
    let id: String
    let title: String
    let subtitle: String
    let completion: MKLocalSearchCompletion

    static func == (lhs: DestinationSuggestion, rhs: DestinationSuggestion) -> Bool { lhs.id == rhs.id }
}
