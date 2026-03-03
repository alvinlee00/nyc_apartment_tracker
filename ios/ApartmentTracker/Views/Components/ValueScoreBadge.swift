import SwiftUI

struct ValueScoreBadge: View {
    let grade: String
    let score: Double?

    var body: some View {
        HStack(spacing: 4) {
            Text(grade)
                .font(.badgeLabel)
                .foregroundColor(.white)
                .frame(width: 22, height: 22)
                .background(Color.forGrade(grade))
                .clipShape(RoundedRectangle(cornerRadius: 4))

            if let score {
                Text(String(format: "%.1f", score))
                    .font(.cardCaption)
                    .foregroundColor(.secondaryText)
            }
        }
    }
}

#if DEBUG
#Preview {
    HStack(spacing: 16) {
        ValueScoreBadge(grade: "A", score: 8.5)
        ValueScoreBadge(grade: "B", score: 6.2)
        ValueScoreBadge(grade: "C", score: 4.8)
        ValueScoreBadge(grade: "D", score: 2.1)
        ValueScoreBadge(grade: "F", score: 0.5)
    }
    .padding()
}
#endif
