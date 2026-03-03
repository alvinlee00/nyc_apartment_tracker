import SwiftUI

extension Color {
    // MARK: - Background
    static let appBackground = Color(red: 250/255, green: 249/255, blue: 246/255)   // #FAF9F6
    static let cardBackground = Color.white

    // MARK: - Text
    static let primaryText = Color(red: 26/255, green: 26/255, blue: 26/255)        // #1A1A1A
    static let secondaryText = Color(red: 107/255, green: 114/255, blue: 128/255)   // #6B7280

    // MARK: - Accent
    static let appAccent = Color(red: 26/255, green: 115/255, blue: 232/255)        // #1A73E8

    // MARK: - Value Scores
    static let valueA = Color(red: 22/255, green: 163/255, blue: 74/255)            // #16A34A
    static let valueB = Color(red: 34/255, green: 197/255, blue: 94/255)            // #22C55E
    static let valueC = Color(red: 234/255, green: 179/255, blue: 8/255)            // #EAB308
    static let valueD = Color(red: 249/255, green: 115/255, blue: 22/255)           // #F97316
    static let valueF = Color(red: 239/255, green: 68/255, blue: 68/255)            // #EF4444

    // MARK: - Status
    static let priceDrop = Color(red: 234/255, green: 88/255, blue: 12/255)         // #EA580C
    static let newBadge = Color(red: 59/255, green: 130/255, blue: 246/255)         // #3B82F6

    static func forGrade(_ grade: String) -> Color {
        switch grade.uppercased() {
        case "A": return .valueA
        case "B": return .valueB
        case "C": return .valueC
        case "D": return .valueD
        default: return .valueF
        }
    }
}
