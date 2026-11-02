import Foundation

/// Key-value text storage behind a protocol: the app group's container on the phone, memory in tests.
public protocol KeyValue {
    func get(_ key: String) -> String?
    func put(_ key: String, _ value: String?)
}

public final class MemoryKeyValue: KeyValue {
    private var data: [String: String] = [:]
    public init() {}
    public func get(_ key: String) -> String? { data[key] }
    public func put(_ key: String, _ value: String?) { data[key] = value }
}

/// Files in a directory shared by the app and its extensions (an app group). Each value is one file.
public final class FileKeyValue: KeyValue {
    private let dir: URL
    public init(directory: URL) {
        dir = directory
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }
    public func get(_ key: String) -> String? {
        try? String(contentsOf: dir.appendingPathComponent(key + ".json"), encoding: .utf8)
    }
    public func put(_ key: String, _ value: String?) {
        let url = dir.appendingPathComponent(key + ".json")
        if let value { try? value.write(to: url, atomically: true, encoding: .utf8) } else { try? FileManager.default.removeItem(at: url) }
    }
}

/// Pairing, policy and the chosen model, as JSON in the store.
public final class AppState {
    public let store: KeyValue
    public let queue: EventQueue

    public init(store: KeyValue) {
        self.store = store
        self.queue = EventQueue(store: store)
    }

    private func read<T: Decodable>(_ key: String, _ type: T.Type) -> T? {
        guard let s = store.get(key), let d = s.data(using: .utf8) else { return nil }
        return try? Wire.decoder.decode(type, from: d)
    }

    private func write<T: Encodable>(_ key: String, _ value: T?) {
        guard let value, let d = try? Wire.encoder.encode(value) else { store.put(key, nil); return }
        store.put(key, String(data: d, encoding: .utf8))
    }

    public var pairing: Pairing? {
        get { read("pairing", Pairing.self) }
        set { write("pairing", newValue) }
    }

    public var policy: Policy? {
        get { read("policy", Policy.self) }
        set { write("policy", newValue) }
    }

    public var classifier: ModelEntry? {
        get { read("classifier", ModelEntry.self) }
        set { write("classifier", newValue) }
    }

    public func api(transport: HttpTransport = URLSessionTransport()) -> Api? {
        guard let p = pairing else { return nil }
        return Api(server: p.server, token: p.token, transport: transport)
    }
}

/// Events wait here until the server takes them. At most 200 per post, 5000 kept.
public final class EventQueue {
    public static let maxBatch = 200
    public static let maxQueue = 5000
    private let store: KeyValue
    private let key = "event_queue"
    private let lock = NSLock()

    public init(store: KeyValue) { self.store = store }

    public func all() -> [WatchmanEvent] {
        lock.lock(); defer { lock.unlock() }
        return unlocked()
    }

    private func unlocked() -> [WatchmanEvent] {
        guard let s = store.get(key), let d = s.data(using: .utf8) else { return [] }
        return (try? Wire.decoder.decode([WatchmanEvent].self, from: d)) ?? []
    }

    private func save(_ q: [WatchmanEvent]) {
        if let d = try? Wire.encoder.encode(q) { store.put(key, String(data: d, encoding: .utf8)) }
    }

    public func push(_ e: WatchmanEvent) {
        lock.lock(); defer { lock.unlock() }
        var q = unlocked()
        q.append(e)
        if q.count > Self.maxQueue { q.removeFirst(q.count - Self.maxQueue) }
        save(q)
    }

    public var count: Int { all().count }

    /// Sends one batch; keeps what the server did not take. Returns ids the server wants images for.
    public func flush(_ send: ([WatchmanEvent]) async throws -> EventsResponse) async throws -> [String] {
        let batch = Array(all().prefix(Self.maxBatch))
        if batch.isEmpty { return [] }
        let r = try await send(batch)
        let done = Set(r.accepted + r.duplicates)
        lock.lock()
        save(unlocked().filter { !done.contains($0.id) })
        lock.unlock()
        return r.upload
    }
}
