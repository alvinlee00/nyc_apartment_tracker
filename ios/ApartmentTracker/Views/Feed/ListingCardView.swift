import SwiftUI

struct ListingCardView: View {
    let listing: Listing

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Image
            if let imageUrl = URL(string: listing.imageUrl), !listing.imageUrl.isEmpty {
                AsyncImage(url: imageUrl) { phase in
                    switch phase {
                    case .success(let image):
                        image
                            .resizable()
                            .aspectRatio(16/10, contentMode: .fill)
                            .clipped()
                    case .failure:
                        imagePlaceholder
                    default:
                        imagePlaceholder
                            .overlay(ProgressView())
                    }
                }
                .frame(height: 180)
                .clipShape(UnevenRoundedRectangle(topLeadingRadius: 12, topTrailingRadius: 12))
            }

            VStack(alignment: .leading, spacing: 8) {
                // Top row: price + value badge
                HStack(alignment: .firstTextBaseline) {
                    Text(listing.price)
                        .font(.priceLabel)
                        .foregroundColor(.primaryText)

                    Spacer()

                    if let grade = listing.valueGrade {
                        ValueScoreBadge(grade: grade, score: listing.valueScore)
                    }
                }

                // Address
                Text(listing.address)
                    .font(.cardTitle)
                    .foregroundColor(.primaryText)
                    .lineLimit(1)

                // Details row
                HStack(spacing: 12) {
                    detailItem(listing.beds, icon: "bed.double")
                    detailItem(listing.baths, icon: "shower")
                    if listing.sqft != "N/A" {
                        detailItem(listing.sqft, icon: "ruler")
                    }
                }

                // Bottom row: neighborhood + badges
                HStack {
                    Text(listing.neighborhood)
                        .font(.cardCaption)
                        .foregroundColor(.secondaryText)

                    Spacer()

                    if listing.isNew {
                        Text("NEW")
                            .font(.badgeLabel)
                            .foregroundColor(.white)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.newBadge)
                            .clipShape(Capsule())
                    }

                    if let dom = listing.daysOnMarket, dom >= 30 {
                        Text("\(dom)d")
                            .font(.badgeLabel)
                            .foregroundColor(.priceDrop)
                    }

                    sourceLabel
                }
            }
            .padding(12)
        }
        .background(Color.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .shadow(color: .black.opacity(0.06), radius: 8, x: 0, y: 2)
    }

    // MARK: - Subviews

    private var imagePlaceholder: some View {
        Rectangle()
            .fill(Color.gray.opacity(0.1))
            .frame(height: 180)
            .overlay(
                Image(systemName: "building.2")
                    .font(.largeTitle)
                    .foregroundColor(.gray.opacity(0.3))
            )
    }

    private func detailItem(_ text: String, icon: String) -> some View {
        HStack(spacing: 4) {
            Image(systemName: icon)
                .font(.caption2)
                .foregroundColor(.secondaryText)
            Text(text)
                .font(.cardBody)
                .foregroundColor(.secondaryText)
        }
    }

    private var sourceLabel: some View {
        Text(listing.source == "renthop" ? "RH" : "SE")
            .font(.system(size: 9, weight: .medium))
            .foregroundColor(.secondaryText)
            .padding(.horizontal, 4)
            .padding(.vertical, 1)
            .overlay(
                RoundedRectangle(cornerRadius: 3)
                    .stroke(Color.secondaryText.opacity(0.3), lineWidth: 0.5)
            )
    }
}

#if DEBUG
#Preview {
    ScrollView {
        VStack(spacing: 12) {
            ListingCardView(listing: .preview)
            ListingCardView(listing: .previewPriceDrop)
        }
        .padding(16)
    }
    .background(Color.appBackground)
}
#endif
