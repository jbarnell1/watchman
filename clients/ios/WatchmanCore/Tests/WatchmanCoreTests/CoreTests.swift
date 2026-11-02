import XCTest
@testable import WatchmanCore

final class CoreTests: XCTestCase {
    struct RawVector: Decodable { let file: String; let width: Int; let height: Int; let pixels: [UInt32]; let dhash: String }
    struct RawFile: Decodable { let vectors: [RawVector] }

    func testDHashReproducesEveryCommittedVector() throws {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "raw", withExtension: "json", subdirectory: "Vectors"))
        let raw = try JSONDecoder().decode(RawFile.self, from: Data(contentsOf: url))
        XCTAssertGreaterThanOrEqual(raw.vectors.count, 8)
        for v in raw.vectors {
            XCTAssertEqual(DHash.compute(packedRGB: v.pixels, width: v.width, height: v.height), v.dhash, v.file)
        }
    }

    func testHammingAndTolerance() {
        XCTAssertEqual(DHash.hamming("0000000000000000", "000000000000000f"), 4)
        XCTAssertTrue(DHash.matches("0000000000000000", "000000000000000f"))
        XCTAssertFalse(DHash.matches("0000000000000000", "00000000000000ff"))
    }

    func testDecisions() {
        let t = Thresholds(cover: 0.8, flag: 0.6, detectorCover: 0.55)
        XCTAssertEqual(Decide.fromScore(0.1, t), .safe)
        XCTAssertEqual(Decide.fromScore(0.6, t), .flag)
        XCTAssertEqual(Decide.fromScore(0.8, t), .cover)
        XCTAssertTrue(Decide.isTiny(16, 100))
        XCTAssertTrue(Decide.isKnownBad("9f3a0c1e5b7d2a47", ["9f3a0c1e5b7d2a46"]))
    }

    func testPolicyDecodesSnakeCase() throws {
        let json = """
        {"version":3,"thresholds":{"cover":0.8,"flag":0.6,"detector_cover":0.55},"strict_mode":true,"risky_apps":[],
         "blocked_apps":["com.google.ios.youtube"],"protected_browser":"safari","sample_interval_s":180,
         "whole_screen_interval_s":5,"paused_until":null,"known_bad_hashes":["9f3a0c1e5b7d2a46"]}
        """.data(using: .utf8)!
        let p = try Wire.decoder.decode(Policy.self, from: json)
        XCTAssertEqual(p.thresholds.detectorCover, 0.55)
        XCTAssertEqual(p.blockedApps, ["com.google.ios.youtube"])
        XCTAssertFalse(p.isPaused())
        let e = WatchmanEvent(type: "image_covered", hasImage: true, detail: ["x": "1"])
        let out = String(data: try Wire.encoder.encode(e), encoding: .utf8)!
        XCTAssertTrue(out.contains("\"has_image\":true"))
        XCTAssertTrue(out.contains("\"occurred_at\""))
    }

    func testQueueKeepsWhatTheServerDidNotTake() async throws {
        let q = EventQueue(store: MemoryKeyValue())
        for id in ["a", "b", "c"] { q.push(WatchmanEvent(id: id, type: "image_covered")) }
        let wanted = try await q.flush { batch in
            XCTAssertEqual(batch.map(\.id), ["a", "b", "c"])
            return EventsResponse(accepted: ["a"], duplicates: ["b"], upload: ["a"])
        }
        XCTAssertEqual(wanted, ["a"])
        XCTAssertEqual(q.all().map(\.id), ["c"])
    }

    struct FakeTransport: HttpTransport {
        let answer: (URLRequest) -> (Int, String)
        func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
            let (status, body) = answer(request)
            let res = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!
            return (body.data(using: .utf8)!, res)
        }
    }

    func testPairThenPolicyCarriesTheToken() async throws {
        var seen: [URLRequest] = []
        let transport = FakeTransport { req in
            seen.append(req)
            switch req.url!.path {
            case "/api/v1/pair":
                return (200, #"{"device_id":"d","token":"tok","person_id":"p","server_name":"Example household","api_version":1}"#)
            case "/api/v1/policy":
                return (200, #"{"version":3,"thresholds":{"cover":0.8,"flag":0.6,"detector_cover":0.55},"strict_mode":true,"risky_apps":[],"blocked_apps":[],"protected_browser":"safari","sample_interval_s":180,"whole_screen_interval_s":5,"paused_until":null,"known_bad_hashes":[]}"#)
            default:
                return (404, #"{"error":{"code":"not_found","message":"no"}}"#)
            }
        }
        let pairing = try await Api.pair(server: "https://watchman.test/", code: "K7Q-4MZ-P2D", deviceName: "iPhone", transport: transport)
        XCTAssertEqual(pairing.token, "tok")
        let api = Api(server: pairing.server, token: pairing.token, transport: transport)
        let policy = try await api.policy()
        XCTAssertEqual(policy.version, 3)
        XCTAssertEqual(seen[1].value(forHTTPHeaderField: "Authorization"), "Bearer tok")
        do {
            _ = try await api.unlock(code: "000-000")
            XCTFail("expected an error")
        } catch let e as ApiError {
            XCTAssertEqual(e.code, "not_found")
        }
    }
}
