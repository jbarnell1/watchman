import Foundation

// Shapes from docs/api-contract.md and models/manifest.json.

public struct Thresholds: Codable, Equatable {
    public var cover: Double
    public var flag: Double
    public var detectorCover: Double

    public init(cover: Double, flag: Double, detectorCover: Double) {
        self.cover = cover
        self.flag = flag
        self.detectorCover = detectorCover
    }
}

public struct Policy: Codable, Equatable {
    public var version: Int
    public var thresholds: Thresholds
    public var strictMode: Bool
    public var coverage: String?
    public var explicitLabels: [String]?
    /// Posture (ADR-022): report only when false; no ask band when askServer is false. Absent means true.
    public var coverOnDevice: Bool?
    public var askServer: Bool?
    public var riskyApps: [String]
    public var blockedApps: [String]
    public var protectedBrowser: String
    public var sampleIntervalS: Int
    public var wholeScreenIntervalS: Int
    public var pausedUntil: String?
    public var knownBadHashes: [String]

    public func isPaused(now: Date = Date()) -> Bool {
        guard let p = pausedUntil, let until = Iso.parse(p) else { return false }
        return until > now
    }
}

public struct Pairing: Codable, Equatable {
    public var server: String
    public var token: String
    public var deviceId: String
    public var personId: String
    public var serverName: String
}

public struct ModelInput: Codable {
    public var name: String
    public var size: Int
    public var layout: String
    public var color: String
    public var scale: Double
    public var mean: [Double]
    public var std: [Double]
    public var resize: String
}

public struct ModelOutput: Codable {
    public var name: String
    public var kind: String
    public var labels: [String]
    public var positive: String?
    public var explicitLabels: [String]?
}

public struct ModelEntry: Codable {
    public var role: String
    public var file: String
    public var url: String
    public var sha256: String
    public var bytes: Int
    public var input: ModelInput
    public var output: ModelOutput
}

public struct ModelsManifest: Codable {
    public var version: Int
    public var models: [ModelEntry]
}

public struct Region: Codable, Equatable {
    public var w: Int
    public var h: Int
    public var screenW: Int
    public var screenH: Int
    public init(w: Int, h: Int, screenW: Int, screenH: Int) {
        self.w = w; self.h = h; self.screenW = screenW; self.screenH = screenH
    }
}

public struct WatchmanEvent: Codable, Equatable {
    public var id: String
    public var type: String
    public var occurredAt: String
    public var app: String?
    public var url: String?
    public var title: String?
    public var action: String
    public var scores: [String: Double]?
    public var region: Region?
    public var hash: String?
    public var hasImage: Bool
    public var detail: [String: String]

    public init(id: String = UUID().uuidString.lowercased(), type: String, occurredAt: String = Iso.now(), app: String? = nil,
                url: String? = nil, title: String? = nil, action: String = "none", scores: [String: Double]? = nil,
                region: Region? = nil, hash: String? = nil, hasImage: Bool = false, detail: [String: String] = [:]) {
        self.id = id; self.type = type; self.occurredAt = occurredAt; self.app = app; self.url = url; self.title = title
        self.action = action; self.scores = scores; self.region = region; self.hash = hash; self.hasImage = hasImage; self.detail = detail
    }
}

public struct EventsResponse: Codable {
    public var accepted: [String]
    public var duplicates: [String]
    public var upload: [String]
    public init(accepted: [String], duplicates: [String], upload: [String]) {
        self.accepted = accepted; self.duplicates = duplicates; self.upload = upload
    }
}

public struct HeartbeatResponse: Codable {
    public var policyVersion: Int
    public var pausedUntil: String?
}

public enum Decision: String, Codable { case safe, flag, cover }

public enum Decide {
    public static let minSide = 20

    public static func fromScore(_ score: Double, _ t: Thresholds) -> Decision {
        if score >= t.cover { return .cover }
        if score >= t.flag { return .flag }
        return .safe
    }

    public static func isTiny(_ w: Int, _ h: Int) -> Bool { w < minSide || h < minSide }

    public static func isKnownBad(_ hash: String, _ knownBad: [String]) -> Bool {
        knownBad.contains { DHash.matches($0, hash) }
    }
}

public enum Iso {
    private static let formatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        return f
    }()

    public static func now(_ date: Date = Date()) -> String { formatter.string(from: date) }

    public static func parse(_ s: String) -> Date? {
        let trimmed = s.replacingOccurrences(of: #"\.\d+"#, with: "", options: .regularExpression)
        return formatter.date(from: trimmed) ?? ISO8601DateFormatter().date(from: s)
    }
}

/// Our JSON is snake_case on the wire and camelCase in Swift.
public enum Wire {
    public static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    public static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()
}
