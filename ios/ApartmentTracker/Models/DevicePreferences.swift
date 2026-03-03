import Foundation

struct GeoBounds: Codable {
    var westLongitude: Double
    var eastLongitude: Double
    var applyTo: [String]

    enum CodingKeys: String, CodingKey {
        case westLongitude = "west_longitude"
        case eastLongitude = "east_longitude"
        case applyTo = "apply_to"
    }
}

struct Filters: Codable {
    var neighborhoods: [String]
    var minPrice: Int
    var maxPrice: Int
    var bedRooms: [String]
    var noFee: Bool
    var geoBounds: GeoBounds?

    enum CodingKeys: String, CodingKey {
        case neighborhoods
        case minPrice = "min_price"
        case maxPrice = "max_price"
        case bedRooms = "bed_rooms"
        case noFee = "no_fee"
        case geoBounds = "geo_bounds"
    }

    static let `default` = Filters(
        neighborhoods: [],
        minPrice: 0,
        maxPrice: 5000,
        bedRooms: [],
        noFee: false,
        geoBounds: nil
    )
}

struct NotificationSettings: Codable {
    var newListings: Bool
    var priceDrops: Bool
    var dailyDigest: Bool

    enum CodingKeys: String, CodingKey {
        case newListings = "new_listings"
        case priceDrops = "price_drops"
        case dailyDigest = "daily_digest"
    }

    static let `default` = NotificationSettings(
        newListings: true,
        priceDrops: true,
        dailyDigest: true
    )
}

struct DevicePreferences: Codable {
    let deviceId: String
    var subscribed: Bool
    var filters: Filters
    var notificationSettings: NotificationSettings

    enum CodingKeys: String, CodingKey {
        case deviceId = "device_id"
        case subscribed
        case filters
        case notificationSettings = "notification_settings"
    }
}

struct DeviceRegisterResponse: Codable {
    let deviceId: String
    let created: Bool

    enum CodingKeys: String, CodingKey {
        case deviceId = "device_id"
        case created
    }
}
