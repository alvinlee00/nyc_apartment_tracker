import Foundation

struct PriceHistoryEntry: Codable, Identifiable {
    var id: String { date }
    let price: Int
    let date: String
}

struct SubwayStation: Codable, Identifiable {
    var id: String { "\(name)-\(routes.joined())" }
    let name: String
    let routes: [String]
    let distanceMi: Double

    enum CodingKeys: String, CodingKey {
        case name, routes
        case distanceMi = "distance_mi"
    }
}

struct Listing: Codable, Identifiable {
    let id: String
    let url: String
    let address: String
    let price: String
    let beds: String
    let baths: String
    let sqft: String
    let neighborhood: String
    let imageUrl: String
    let source: String
    let firstSeen: String?
    let daysOnMarket: Int?
    let valueScore: Double?
    let valueGrade: String?
    let latitude: Double?
    let longitude: Double?

    // Detail-only fields
    var crossStreets: String?
    var priceHistory: [PriceHistoryEntry]?
    var nearbyStations: [SubwayStation]?
    var googleMapsUrl: String?

    enum CodingKeys: String, CodingKey {
        case id, url, address, price, beds, baths, sqft, neighborhood, source, latitude, longitude
        case imageUrl = "image_url"
        case firstSeen = "first_seen"
        case daysOnMarket = "days_on_market"
        case valueScore = "value_score"
        case valueGrade = "value_grade"
        case crossStreets = "cross_streets"
        case priceHistory = "price_history"
        case nearbyStations = "nearby_stations"
        case googleMapsUrl = "google_maps_url"
    }

    var priceInt: Int? {
        let digits = price.replacingOccurrences(of: "[^0-9]", with: "", options: .regularExpression)
        return Int(digits)
    }

    var isNew: Bool {
        guard let daysOnMarket else { return true }
        return daysOnMarket <= 1
    }
}

struct ListingsPage: Codable {
    let listings: [Listing]
    let total: Int
    let page: Int
    let perPage: Int
    let hasMore: Bool

    enum CodingKeys: String, CodingKey {
        case listings, total, page
        case perPage = "per_page"
        case hasMore = "has_more"
    }
}

#if DEBUG
extension Listing {
    static let preview = Listing(
        id: "https://streeteasy.com/building/123-east-10th-street",
        url: "https://streeteasy.com/building/123-east-10th-street",
        address: "123 East 10th Street #4A",
        price: "$3,200",
        beds: "1 bed",
        baths: "1 bath",
        sqft: "650 ft²",
        neighborhood: "East Village",
        imageUrl: "",
        source: "streeteasy",
        firstSeen: "2026-02-25T12:00:00",
        daysOnMarket: 2,
        valueScore: 7.5,
        valueGrade: "B",
        latitude: 40.7282,
        longitude: -73.9907
    )

    static let previewPriceDrop = Listing(
        id: "https://streeteasy.com/building/456-west-23rd-street",
        url: "https://streeteasy.com/building/456-west-23rd-street",
        address: "456 West 23rd Street #2B",
        price: "$2,800",
        beds: "Studio",
        baths: "1 bath",
        sqft: "450 ft²",
        neighborhood: "Chelsea",
        imageUrl: "",
        source: "streeteasy",
        firstSeen: "2026-02-01T12:00:00",
        daysOnMarket: 26,
        valueScore: 8.2,
        valueGrade: "A",
        latitude: 40.7465,
        longitude: -74.0014
    )
}
#endif
