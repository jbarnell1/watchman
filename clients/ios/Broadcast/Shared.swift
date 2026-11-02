import Foundation
import WatchmanCore

/// The same shared container the app uses (Watchman/WatchmanApp.swift has the app's copy).
enum Shared {
    static let group = "group.app.watchman"

    static var directory: URL {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group)!
            .appendingPathComponent("watchman", isDirectory: true)
    }

    static let state = AppState(store: FileKeyValue(directory: directory))
    static var modelsDirectory: URL { directory.appendingPathComponent("models", isDirectory: true) }
}
