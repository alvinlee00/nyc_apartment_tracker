import SwiftUI

struct NeighborhoodPickerView: View {
    @EnvironmentObject private var appState: AppState
    @Binding var selected: Set<String>
    @State private var searchText = ""

    var body: some View {
        List {
            ForEach(filteredBoroughs, id: \.key) { borough, neighborhoods in
                Section(borough) {
                    ForEach(neighborhoods) { hood in
                        Button {
                            toggle(hood.slug)
                        } label: {
                            HStack {
                                Text(hood.name)
                                    .foregroundColor(.primaryText)
                                Spacer()
                                if selected.contains(hood.slug) {
                                    Image(systemName: "checkmark")
                                        .foregroundColor(.appAccent)
                                }
                            }
                        }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .searchable(text: $searchText, prompt: "Search neighborhoods")
        .navigationTitle("Neighborhoods")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button(selected.isEmpty ? "Select All" : "Clear") {
                    if selected.isEmpty {
                        selected = Set(appState.neighborhoods.map(\.slug))
                    } else {
                        selected.removeAll()
                    }
                }
            }
        }
    }

    private var filteredBoroughs: [(key: String, value: [Neighborhood])] {
        let allHoods = appState.neighborhoods
        let filtered = searchText.isEmpty
            ? allHoods
            : allHoods.filter { $0.name.localizedCaseInsensitiveContains(searchText) }

        let grouped = Dictionary(grouping: filtered, by: \.borough)
        let order = ["Manhattan", "Brooklyn", "Queens", "Upper Manhattan", "Other"]
        return order.compactMap { borough in
            guard let hoods = grouped[borough], !hoods.isEmpty else { return nil }
            return (key: borough, value: hoods.sorted { $0.name < $1.name })
        }
    }

    private func toggle(_ slug: String) {
        if selected.contains(slug) {
            selected.remove(slug)
        } else {
            selected.insert(slug)
        }
    }
}

#if DEBUG
#Preview {
    NavigationStack {
        NeighborhoodPickerView(selected: .constant(Set(["east-village", "chelsea"])))
            .environmentObject(AppState())
    }
}
#endif
