import CoreImage
import ReplayKit
import WatchmanCore

/// The ReplayKit broadcast extension: the whole-screen accountability report on iPhone (ADR-012).
/// Every whole-screen interval it takes the latest frame, scores it whole and in six tiles with the
/// crop classifier, records flagged tiles (with a 512 px crop) and heartbeats as "capture granted".
/// It cannot cover anything; Safari is covered by the web extension. Stopping is reported.
final class SampleHandler: RPBroadcastSampleHandler {
    private let state = Shared.state
    private var scorer: OnnxScorer?
    private var lastPass: Date = .distantPast
    private var lastHeartbeat: Date = .distantPast
    private var lastFlush: Date = .distantPast
    private var recentHashes: [String: Date] = [:]
    private let context = CIContext(options: [.useSoftwareRenderer: false])
    private var busy = false

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        state.store.put("broadcasting", "1")
        if let entry = state.classifier {
            let path = Shared.modelsDirectory.appendingPathComponent(entry.file).path
            scorer = try? OnnxScorer(entry: entry, modelPath: path)
        }
        state.queue.push(WatchmanEvent(type: "protection_changed", app: "ios", detail: ["field": "capture", "from": "denied", "to": "granted"]))
        Task { await heartbeat(force: true) }
    }

    override func broadcastPaused() { }
    override func broadcastResumed() { }

    override func broadcastFinished() {
        state.store.put("broadcasting", "0")
        state.queue.push(WatchmanEvent(type: "protection_changed", app: "ios", detail: ["field": "capture", "from": "granted", "to": "denied"]))
        let group = DispatchGroup()
        group.enter()
        Task { await heartbeat(force: true); group.leave() }
        _ = group.wait(timeout: .now() + 5)
    }

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
        guard sampleBufferType == .video, !busy else { return }
        let now = Date()
        let interval = TimeInterval(state.policy?.wholeScreenIntervalS ?? 5)
        guard now.timeIntervalSince(lastPass) >= interval, let pixels = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        lastPass = now
        busy = true
        defer { busy = false }
        if let policy = state.policy, policy.isPaused(now: now) { return }
        examine(pixels)
        Task { await heartbeat(force: false) }
    }

    private func examine(_ pixels: CVPixelBuffer) {
        guard let scorer, let policy = state.policy else { return }
        let image = CIImage(cvPixelBuffer: pixels)
        let w = Int(image.extent.width)
        let h = Int(image.extent.height)
        var regions: [CGRect] = [CGRect(x: 0, y: 0, width: w, height: h)]
        if policy.strictMode {
            for row in 0..<3 { for col in 0..<2 {
                regions.append(CGRect(x: col * w / 2, y: row * h / 3, width: w / 2, height: h / 3))
            } }
        }
        let t = policy.thresholds
        for region in regions {
            guard let cg = context.createCGImage(image.cropped(to: region), from: region) else { continue }
            let hash = DHash.compute(rgba: Pixels.rgba(cg, width: 128, height: 128), width: 128, height: 128)
            let knownBad = Decide.isKnownBad(hash, policy.knownBadHashes)
            if let seen = recentHashes[hash], Date().timeIntervalSince(seen) < 600 { continue }
            let square = Pixels.rgba(cg, width: scorer.size, height: scorer.size)
            let score = knownBad ? 1.0 : (scorer.score(square) ?? 0)
            let decision = Decide.fromScore(score, t)
            if decision == .safe { continue }
            recentHashes[hash] = Date()
            let jpeg = Pixels.jpeg(cg, maxSide: 512)
            let event = WatchmanEvent(type: "image_flagged", app: "ios", action: "flagged",
                                      scores: ["classifier": (score * 1000).rounded() / 1000],
                                      region: Region(w: Int(region.width), h: Int(region.height), screenW: w, screenH: h),
                                      hash: hash, hasImage: jpeg != nil,
                                      detail: ["platform": "ios", "covered": "false", "level": decision.rawValue])
            if let jpeg {
                let dir = Shared.directory.appendingPathComponent("pending")
                try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                try? jpeg.write(to: dir.appendingPathComponent(event.id + ".jpg"))
            }
            state.queue.push(event)
        }
        if recentHashes.count > 500 { recentHashes = recentHashes.filter { Date().timeIntervalSince($0.value) < 600 } }
    }

    private func heartbeat(force: Bool) async {
        let now = Date()
        guard force || now.timeIntervalSince(lastHeartbeat) >= 300 else {
            if now.timeIntervalSince(lastFlush) >= 60 { await flush() }
            return
        }
        lastHeartbeat = now
        guard let api = state.api() else { return }
        do {
            let r = try await api.heartbeat([
                "sent_at": Iso.now(), "app_version": "0.1.0", "booted_at": NSNull(), "safe_mode": false, "battery_pct": NSNull(),
                "queued_events": state.queue.count,
                "state": ["protection": true, "accessibility": NSNull(), "capture": "granted", "overlay": NSNull(),
                          "device_admin": NSNull(), "extension": NSNull(), "vpn": NSNull()],
            ])
            state.store.put("last_heartbeat", Iso.now())
            if state.policy?.version != r.policyVersion || state.policy?.pausedUntil != r.pausedUntil {
                state.policy = try await api.policy()
            }
        } catch { }
        await flush()
    }

    private func flush() async {
        lastFlush = Date()
        guard let api = state.api() else { return }
        guard let wanted = try? await state.queue.flush({ try await api.events($0) }) else { return }
        for id in wanted {
            let url = Shared.directory.appendingPathComponent("pending").appendingPathComponent(id + ".jpg")
            if let jpeg = try? Data(contentsOf: url) {
                try? await api.uploadImage(eventId: id, jpeg: jpeg)
                try? FileManager.default.removeItem(at: url)
            }
        }
    }
}
