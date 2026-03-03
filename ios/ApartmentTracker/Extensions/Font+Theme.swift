import SwiftUI

extension Font {
    // MARK: - Screen Titles (serif)
    static let screenTitle = Font.system(.largeTitle, design: .serif).weight(.bold)
    static let sectionTitle = Font.system(.title3, design: .serif).weight(.semibold)

    // MARK: - Body (sans-serif)
    static let cardTitle = Font.system(.headline, design: .default).weight(.semibold)
    static let cardBody = Font.system(.subheadline, design: .default)
    static let cardCaption = Font.system(.caption, design: .default)
    static let priceLabel = Font.system(.title2, design: .default).weight(.bold)
    static let badgeLabel = Font.system(.caption2, design: .default).weight(.bold)
}
