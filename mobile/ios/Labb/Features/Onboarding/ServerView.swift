import LabbDesign
import SwiftUI

/// The first screen: which labb server to use. An address, or an invite link someone sent.
struct ServerView: View {
    @Environment(AppModel.self) private var app
    @State private var address = ""
    @State private var problem: String?
    @FocusState private var focused: Bool

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: LabbSpace.s12) {
                    VStack(alignment: .leading, spacing: LabbSpace.s4) {
                        Text("labb").labbHeading(.xxxl)
                        Text("Connect to your labb server to chat with your models, agents and PCs.")
                            .foregroundStyle(.labb.muted)
                    }

                    VStack(alignment: .leading, spacing: LabbSpace.s4) {
                        Text("Server").labbLabel()
                        TextField("labb.example.com or 192.168.1.20:8000", text: $address)
                            .textContentType(.URL)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .submitLabel(.go)
                            .focused($focused)
                            .onSubmit(connect)
                            .labbField(invalid: problem != nil)
                        Text("The address you open labb at in a browser. Got an invite link? Paste it here.")
                            .labbText(.sm)
                            .foregroundStyle(.labb.muted)
                        if let message = problem ?? app.serverProblem {
                            Label(message, systemImage: "exclamationmark.triangle.fill")
                                .labbText(.sm)
                                .foregroundStyle(.labb.error)
                        }
                    }

                    Button(action: connect) {
                        Text("Continue").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.labb(.primary))
                    .disabled(address.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                .padding(LabbSpace.s8)
            }
            .labbRoot()
            .onAppear {
                if let url = app.serverURL { address = url.absoluteString }
                focused = address.isEmpty
            }
        }
    }

    private func connect() {
        guard let parsed = AppModel.parse(address) else {
            problem = "That doesn't look like an address. Try something like labb.example.com."
            return
        }
        problem = nil
        app.connect(to: parsed.url, invite: parsed.invite)
    }
}

/// Asking the server who we are
struct CheckingView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        VStack(spacing: LabbSpace.s6) {
            ProgressView()
            Text("Connecting to \(app.serverURL?.host() ?? "the server")…").foregroundStyle(.labb.muted)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .labbRoot()
    }
}
