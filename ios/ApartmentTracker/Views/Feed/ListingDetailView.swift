import SwiftUI

struct ListingDetailView: View {
    let listingId: String
    @EnvironmentObject private var appState: AppState
    @State private var listing: Listing?
    @State private var isLoading = true
    @State private var error: String?

    var body: some View {
        ScrollView {
            if isLoading {
                ProgressView()
                    .padding(.top, 100)
            } else if let listing {
                detailContent(listing)
            } else if let error {
                errorView(error)
            }
        }
        .background(Color.appBackground)
        .navigationTitle("Details")
        .navigationBarTitleDisplayMode(.inline)
        .task { await loadDetail() }
    }

    // MARK: - Detail Content

    private func detailContent(_ listing: Listing) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            // Image
            if let imageUrl = URL(string: listing.imageUrl), !listing.imageUrl.isEmpty {
                AsyncImage(url: imageUrl) { phase in
                    if let image = phase.image {
                        image
                            .resizable()
                            .aspectRatio(16/10, contentMode: .fill)
                    } else {
                        Rectangle()
                            .fill(Color.gray.opacity(0.1))
                            .aspectRatio(16/10, contentMode: .fill)
                            .overlay(ProgressView())
                    }
                }
                .frame(maxHeight: 250)
                .clipped()
            }

            VStack(alignment: .leading, spacing: 20) {
                // Header
                headerSection(listing)

                Divider()

                // Details grid
                detailsGrid(listing)

                // Cross streets
                if let crossStreets = listing.crossStreets {
                    infoRow(icon: "mappin.and.ellipse", label: "Cross Streets", value: crossStreets)
                }

                // Subway stations
                if let stations = listing.nearbyStations, !stations.isEmpty {
                    Divider()
                    subwaySection(stations)
                }

                // Price history
                if let history = listing.priceHistory, !history.isEmpty {
                    Divider()
                    priceHistorySection(history)
                }

                // Actions
                Divider()
                actionsSection(listing)
            }
            .padding(16)
        }
    }

    // MARK: - Sections

    private func headerSection(_ listing: Listing) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(listing.price)
                    .font(.system(.title, design: .default).bold())
                    .foregroundColor(.primaryText)

                Spacer()

                if let grade = listing.valueGrade {
                    ValueScoreBadge(grade: grade, score: listing.valueScore)
                }
            }

            Text(listing.address)
                .font(.sectionTitle)
                .foregroundColor(.primaryText)

            Text(listing.neighborhood)
                .font(.cardBody)
                .foregroundColor(.secondaryText)

            if let dom = listing.daysOnMarket {
                Text("Tracked for \(dom) day\(dom == 1 ? "" : "s")")
                    .font(.cardCaption)
                    .foregroundColor(dom >= 30 ? .priceDrop : .secondaryText)
            }
        }
    }

    private func detailsGrid(_ listing: Listing) -> some View {
        LazyVGrid(columns: [
            GridItem(.flexible()),
            GridItem(.flexible()),
            GridItem(.flexible()),
        ], spacing: 12) {
            detailCell(icon: "bed.double", label: "Beds", value: listing.beds)
            detailCell(icon: "shower", label: "Baths", value: listing.baths)
            detailCell(icon: "ruler", label: "Size", value: listing.sqft)
        }
    }

    private func detailCell(icon: String, label: String, value: String) -> some View {
        VStack(spacing: 4) {
            Image(systemName: icon)
                .font(.title3)
                .foregroundColor(.appAccent)
            Text(value)
                .font(.cardTitle)
                .foregroundColor(.primaryText)
            Text(label)
                .font(.cardCaption)
                .foregroundColor(.secondaryText)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .background(Color.appBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func infoRow(icon: String, label: String, value: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .foregroundColor(.appAccent)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                    .font(.cardCaption)
                    .foregroundColor(.secondaryText)
                Text(value)
                    .font(.cardBody)
                    .foregroundColor(.primaryText)
            }
        }
    }

    private func subwaySection(_ stations: [SubwayStation]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Nearby Subway")
                .font(.cardTitle)
                .foregroundColor(.primaryText)

            ForEach(stations) { station in
                HStack {
                    SubwayRoutesView(routes: station.routes)
                    VStack(alignment: .leading) {
                        Text(station.name)
                            .font(.cardBody)
                            .foregroundColor(.primaryText)
                        Text("\(String(format: "%.2f", station.distanceMi)) mi")
                            .font(.cardCaption)
                            .foregroundColor(.secondaryText)
                    }
                    Spacer()
                }
            }
        }
    }

    private func priceHistorySection(_ history: [PriceHistoryEntry]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Price History")
                .font(.cardTitle)
                .foregroundColor(.primaryText)

            ForEach(history) { entry in
                HStack {
                    Text("$\(entry.price.formatted())")
                        .font(.cardBody)
                        .foregroundColor(.primaryText)
                    Spacer()
                    Text(entry.date.prefix(10))
                        .font(.cardCaption)
                        .foregroundColor(.secondaryText)
                }
            }
        }
    }

    private func actionsSection(_ listing: Listing) -> some View {
        VStack(spacing: 12) {
            if let urlString = listing.googleMapsUrl, let url = URL(string: urlString) {
                Link(destination: url) {
                    Label("View on Google Maps", systemImage: "map")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }

            if let url = URL(string: listing.url) {
                Link(destination: url) {
                    Label(listing.source == "renthop" ? "View on RentHop" : "View on StreetEasy", systemImage: "safari")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(.appAccent)
            }
        }
    }

    // MARK: - Error

    private func errorView(_ message: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
                .font(.largeTitle)
                .foregroundColor(.secondaryText)
            Text(message)
                .font(.cardBody)
                .foregroundColor(.secondaryText)
            Button("Retry") {
                Task { await loadDetail() }
            }
            .buttonStyle(.borderedProminent)
            .tint(.appAccent)
        }
        .padding(.top, 100)
    }

    // MARK: - Load

    private func loadDetail() async {
        isLoading = true
        error = nil
        do {
            listing = try await APIClient.shared.getListingDetail(id: listingId)
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}

#if DEBUG
#Preview {
    NavigationStack {
        ListingDetailView(listingId: "preview")
            .environmentObject(AppState())
    }
}
#endif
