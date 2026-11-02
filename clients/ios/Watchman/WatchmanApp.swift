import SwiftUI
import WatchmanCore

/// Where the app and its extensions keep shared state: the app group container.
enum Shared {
    static let group = "group.app.watchman"

    static var directory: URL {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group)?
            .appendingPathComponent("watchman", isDirectory: true)
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("watchman")
    }

    static let state = AppState(store: FileKeyValue(directory: directory))
    static var modelsDirectory: URL { directory.appendingPathComponent("models", isDirectory: true) }
}

@main
struct WatchmanApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .task { await model.refresh() }
                .onReceive(NotificationCenter.default.publisher(for: UIApplication.willEnterForegroundNotification)) { _ in
                    Task { await model.refresh() }
                }
        }
    }
}

struct RootView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        NavigationStack {
            if model.pairing == nil { PairView() } else { StatusView() }
        }
        .tint(Color(red: 0.08, green: 0.43, blue: 0.48))
    }
}
