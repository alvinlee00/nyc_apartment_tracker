import SwiftUI

struct PriceRangeView: View {
    @Binding var minPrice: Int
    @Binding var maxPrice: Int

    @State private var minText: String = ""
    @State private var maxText: String = ""

    var body: some View {
        Form {
            Section("Monthly Rent") {
                HStack {
                    Text("Min")
                        .foregroundColor(.secondaryText)
                    Spacer()
                    TextField("$0", text: $minText)
                        .keyboardType(.numberPad)
                        .multilineTextAlignment(.trailing)
                        .frame(width: 120)
                        .onChange(of: minText) { _, newValue in
                            let digits = newValue.filter(\.isNumber)
                            if let val = Int(digits) {
                                minPrice = val
                            } else if digits.isEmpty {
                                minPrice = 0
                            }
                        }
                }

                HStack {
                    Text("Max")
                        .foregroundColor(.secondaryText)
                    Spacer()
                    TextField("$5,000", text: $maxText)
                        .keyboardType(.numberPad)
                        .multilineTextAlignment(.trailing)
                        .frame(width: 120)
                        .onChange(of: maxText) { _, newValue in
                            let digits = newValue.filter(\.isNumber)
                            if let val = Int(digits) {
                                maxPrice = val
                            } else if digits.isEmpty {
                                maxPrice = 0
                            }
                        }
                }
            }

            Section {
                HStack {
                    Text("Quick Set")
                        .font(.cardCaption)
                        .foregroundColor(.secondaryText)
                    Spacer()
                }

                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                    quickButton("Up to $3,000", min: 0, max: 3000)
                    quickButton("Up to $4,000", min: 0, max: 4000)
                    quickButton("Up to $5,000", min: 0, max: 5000)
                    quickButton("$3k-$5k", min: 3000, max: 5000)
                }
            }
        }
        .navigationTitle("Price Range")
        .onAppear {
            minText = minPrice > 0 ? "\(minPrice)" : ""
            maxText = maxPrice > 0 ? "\(maxPrice)" : ""
        }
    }

    private func quickButton(_ label: String, min: Int, max: Int) -> some View {
        Button {
            minPrice = min
            maxPrice = max
            minText = min > 0 ? "\(min)" : ""
            maxText = max > 0 ? "\(max)" : ""
        } label: {
            Text(label)
                .font(.cardCaption)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(
                    (minPrice == min && maxPrice == max)
                        ? Color.appAccent.opacity(0.1)
                        : Color.appBackground
                )
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(
                            (minPrice == min && maxPrice == max)
                                ? Color.appAccent
                                : Color.secondaryText.opacity(0.2),
                            lineWidth: 1
                        )
                )
        }
        .buttonStyle(.plain)
    }
}

#if DEBUG
#Preview {
    NavigationStack {
        PriceRangeView(minPrice: .constant(0), maxPrice: .constant(5000))
    }
}
#endif
