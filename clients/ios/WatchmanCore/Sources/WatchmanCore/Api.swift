import Foundation

public struct ApiError: Error, Equatable {
    public let status: Int
    public let code: String
    public let message: String
}

/// Something that performs HTTP, so tests can answer without a network.
public protocol HttpTransport {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

public struct URLSessionTransport: HttpTransport {
    public init() {}
    public func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await URLSession.shared.data(for: request)
        return (data, response as! HTTPURLResponse)
    }
}

/// The device API (docs/api-contract.md).
public final class Api {
    private let server: String
    private let token: String?
    private let transport: HttpTransport

    public init(server: String, token: String?, transport: HttpTransport = URLSessionTransport()) {
        self.server = server.hasSuffix("/") ? String(server.dropLast()) : server
        self.token = token
        self.transport = transport
    }

    private func request(_ method: String, _ path: String, json: Data? = nil, jpeg: Data? = nil) async throws -> Data {
        var req = URLRequest(url: URL(string: server + path)!)
        req.httpMethod = method
        req.setValue("watchman-ios/0.1.0", forHTTPHeaderField: "User-Agent")
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let json {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = json
        } else if let jpeg {
            req.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
            req.httpBody = jpeg
        }
        let (data, res) = try await transport.send(req)
        guard (200..<300).contains(res.statusCode) else {
            var code = "error"
            var message = HTTPURLResponse.localizedString(forStatusCode: res.statusCode)
            if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let err = obj["error"] as? [String: Any] {
                code = err["code"] as? String ?? code
                message = err["message"] as? String ?? message
            }
            throw ApiError(status: res.statusCode, code: code, message: message)
        }
        return data
    }

    public static func pair(server: String, code: String, deviceName: String, transport: HttpTransport = URLSessionTransport()) async throws -> Pairing {
        let api = Api(server: server, token: nil, transport: transport)
        let body: [String: Any] = ["code": code, "device": ["name": deviceName, "platform": "ios", "kind": "app", "app_version": "0.1.0"]]
        let data = try await api.request("POST", "/api/v1/pair", json: try JSONSerialization.data(withJSONObject: body))
        let r = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        return Pairing(server: api.server, token: r["token"] as! String, deviceId: r["device_id"] as! String,
                       personId: r["person_id"] as! String, serverName: r["server_name"] as! String)
    }

    public func heartbeat(_ body: [String: Any]) async throws -> HeartbeatResponse {
        let data = try await request("POST", "/api/v1/heartbeat", json: try JSONSerialization.data(withJSONObject: body))
        return try Wire.decoder.decode(HeartbeatResponse.self, from: data)
    }

    public func policy() async throws -> Policy {
        try Wire.decoder.decode(Policy.self, from: try await request("GET", "/api/v1/policy"))
    }

    public func events(_ events: [WatchmanEvent]) async throws -> EventsResponse {
        let data = try await request("POST", "/api/v1/events", json: try Wire.encoder.encode(["events": events]))
        return try Wire.decoder.decode(EventsResponse.self, from: data)
    }

    public func uploadImage(eventId: String, jpeg: Data) async throws {
        _ = try await request("PUT", "/api/v1/events/\(eventId)/image", jpeg: jpeg)
    }

    public func manifest() async throws -> ModelsManifest {
        try Wire.decoder.decode(ModelsManifest.self, from: try await request("GET", "/api/v1/models/manifest"))
    }

    public func modelBytes(path: String) async throws -> Data {
        try await request("GET", path)
    }

    public func unlock(code: String) async throws -> String {
        let data = try await request("POST", "/api/v1/unlock", json: try JSONSerialization.data(withJSONObject: ["code": code]))
        let r = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        return r["paused_until"] as! String
    }

    public func requestUnlock(minutes: Int, reason: String) async throws {
        _ = try await request("POST", "/api/v1/unlock/request", json: try JSONSerialization.data(withJSONObject: ["minutes": minutes, "reason": reason]))
    }
}
