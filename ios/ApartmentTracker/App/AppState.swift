import Foundation
import SwiftUI

@MainActor
final class AppState: ObservableObject {
    // MARK: - Listings
    @Published var listings: [Listing] = []
    @Published var isLoadingListings = false
    @Published var listingsError: String?
    @Published var currentPage = 1
    @Published var hasMore = true
    @Published var sortOrder: SortOrder = .newest
    @Published var totalListings = 0

    // MARK: - Preferences
    @Published var preferences: DevicePreferences?
    @Published var isLoadingPreferences = false

    // MARK: - Meta
    @Published var neighborhoods: [Neighborhood] = []
    @Published var avenues: [Avenue] = []

    // MARK: - Registration
    @Published var isRegistered = false

    private let api = APIClient.shared

    enum SortOrder: String, CaseIterable {
        case newest = "newest"
        case priceAsc = "price_asc"
        case priceDesc = "price_desc"
        case value = "value"

        var label: String {
            switch self {
            case .newest: return "Newest"
            case .priceAsc: return "Price: Low to High"
            case .priceDesc: return "Price: High to Low"
            case .value: return "Best Value"
            }
        }
    }

    // MARK: - Registration

    func registerIfNeeded() async {
        guard let token = DeviceManager.shared.apnsToken else { return }
        do {
            let result = try await api.registerDevice(apnsToken: token)
            isRegistered = true
            if result.created {
                await loadPreferences()
            }
        } catch {
            print("Registration failed: \(error)")
        }
    }

    // MARK: - Listings

    func loadListings(refresh: Bool = false) async {
        if refresh {
            currentPage = 1
            hasMore = true
        }

        guard !isLoadingListings else { return }
        isLoadingListings = true
        listingsError = nil

        do {
            let page = try await api.getListings(
                page: currentPage,
                perPage: 20,
                sort: sortOrder.rawValue
            )

            if refresh {
                listings = page.listings
            } else {
                listings.append(contentsOf: page.listings)
            }

            totalListings = page.total
            hasMore = page.hasMore
            currentPage = page.page + 1
        } catch {
            listingsError = error.localizedDescription
        }

        isLoadingListings = false
    }

    func loadMore() async {
        guard hasMore, !isLoadingListings else { return }
        await loadListings()
    }

    func changeSortOrder(_ order: SortOrder) async {
        sortOrder = order
        await loadListings(refresh: true)
    }

    // MARK: - Preferences

    func loadPreferences() async {
        isLoadingPreferences = true
        do {
            preferences = try await api.getPreferences()
        } catch {
            print("Failed to load preferences: \(error)")
        }
        isLoadingPreferences = false
    }

    func updateFilters(_ filters: Filters) async {
        do {
            preferences = try await api.updateFilters(filters)
            await loadListings(refresh: true)
        } catch {
            print("Failed to update filters: \(error)")
        }
    }

    func updateNotifications(_ settings: NotificationSettings) async {
        do {
            preferences = try await api.updateNotifications(settings)
        } catch {
            print("Failed to update notifications: \(error)")
        }
    }

    // MARK: - Meta

    func loadMeta() async {
        do {
            async let n = api.getNeighborhoods()
            async let a = api.getAvenues()
            neighborhoods = try await n
            avenues = try await a
        } catch {
            print("Failed to load meta: \(error)")
        }
    }
}
