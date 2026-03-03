import SwiftUI

struct GeoFilterView: View {
    @EnvironmentObject private var appState: AppState

    @State private var isEnabled = false
    @State private var westAvenue: String = ""
    @State private var eastAvenue: String = ""

    var body: some View {
        Form {
            Section {
                Toggle("Enable Geographic Filter", isOn: $isEnabled)
                    .onChange(of: isEnabled) { _, newValue in
                        if !newValue {
                            Task { await clearGeoBounds() }
                        }
                    }
            } footer: {
                Text("Filter listings to apartments between two Manhattan avenues (west-to-east).")
            }

            if isEnabled {
                Section("West Boundary") {
                    Picker("Western Avenue", selection: $westAvenue) {
                        Text("Select...").tag("")
                        ForEach(sortedAvenues, id: \.name) { avenue in
                            Text(avenue.name).tag(avenue.name)
                        }
                    }
                }

                Section("East Boundary") {
                    Picker("Eastern Avenue", selection: $eastAvenue) {
                        Text("Select...").tag("")
                        ForEach(sortedAvenues, id: \.name) { avenue in
                            Text(avenue.name).tag(avenue.name)
                        }
                    }
                }

                if !westAvenue.isEmpty && !eastAvenue.isEmpty {
                    Section {
                        Button("Apply Filter") {
                            Task { await applyGeoBounds() }
                        }
                        .frame(maxWidth: .infinity)
                        .foregroundColor(.appAccent)
                    }
                }
            }
        }
        .navigationTitle("Geo Filter")
        .onAppear { loadCurrent() }
    }

    /// Avenues sorted west-to-east (most negative longitude first)
    private var sortedAvenues: [Avenue] {
        appState.avenues.sorted { $0.longitude < $1.longitude }
    }

    private func loadCurrent() {
        guard let bounds = appState.preferences?.filters.geoBounds else {
            isEnabled = false
            return
        }
        isEnabled = true
        westAvenue = appState.avenues.first { $0.longitude == bounds.westLongitude }?.name ?? ""
        eastAvenue = appState.avenues.first { $0.longitude == bounds.eastLongitude }?.name ?? ""
    }

    private func applyGeoBounds() async {
        guard var filters = appState.preferences?.filters else { return }
        let westLon = appState.avenues.first { $0.name == westAvenue }?.longitude
        let eastLon = appState.avenues.first { $0.name == eastAvenue }?.longitude
        guard let w = westLon, let e = eastLon else { return }

        filters.geoBounds = GeoBounds(westLongitude: min(w, e), eastLongitude: max(w, e), applyTo: [])
        await appState.updateFilters(filters)
    }

    private func clearGeoBounds() async {
        guard var filters = appState.preferences?.filters else { return }
        filters.geoBounds = nil
        await appState.updateFilters(filters)
    }
}

#if DEBUG
#Preview {
    NavigationStack {
        GeoFilterView()
            .environmentObject(AppState())
    }
}
#endif
