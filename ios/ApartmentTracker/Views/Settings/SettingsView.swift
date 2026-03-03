import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        NavigationStack {
            ZStack {
                Color.appBackground.ignoresSafeArea()

                if appState.isLoadingPreferences && appState.preferences == nil {
                    ProgressView()
                } else if let prefs = appState.preferences {
                    settingsList(prefs)
                } else {
                    notRegisteredView
                }
            }
            .navigationTitle("Settings")
        }
    }

    private func settingsList(_ prefs: DevicePreferences) -> some View {
        List {
            // MARK: - Filters
            Section("Filters") {
                NavigationLink {
                    NeighborhoodPickerView(
                        selected: Binding(
                            get: { Set(prefs.filters.neighborhoods) },
                            set: { newValue in
                                var filters = prefs.filters
                                filters.neighborhoods = Array(newValue).sorted()
                                Task { await appState.updateFilters(filters) }
                            }
                        )
                    )
                } label: {
                    HStack {
                        Label("Neighborhoods", systemImage: "mappin.circle")
                        Spacer()
                        Text(neighborhoodSummary(prefs.filters.neighborhoods))
                            .font(.cardCaption)
                            .foregroundColor(.secondaryText)
                    }
                }

                NavigationLink {
                    PriceRangeView(
                        minPrice: Binding(
                            get: { prefs.filters.minPrice },
                            set: { newValue in
                                var filters = prefs.filters
                                filters.minPrice = newValue
                                Task { await appState.updateFilters(filters) }
                            }
                        ),
                        maxPrice: Binding(
                            get: { prefs.filters.maxPrice },
                            set: { newValue in
                                var filters = prefs.filters
                                filters.maxPrice = newValue
                                Task { await appState.updateFilters(filters) }
                            }
                        )
                    )
                } label: {
                    HStack {
                        Label("Price Range", systemImage: "dollarsign.circle")
                        Spacer()
                        Text(priceSummary(prefs.filters))
                            .font(.cardCaption)
                            .foregroundColor(.secondaryText)
                    }
                }

                NavigationLink {
                    BedTypePickerView(
                        selected: Binding(
                            get: { Set(prefs.filters.bedRooms) },
                            set: { newValue in
                                var filters = prefs.filters
                                filters.bedRooms = Array(newValue).sorted()
                                Task { await appState.updateFilters(filters) }
                            }
                        )
                    )
                } label: {
                    HStack {
                        Label("Bedrooms", systemImage: "bed.double")
                        Spacer()
                        Text(bedSummary(prefs.filters.bedRooms))
                            .font(.cardCaption)
                            .foregroundColor(.secondaryText)
                    }
                }

                NavigationLink {
                    GeoFilterView()
                } label: {
                    HStack {
                        Label("Geographic Filter", systemImage: "square.dashed")
                        Spacer()
                        Text(prefs.filters.geoBounds != nil ? "Active" : "Off")
                            .font(.cardCaption)
                            .foregroundColor(.secondaryText)
                    }
                }
            }

            // MARK: - Notifications
            Section("Notifications") {
                NavigationLink {
                    NotificationSettingsView()
                } label: {
                    HStack {
                        Label("Push Notifications", systemImage: "bell")
                        Spacer()
                        Text(notifSummary(prefs.notificationSettings))
                            .font(.cardCaption)
                            .foregroundColor(.secondaryText)
                    }
                }
            }

            // MARK: - About
            Section("About") {
                HStack {
                    Text("Device ID")
                    Spacer()
                    Text(DeviceManager.shared.deviceId.prefix(8) + "...")
                        .font(.cardCaption)
                        .foregroundColor(.secondaryText)
                }
            }
        }
        .listStyle(.insetGrouped)
    }

    private var notRegisteredView: some View {
        VStack(spacing: 16) {
            Image(systemName: "gearshape")
                .font(.system(size: 48))
                .foregroundColor(.secondaryText.opacity(0.5))
            Text("Not connected")
                .font(.sectionTitle)
            Text("The app will connect to the server once push notifications are enabled.")
                .font(.cardBody)
                .foregroundColor(.secondaryText)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
        }
    }

    // MARK: - Summary Helpers

    private func neighborhoodSummary(_ hoods: [String]) -> String {
        if hoods.isEmpty { return "All" }
        if hoods.count <= 2 {
            let names = hoods.compactMap { slug in
                appState.neighborhoods.first { $0.slug == slug }?.name ?? slug
            }
            return names.joined(separator: ", ")
        }
        return "\(hoods.count) selected"
    }

    private func priceSummary(_ filters: Filters) -> String {
        let min = filters.minPrice
        let max = filters.maxPrice
        if min > 0 && max > 0 { return "$\(min.formatted()) - $\(max.formatted())" }
        if max > 0 { return "Up to $\(max.formatted())" }
        if min > 0 { return "$\(min.formatted())+" }
        return "Any"
    }

    private func bedSummary(_ beds: [String]) -> String {
        if beds.isEmpty { return "Any" }
        return beds.joined(separator: ", ")
    }

    private func notifSummary(_ settings: NotificationSettings) -> String {
        let count = [settings.newListings, settings.priceDrops, settings.dailyDigest].filter { $0 }.count
        return "\(count)/3 enabled"
    }
}

#if DEBUG
#Preview {
    SettingsView()
        .environmentObject(AppState())
}
#endif
