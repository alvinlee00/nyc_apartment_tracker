import SwiftUI

struct BedTypePickerView: View {
    @Binding var selected: Set<String>

    private let bedTypes = ["Studio", "1", "2", "3", "4+"]

    var body: some View {
        Form {
            Section("Select Bedroom Types") {
                Text("Leave empty for all types")
                    .font(.cardCaption)
                    .foregroundColor(.secondaryText)

                FlowLayout(spacing: 8) {
                    ForEach(bedTypes, id: \.self) { bed in
                        chipButton(bed)
                    }
                }
                .padding(.vertical, 8)
            }
        }
        .navigationTitle("Bedrooms")
    }

    private func chipButton(_ bed: String) -> some View {
        let isSelected = selected.contains(bed)
        return Button {
            if isSelected {
                selected.remove(bed)
            } else {
                selected.insert(bed)
            }
        } label: {
            Text(bed == "Studio" ? "Studio" : "\(bed) BR")
                .font(.cardBody)
                .foregroundColor(isSelected ? .white : .primaryText)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(isSelected ? Color.appAccent : Color.appBackground)
                .clipShape(Capsule())
                .overlay(
                    Capsule()
                        .stroke(isSelected ? Color.appAccent : Color.secondaryText.opacity(0.3), lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
    }
}

/// Simple horizontal flow layout for chips.
struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let result = arrangeSubviews(proposal: proposal, subviews: subviews)
        return result.size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let result = arrangeSubviews(proposal: proposal, subviews: subviews)
        for (index, position) in result.positions.enumerated() {
            subviews[index].place(at: CGPoint(x: bounds.minX + position.x, y: bounds.minY + position.y), proposal: .unspecified)
        }
    }

    private func arrangeSubviews(proposal: ProposedViewSize, subviews: Subviews) -> (size: CGSize, positions: [CGPoint]) {
        let maxWidth = proposal.width ?? .infinity
        var positions: [CGPoint] = []
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var maxX: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x + size.width > maxWidth && x > 0 {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            positions.append(CGPoint(x: x, y: y))
            rowHeight = max(rowHeight, size.height)
            x += size.width + spacing
            maxX = max(maxX, x)
        }

        return (CGSize(width: maxX, height: y + rowHeight), positions)
    }
}

#if DEBUG
#Preview {
    NavigationStack {
        BedTypePickerView(selected: .constant(Set(["Studio", "1"])))
    }
}
#endif
