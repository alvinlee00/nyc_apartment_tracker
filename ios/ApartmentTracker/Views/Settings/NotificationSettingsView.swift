import SwiftUI

struct NotificationSettingsView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        Form {
            if let settings = appState.preferences?.notificationSettings {
                Section {
                    Toggle("New Listings", isOn: binding(for: \.newListings, settings: settings))
                    Toggle("Price Drops", isOn: binding(for: \.priceDrops, settings: settings))
                    Toggle("Daily Digest", isOn: binding(for: \.dailyDigest, settings: settings))
                } footer: {
                    Text("Choose which push notifications you'd like to receive.")
                }
            } else {
                Section {
                    Text("Loading...")
                        .foregroundColor(.secondaryText)
                }
            }
        }
        .navigationTitle("Notifications")
    }

    private func binding(for keyPath: WritableKeyPath<NotificationSettings, Bool>, settings: NotificationSettings) -> Binding<Bool> {
        Binding(
            get: { settings[keyPath: keyPath] },
            set: { newValue in
                var updated = settings
                updated[keyPath: keyPath] = newValue
                Task { await appState.updateNotifications(updated) }
            }
        )
    }
}

#if DEBUG
#Preview {
    NavigationStack {
        NotificationSettingsView()
            .environmentObject(AppState())
    }
}
#endif
