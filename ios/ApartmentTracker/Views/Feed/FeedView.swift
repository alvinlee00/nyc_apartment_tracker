import SwiftUI

struct FeedView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        NavigationStack {
            ZStack {
                Color.appBackground.ignoresSafeArea()

                if appState.listings.isEmpty && !appState.isLoadingListings {
                    emptyState
                } else {
                    listContent
                }
            }
            .navigationTitle("Apartments")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    sortMenu
                }
                ToolbarItem(placement: .topBarTrailing) {
                    if appState.totalListings > 0 {
                        Text("\(appState.totalListings)")
                            .font(.caption)
                            .foregroundColor(.secondaryText)
                    }
                }
            }
        }
    }

    // MARK: - List Content

    private var listContent: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ForEach(appState.listings) { listing in
                    NavigationLink(value: listing.id) {
                        ListingCardView(listing: listing)
                    }
                    .buttonStyle(.plain)
                    .onAppear {
                        if listing.id == appState.listings.last?.id {
                            Task { await appState.loadMore() }
                        }
                    }
                }

                if appState.isLoadingListings {
                    ProgressView()
                        .padding()
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 24)
        }
        .refreshable {
            await appState.loadListings(refresh: true)
        }
        .navigationDestination(for: String.self) { listingId in
            ListingDetailView(listingId: listingId)
        }
    }

    // MARK: - Sort Menu

    private var sortMenu: some View {
        Menu {
            ForEach(AppState.SortOrder.allCases, id: \.self) { order in
                Button {
                    Task { await appState.changeSortOrder(order) }
                } label: {
                    HStack {
                        Text(order.label)
                        if appState.sortOrder == order {
                            Image(systemName: "checkmark")
                        }
                    }
                }
            }
        } label: {
            Image(systemName: "arrow.up.arrow.down")
        }
    }

    // MARK: - Empty State

    private var emptyState: some View {
        VStack(spacing: 16) {
            Image(systemName: "building.2")
                .font(.system(size: 48))
                .foregroundColor(.secondaryText.opacity(0.5))

            Text("No listings yet")
                .font(.sectionTitle)
                .foregroundColor(.primaryText)

            Text("Configure your neighborhood and price preferences in Settings to see matching apartments.")
                .font(.cardBody)
                .foregroundColor(.secondaryText)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)

            if let error = appState.listingsError {
                Text(error)
                    .font(.cardCaption)
                    .foregroundColor(.priceDrop)
                    .padding(.top, 8)
            }

            Button("Refresh") {
                Task { await appState.loadListings(refresh: true) }
            }
            .buttonStyle(.borderedProminent)
            .tint(.appAccent)
        }
    }
}

#if DEBUG
#Preview {
    FeedView()
        .environmentObject(AppState())
}
#endif
