import Foundation
import SwiftUI
import WatchmanCore
import CryptoKit

/// Pairing, policy, heartbeats from the app side. The broadcast extension does the rest while it runs.
@MainActor
final class AppModel: ObservableObject {
    @Published var pairing: Pairing?
    @Published var policy: Policy?
    @Published var lastHeartbeatAt: Date?
    @Published var lastError: String?
    @Published var queued = 0
    @Published var modelReady = false
    @Published var broadcasting = false

    private let state = Shared.state

    func refresh() async {
        pairing = state.pairing
        policy = state.policy
        queued = state.queue.count
        modelReady = state.classifier.map { FileManager.default.fileExists(atPath: Shared.modelsDirectory.appendingPathComponent($0.file).path) } ?? false
        broadcasting = (state.store.get("broadcasting") == "1")
        if let p = state.store.get("last_heartbeat"), let d = Iso.parse(p) { lastHeartbeatAt = d }
        if pairing != nil { await heartbeat() }
    }

    func pair(server: String, code: String, name: String) async -> String? {
        do {
            let p = try await Api.pair(server: server, code: code, deviceName: name.isEmpty ? UIDevice.current.name : name)
            state.pairing = p
            state.policy = try await Api(server: p.server, token: p.token).policy()
            await refresh()
            await ensureModel()
            return nil
        } catch let e as ApiError {
            return e.message
        } catch {
            return error.localizedDescription
        }
    }

    /// The app reports capture as granted only while a broadcast is running (ADR-012: reports only).
    func heartbeat() async {
        guard let api = state.api() else { return }
        do {
            let r = try await api.heartbeat([
                "sent_at": Iso.now(), "app_version": "0.1.0", "booted_at": NSNull(), "safe_mode": false,
                "battery_pct": Int(UIDevice.current.batteryLevel * 100), "queued_events": state.queue.count,
                "state": ["protection": broadcasting, "accessibility": NSNull(), "capture": broadcasting ? "granted" : "denied",
                          "overlay": NSNull(), "device_admin": NSNull(), "extension": NSNull(), "vpn": NSNull()],
            ])
            state.store.put("last_heartbeat", Iso.now())
            lastHeartbeatAt = Date()
            lastError = nil
            if state.policy?.version != r.policyVersion || state.policy?.pausedUntil != r.pausedUntil {
                state.policy = try await api.policy()
                policy = state.policy
            }
            let wanted = try await state.queue.flush { try await api.events($0) }
            for id in wanted {
                let url = Shared.directory.appendingPathComponent("pending").appendingPathComponent(id + ".jpg")
                if let jpeg = try? Data(contentsOf: url) {
                    try? await api.uploadImage(eventId: id, jpeg: jpeg)
                    try? FileManager.default.removeItem(at: url)
                }
            }
            queued = state.queue.count
        } catch let e as ApiError {
            if e.status == 401 { state.pairing = nil; state.policy = nil; pairing = nil }
            lastError = "\(e.code): \(e.message)"
        } catch {
            lastError = error.localizedDescription
        }
    }

    func ensureModel() async {
        guard let api = state.api() else { return }
        do {
            let manifest = try await api.manifest()
            guard let entry = manifest.models.first(where: { $0.role == "crop_classifier_int8" }) ?? manifest.models.first(where: { $0.role == "crop_classifier" }) else { return }
            let target = Shared.modelsDirectory.appendingPathComponent(entry.file)
            if let data = try? Data(contentsOf: target), sha256(data) == entry.sha256 {
                state.classifier = entry
                modelReady = true
                return
            }
            let bytes = try await api.modelBytes(path: entry.url)
            guard sha256(bytes) == entry.sha256 else { lastError = "model hash mismatch"; return }
            try FileManager.default.createDirectory(at: Shared.modelsDirectory, withIntermediateDirectories: true)
            try bytes.write(to: target, options: .atomic)
            state.classifier = entry
            modelReady = true
        } catch {
            lastError = error.localizedDescription
        }
    }

    func unlock(code: String) async -> String? {
        guard let api = state.api() else { return "not paired" }
        do {
            _ = try await api.unlock(code: code)
            state.policy = try await api.policy()
            policy = state.policy
            return nil
        } catch let e as ApiError { return e.message } catch { return error.localizedDescription }
    }

    func requestUnlock(minutes: Int, reason: String) async -> String? {
        guard let api = state.api() else { return "not paired" }
        do { try await api.requestUnlock(minutes: minutes, reason: reason); return nil }
        catch let e as ApiError { return e.message } catch { return error.localizedDescription }
    }

    private func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
