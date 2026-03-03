import Foundation
import UIKit

/// HTTP client for the NYC Apartment Tracker backend API.
final class APIClient {
    static let shared = APIClient()

    #if DEBUG
    private let baseURL = URL(string: "http://localhost:8080")!
    #else
    private let baseURL = URL(string: "https://nyc-apartment-tracker-api.fly.dev")!
    #endif

    private let session = URLSession.shared
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        return d
    }()

    private init() {}

    private var deviceId: String { DeviceManager.shared.deviceId }

    // MARK: - Generic request

    private func request<T: Decodable>(
        _ method: String,
        path: String,
        body: Encodable? = nil,
        queryItems: [URLQueryItem]? = nil
    ) async throws -> T {
        var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        components.queryItems = queryItems

        var req = URLRequest(url: components.url!)
        req.httpMethod = method
        req.setValue(deviceId, forHTTPHeaderField: "X-Device-ID")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        if let body {
            req.httpBody = try JSONEncoder().encode(body)
        }

        let (data, response) = try await session.data(for: req)

        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        guard (200...299).contains(http.statusCode) else {
            throw APIError.httpError(http.statusCode, String(data: data, encoding: .utf8))
        }

        return try decoder.decode(T.self, from: data)
    }

    private func requestNoContent(
        _ method: String,
        path: String,
        body: Encodable? = nil
    ) async throws {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.httpMethod = method
        req.setValue(deviceId, forHTTPHeaderField: "X-Device-ID")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        if let body {
            req.httpBody = try JSONEncoder().encode(body)
        }

        let (data, response) = try await session.data(for: req)

        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        guard (200...299).contains(http.statusCode) else {
            throw APIError.httpError(http.statusCode, String(data: data, encoding: .utf8))
        }
    }

    // MARK: - Device

    struct RegisterBody: Encodable {
        let apnsToken: String
        let deviceName: String?

        enum CodingKeys: String, CodingKey {
            case apnsToken = "apns_token"
            case deviceName = "device_name"
        }
    }

    func registerDevice(apnsToken: String) async throws -> DeviceRegisterResponse {
        try await request("POST", path: "/devices/register", body: RegisterBody(
            apnsToken: apnsToken,
            deviceName: deviceName()
        ))
    }

    func getPreferences() async throws -> DevicePreferences {
        try await request("GET", path: "/devices/me")
    }

    func updateFilters(_ filters: Filters) async throws -> DevicePreferences {
        try await request("PUT", path: "/devices/me/filters", body: filters)
    }

    func updateNotifications(_ settings: NotificationSettings) async throws -> DevicePreferences {
        try await request("PUT", path: "/devices/me/notifications", body: settings)
    }

    func unsubscribe() async throws {
        try await requestNoContent("DELETE", path: "/devices/me")
    }

    // MARK: - Listings

    func getListings(page: Int = 1, perPage: Int = 20, sort: String = "newest") async throws -> ListingsPage {
        try await request("GET", path: "/listings", queryItems: [
            URLQueryItem(name: "page", value: "\(page)"),
            URLQueryItem(name: "per_page", value: "\(perPage)"),
            URLQueryItem(name: "sort", value: sort),
        ])
    }

    func getListingDetail(id: String) async throws -> Listing {
        try await request("GET", path: "/listings/\(id)")
    }

    // MARK: - Meta

    func getNeighborhoods() async throws -> [Neighborhood] {
        try await request("GET", path: "/meta/neighborhoods")
    }

    func getAvenues() async throws -> [Avenue] {
        try await request("GET", path: "/meta/avenues")
    }

    // MARK: - Helpers

    private func deviceName() -> String? {
        return UIDevice.current.name
    }
}

enum APIError: LocalizedError {
    case invalidResponse
    case httpError(Int, String?)

    var errorDescription: String? {
        switch self {
        case .invalidResponse: return "Invalid server response"
        case .httpError(let code, let body): return "HTTP \(code): \(body ?? "Unknown error")"
        }
    }
}
