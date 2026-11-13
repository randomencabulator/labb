import LabbDesign
import SwiftUI

/// Signing in, as the web does: the server's first account on a fresh server, an invite to join
/// with, or a username and password
struct SignInView: View {
    @Environment(AppModel.self) private var app
    let setupNeeded: Bool
    let invite: String?

    @State private var username = ""
    @State private var password = ""
    @State private var setupToken = ""
    @State private var problem: String?
    @State private var working = false
    @State private var inviteInfo: InviteInfo?
    @State private var inviteProblem: String?
    @FocusState private var field: Field?

    private enum Field {
        case username, password, setupToken
    }

    private var mode: Mode {
        if setupNeeded { return .setup }
        if invite != nil { return .invite }
        return .signIn
    }

    private enum Mode {
        case setup, invite, signIn
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: LabbSpace.s12) {
                    VStack(alignment: .leading, spacing: LabbSpace.s4) {
                        Text(title).labbHeading(.xxl)
                        Text(subtitle).foregroundStyle(.labb.muted)
                    }

                    if mode == .invite, let inviteProblem {
                        Label(inviteProblem, systemImage: "xmark.octagon.fill").foregroundStyle(.labb.error)
                    } else {
                        form
                    }

                    if mode == .signIn {
                        Text("New here? Ask an admin for an invite link, and paste it as the server address.")
                            .labbText(.sm)
                            .foregroundStyle(.labb.muted)
                    }
                }
                .padding(LabbSpace.s8)
            }
            .labbRoot()
            .navigationTitle(app.serverURL?.host() ?? "")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Server") { Task { await app.forgetServer() } }
                }
            }
            .task(id: invite) { await loadInvite() }
        }
    }

    private var form: some View {
        VStack(alignment: .leading, spacing: LabbSpace.s8) {
            VStack(alignment: .leading, spacing: LabbSpace.s4) {
                if mode == .setup {
                    // The ?setup= part of the link the server printed when it started
                    TextField("Setup code", text: $setupToken)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.next)
                        .focused($field, equals: .setupToken)
                        .onSubmit { field = .username }
                        .labbField()
                    Text("From the setup link in the server's log (docker compose logs labb-web): the part after ?setup=")
                        .labbText(.sm)
                        .foregroundStyle(.labb.muted)
                }
                TextField("Username", text: $username)
                    .textContentType(mode == .signIn ? .username : .username)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.next)
                    .focused($field, equals: .username)
                    .onSubmit { field = .password }
                    .labbField()
                SecureField("Password", text: $password)
                    .textContentType(mode == .signIn ? .password : .newPassword)
                    .submitLabel(.go)
                    .focused($field, equals: .password)
                    .onSubmit(submit)
                    .labbField(invalid: problem != nil)
                if mode != .signIn {
                    Text("At least 8 characters.").labbText(.sm).foregroundStyle(.labb.muted)
                }
                if let problem {
                    Label(problem, systemImage: "exclamationmark.triangle.fill").labbText(.sm).foregroundStyle(.labb.error)
                }
            }
            Button(action: submit) {
                Group {
                    if working { ProgressView() } else { Text(buttonLabel) }
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.labb(.primary))
            .disabled(working || username.isEmpty || password.isEmpty)
        }
        .onAppear { field = .username }
    }

    private var title: String {
        switch mode {
        case .setup: "Set up labb"
        case .invite: "Join labb"
        case .signIn: "Sign in"
        }
    }

    private var subtitle: String {
        switch mode {
        case .setup: "Create the first account. It's the admin: it invites everyone else."
        case .invite:
            if let inviteInfo { "You're invited as \(inviteInfo.role == .admin ? "an admin" : "a member"). Choose a username and password." }
            else { "Choose a username and password." }
        case .signIn: "With your username and password on \(app.serverURL?.host() ?? "this server")."
        }
    }

    private var buttonLabel: String {
        switch mode {
        case .setup: "Create account"
        case .invite: "Join"
        case .signIn: "Sign in"
        }
    }

    private func loadInvite() async {
        guard let invite, let api = app.api else { return }
        do {
            inviteInfo = try await api.get("/auth/invite/\(invite)")
        } catch {
            inviteProblem = "This invite link was used already or has expired. Ask whoever sent it for a new one."
        }
    }

    private func submit() {
        guard let api = app.api, !username.isEmpty, !password.isEmpty, !working else { return }
        working = true
        problem = nil
        var body: JSONValue = ["username": .string(username), "password": .string(password)]
        if mode == .setup {
            body = ["username": .string(username), "password": .string(password), "token": .string(setupToken)]
        }
        let path = switch mode {
        case .setup: "/auth/setup"
        case .invite: "/auth/invite/\(invite ?? "")"
        case .signIn: "/auth/login"
        }
        Task {
            defer { working = false }
            do {
                let user: User = try await api.send("POST", path, body: body)
                app.signedIn(user)
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}
