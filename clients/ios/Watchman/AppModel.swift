import Foundation
import SwiftUI
import WatchmanCore

/// Pairing, policy, partner codes and heartbeats. Safari's Watchman extension does the watching (ADR-049).
@MainActor
final class AppModel: ObservableObject {
    @Published var pairing: Pairing?
    @Published var policy: Policy?
    @Published var lastHeartbeatAt: Date?
    @Published var lastError: String?
    @Published var queued = 0

    private let state = Shared.state

    func refresh() async {
        pairing = state.pairing
        policy = state.policy
        queued = state.queue.count
        if let p = state.store.get("last_heartbeat"), let d = Iso.parse(p) { lastHeartbeatAt = d }
        if pairing != nil { await heartbeat() }
    }

    func pair(server: String, code: String, name: String) async -> String? {
        do {
            let p = try await Api.pair(server: server, code: code, deviceName: name.isEmpty ? UIDevice.current.name : name)
            state.pairing = p
            state.policy = try await Api(server: p.server, token: p.token).policy()
            await refresh()
            return nil
        } catch let e as ApiError {
            return e.message
        } catch {
            return error.localizedDescription
        }
    }

    /// The app cannot see whether the Safari extension is on, so it reports protection as unknown, not
    /// off; the extension's own heartbeats carry its state (ADR-049).
    func heartbeat() async {
        guard let api = state.api() else { return }
        do {
            let r = try await api.heartbeat([
                "sent_at": Iso.now(), "app_version": "0.1.0", "booted_at": NSNull(), "safe_mode": false,
                "battery_pct": Int(UIDevice.current.batteryLevel * 100), "queued_events": state.queue.count,
                "state": ["protection": NSNull(), "accessibility": NSNull(), "capture": NSNull(),
                          "overlay": NSNull(), "device_admin": NSNull(), "extension": NSNull(), "vpn": NSNull()],
            ])
            state.store.put("last_heartbeat", Iso.now())
            lastHeartbeatAt = Date()
            lastError = nil
            if state.policy?.version != r.policyVersion || state.policy?.pausedUntil != r.pausedUntil {
                state.policy = try await api.policy()
                policy = state.policy
            }
            _ = try await state.queue.flush { try await api.events($0) }
            queued = state.queue.count
        } catch let e as ApiError {
            if e.status == 401 { state.pairing = nil; state.policy = nil; pairing = nil }
            lastError = "\(e.code): \(e.message)"
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
}
