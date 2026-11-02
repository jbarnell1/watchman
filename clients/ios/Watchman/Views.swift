import ReplayKit
import SwiftUI
import WatchmanCore

private let muted = Color(red: 0.37, green: 0.42, blue: 0.44)
private let negative = Color(red: 0.77, green: 0.14, blue: 0.16)
private let warning = Color(red: 0.57, green: 0.25, blue: 0.05)

struct PairView: View {
    @EnvironmentObject var model: AppModel
    @State private var server = ""
    @State private var code = ""
    @State private var name = ""
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        Form {
            Section {
                Text("Your household server runs on your own Tailscale network. The admin creates a pairing code on its Devices page; it is valid for 15 minutes.")
                    .foregroundStyle(muted)
            }
            Section("Server") {
                TextField("https://watchman.your-tailnet.ts.net", text: $server)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
            }
            Section("Pairing code") {
                TextField("K7Q-4MZ-P2D", text: $code).textInputAutocapitalization(.characters).autocorrectionDisabled()
            }
            Section("This phone's name") {
                TextField("iPhone", text: $name)
            }
            Section {
                Button(busy ? "Pairing" : "Pair") {
                    busy = true
                    Task {
                        error = await model.pair(server: server.trimmingCharacters(in: .whitespaces), code: code.trimmingCharacters(in: .whitespaces), name: name)
                        busy = false
                    }
                }
                .disabled(busy || server.isEmpty || code.isEmpty)
                if let error { Text(error).foregroundStyle(negative) }
            }
        }
        .navigationTitle("Pair this phone")
    }
}

struct StatusView: View {
    @EnvironmentObject var model: AppModel
    @State private var showPause = false

    var paused: Bool { model.policy?.isPaused() ?? false }

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 4) {
                    Text(paused ? "Paused by a partner code" : (model.broadcasting ? "Screen report on" : "Screen report off"))
                        .font(.title2.weight(.medium))
                        .foregroundStyle(paused ? warning : (model.broadcasting ? .primary : negative))
                    Text(statusLine).foregroundStyle(muted).font(.subheadline)
                    if let e = model.lastError { Text(e).foregroundStyle(negative).font(.caption) }
                }
                .padding(.vertical, 4)
            }
            Section("What this phone can do") {
                row("Screen report (broadcast)", model.broadcasting)
                row("Model on the phone", model.modelReady)
                row("Safari image blur", true, note: "Turn on the Watchman extension in Settings, Safari, Extensions.")
            }
            Section {
                BroadcastPickerView().frame(height: 44)
                Text("Tap the button and choose Watchman, then Start Broadcast. The red indicator means the report is running. Stopping it is reported to your partners.")
                    .font(.caption).foregroundStyle(muted)
            } header: { Text("Start the screen report") }
            Section {
                Button("Partner code or pause request") { showPause = true }
                if model.queued > 0 { Text("\(model.queued) event\(model.queued == 1 ? "" : "s") waiting to send.").font(.caption).foregroundStyle(muted) }
            }
            Section {
                Text("iPhone lets no app see or cover another app, so this phone reports what it saw rather than blurring it, except in Safari where the extension holds images back. Blocked apps and web limits come from Screen Time with a passcode your partner holds.")
                    .font(.caption).foregroundStyle(muted)
            }
        }
        .navigationTitle("Watchman")
        .refreshable { await model.refresh() }
        .sheet(isPresented: $showPause) { PauseView() }
    }

    private var statusLine: String {
        if paused, let until = model.policy?.pausedUntil, let d = Iso.parse(until) {
            return "Until \(d.formatted(date: .omitted, time: .shortened)). Every pause appears in the next digest."
        }
        let hb = model.lastHeartbeatAt.map { "heartbeat \($0.formatted(.relative(presentation: .named)))" } ?? "no heartbeat yet"
        return "Reporting to \(model.pairing?.serverName ?? ""), \(hb)."
    }

    private func row(_ label: String, _ on: Bool, note: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(label)
                Spacer()
                Text(on ? "on" : "off").font(.caption.weight(.medium))
                    .padding(.horizontal, 7).padding(.vertical, 2)
                    .background(on ? Color(red: 0.86, green: 0.98, blue: 0.9) : Color(red: 1, green: 0.93, blue: 0.92))
                    .foregroundStyle(on ? Color(red: 0.02, green: 0.15, blue: 0.05) : Color(red: 0.51, green: 0.11, blue: 0.12))
                    .clipShape(RoundedRectangle(cornerRadius: 4))
            }
            if let note { Text(note).font(.caption).foregroundStyle(muted) }
        }
    }
}

struct PauseView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var code = ""
    @State private var minutes = "30"
    @State private var reason = ""
    @State private var codeError: String?
    @State private var note: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Nothing pauses protection without a partner code. A code is single use and the pause appears in the next digest.").foregroundStyle(muted)
                }
                Section("Code from your partner") {
                    TextField("482-913", text: $code).keyboardType(.numberPad)
                    Button("Pause") {
                        Task {
                            codeError = await model.unlock(code: code.trimmingCharacters(in: .whitespaces))
                            if codeError == nil { dismiss() }
                        }
                    }
                    if let codeError { Text(codeError).foregroundStyle(negative) }
                }
                Section("Or ask for one") {
                    TextField("Minutes", text: $minutes).keyboardType(.numberPad)
                    TextField("Reason", text: $reason)
                    Button("Ask my partners") {
                        Task { note = await model.requestUnlock(minutes: Int(minutes) ?? 30, reason: reason) ?? "Asked. A partner can send you a code." }
                    }
                    if let note { Text(note).foregroundStyle(muted) }
                }
            }
            .navigationTitle("Pause")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }
    }
}

/// The system control that starts the broadcast extension.
struct BroadcastPickerView: UIViewRepresentable {
    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let v = RPSystemBroadcastPickerView()
        v.preferredExtension = "app.watchman.ios.broadcast"
        v.showsMicrophoneButton = false
        return v
    }

    func updateUIView(_ uiView: RPSystemBroadcastPickerView, context: Context) {}
}
