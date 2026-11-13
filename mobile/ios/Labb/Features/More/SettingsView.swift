import LabbDesign
import SwiftUI

/// Your account: password, API keys, approving labb on a computer, integrations, the server
struct SettingsView: View {
    @Environment(AppModel.self) private var app
    @State private var changingPassword = false
    @State private var signingOut = false
    @State private var switching = false

    var body: some View {
        List {
            Section {
                LabeledContent("Username", value: app.user?.username ?? "")
                LabeledContent("Role", value: app.user?.isAdmin == true ? "Admin" : "Member")
                Button("Change password") { changingPassword = true }
            }
            Section {
                NavigationLink("API keys") { KeysView() }
                NavigationLink("Approve labb on a computer") { DeviceApprovalView() }
                NavigationLink("Integrations") { ConnectionsView() }
            } footer: {
                Text("API keys sign in labb on your computers and scripts. labb --login asks you to approve it with a code.")
            }
            Section {
                LabeledContent("Server", value: app.serverURL?.absoluteString ?? "")
                Button("Use another server") { switching = true }
                Button("Sign out", role: .destructive) { signingOut = true }
            }
        }
        .labbList()
        .navigationTitle("Settings")
        .sheet(isPresented: $changingPassword) { PasswordView() }
        .confirmationDialog("Sign out of \(app.serverURL?.host() ?? "the server")?", isPresented: $signingOut, titleVisibility: .visible) {
            Button("Sign out", role: .destructive) { Task { await app.signOut() } }
        }
        .confirmationDialog("Sign out and choose another server?", isPresented: $switching, titleVisibility: .visible) {
            Button("Choose another server", role: .destructive) { Task { await app.forgetServer() } }
        }
    }
}

private struct PasswordView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State private var current = ""
    @State private var new = ""
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            Form {
                SecureField("Current password", text: $current).textContentType(.password)
                SecureField("New password", text: $new).textContentType(.newPassword)
                if let problem { Text(problem).foregroundStyle(.labb.error) }
            }
            .navigationTitle("Change password")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Change") {
                        Task {
                            do {
                                try await app.api?.perform("POST", "/auth/password", body: ["current": .string(current), "new": .string(new)])
                                dismiss()
                            } catch {
                                problem = error.localizedDescription
                            }
                        }
                    }
                    .disabled(current.isEmpty || new.count < 8)
                }
            }
        }
        .presentationDetents([.medium])
    }
}

private struct KeysView: View {
    @Environment(AppModel.self) private var app
    @State private var keys: [ApiKey] = []
    @State private var naming = false
    @State private var name = ""
    @State private var created: ApiKey?
    @State private var errors = ErrorAlert()

    var body: some View {
        List {
            if let created, let key = created.key {
                Section {
                    Text(key).labbText(.sm, design: LabbFontDesign.mono).textSelection(.enabled)
                    Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.string = key }
                } header: {
                    Text("New key: \(created.name)")
                } footer: {
                    Text("Copy it now: it isn't shown again.")
                }
            }
            Section {
                ForEach(keys) { key in
                    VStack(alignment: .leading, spacing: LabbSpace.s1) {
                        Text(key.name).labbText(.md, weight: LabbWeight.medium).strikethrough(key.revokedAt != nil)
                        Text("\(key.shown)… · " + (key.revokedAt != nil ? "revoked" : key.lastUsedAt.map { "used \(relativeTime($0))" } ?? "never used"))
                            .labbText(.xs, design: LabbFontDesign.mono).foregroundStyle(.labb.muted)
                    }
                    .swipeActions {
                        if key.revokedAt == nil {
                            Button("Revoke", role: .destructive) { revoke(key) }
                        }
                    }
                }
            }
        }
        .labbList()
        .navigationTitle("API keys")
        .toolbar {
            ToolbarItem(placement: .primaryAction) { Button("New key", systemImage: "plus") { naming = true } }
        }
        .alert("New API key", isPresented: $naming) {
            TextField("What it's for", text: $name)
            Button("Cancel", role: .cancel) {}
            Button("Create", action: create)
        }
        .errorAlert(errors)
        .task { await load() }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { keys = try await api.get("/keys") }
    }

    private func create() {
        let name = self.name.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        Task {
            await errors.run { created = try await app.api!.send("POST", "/keys", body: ["name": .string(name)]) }
            self.name = ""
            await load()
        }
    }

    private func revoke(_ key: ApiKey) {
        Task {
            await errors.run { try await app.api?.perform("DELETE", "/keys/\(key.id)") }
            await load()
        }
    }
}

/// labb --login on a computer shows a code: approving it here signs that computer in
private struct DeviceApprovalView: View {
    @Environment(AppModel.self) private var app
    @State private var code = ""
    @State private var login: DeviceLogin?
    @State private var done: String?
    @State private var errors = ErrorAlert()

    var body: some View {
        Form {
            Section {
                TextField("Code", text: $code)
                    .labbText(.xl, weight: LabbWeight.bold, design: LabbFontDesign.mono)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                Button("Look it up", action: lookUp).disabled(code.trimmingCharacters(in: .whitespaces).isEmpty)
            } footer: {
                Text("Run labb --login on the computer, and type the code it shows.")
            }
            if let login {
                Section {
                    Text("\(login.machine) wants to sign in to labb as \(app.user?.username ?? "you").")
                    HStack(spacing: LabbSpace.s6) {
                        Button("Approve") { answer(true) }.buttonStyle(.labb(.primary))
                        Button("Deny") { answer(false) }.buttonStyle(.labb(.danger))
                    }
                }
            }
            if let done { Section { Label(done, systemImage: "checkmark.circle").foregroundStyle(.labb.series3) } }
        }
        .navigationTitle("Approve labb")
        .errorAlert(errors)
    }

    private var cleanCode: String { code.trimmingCharacters(in: .whitespaces).uppercased() }

    private func lookUp() {
        done = nil
        Task { await errors.run { login = try await app.api!.get("/auth/device/\(cleanCode)") } }
    }

    private func answer(_ approve: Bool) {
        Task {
            await errors.run {
                try await app.api?.perform("POST", "/auth/device/\(cleanCode)", body: ["approve": .bool(approve)])
                done = approve ? "Approved: labb on \(login?.machine ?? "the computer") is signed in." : "Denied."
                login = nil
                code = ""
            }
        }
    }
}

/// Google and other apps labb can use for you. Connecting signs in on the app's own site; when the
/// server sends you back to itself, finishing needs the web app's session, so that's done there.
private struct ConnectionsView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.openURL) private var openURL
    @State private var connections: [AppConnection] = []
    @State private var pasting: String?
    @State private var pasted = ""
    @State private var errors = ErrorAlert()

    var body: some View {
        List {
            ForEach(connections) { connection in
                Section {
                    if let account = connection.connection {
                        LabeledContent("Account", value: account.account)
                        if account.status == "expired" {
                            Text("The connection expired: connect again.").foregroundStyle(.labb.error)
                        }
                        if !account.missing.isEmpty {
                            Text("Not allowed: " + account.missing.joined(separator: ", ")).labbText(.sm).foregroundStyle(.labb.muted)
                        }
                        Button("Disconnect", role: .destructive) { disconnect(connection) }
                    }
                    if connection.connection == nil || connection.connection?.status == "expired" {
                        if connection.available {
                            Button("Connect") { connect(connection) }
                        } else {
                            Text("An admin hasn't set this up yet.").foregroundStyle(.labb.muted)
                        }
                    }
                } header: {
                    Text(connection.label)
                } footer: {
                    Text("Lets labb use " + connection.uses.joined(separator: ", ") + ".")
                }
            }
        }
        .labbList()
        .navigationTitle("Integrations")
        .alert("Paste the address", isPresented: Binding(get: { pasting != nil }, set: { if !$0 { pasting = nil } })) {
            TextField("http://localhost:…", text: $pasted)
            Button("Cancel", role: .cancel) {}
            Button("Finish") { finish() }
        } message: {
            Text("After allowing labb, copy the address the browser ended up at (even if the page didn't load) and paste it here.")
        }
        .errorAlert(errors)
        .task { await load() }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { connections = try await api.get("/connections") }
    }

    private func connect(_ connection: AppConnection) {
        Task {
            await errors.run {
                let start: ConnectStart = try await app.api!.send("POST", "/connections/\(connection.name)/start")
                guard start.mode == "paste" else {
                    errors.message = "This server finishes connecting in the web app. Open \(app.serverURL?.host() ?? "it") in a browser, go to Settings → Integrations and connect there."
                    return
                }
                if let url = URL(string: start.url) { openURL(url) }
                pasting = connection.name
            }
        }
    }

    private func finish() {
        guard let name = pasting else { return }
        Task {
            await errors.run {
                let _: AppConnection = try await app.api!.send("POST", "/connections/\(name)/finish", body: ["url": .string(pasted)])
            }
            pasted = ""
            await load()
        }
    }

    private func disconnect(_ connection: AppConnection) {
        Task {
            await errors.run { try await app.api?.perform("DELETE", "/connections/\(connection.name)") }
            await load()
        }
    }
}

// MARK: - Server (admins)

struct AdminView: View {
    var body: some View {
        List {
            NavigationLink("People") { UsersView() }
            NavigationLink("Invites") { InvitesView() }
            NavigationLink("Health") { HealthView() }
            NavigationLink("Limits") { LimitsView() }
            Section {
                Text("Models and providers, MCP servers, speech and connected-app setup are in the web app's Settings.")
                    .labbText(.sm).foregroundStyle(.labb.muted)
            }
        }
        .labbList()
        .navigationTitle("Server")
    }
}

private struct UsersView: View {
    @Environment(AppModel.self) private var app
    @State private var users: [User] = []
    @State private var errors = ErrorAlert()

    var body: some View {
        List {
            ForEach(users) { user in
                VStack(alignment: .leading, spacing: LabbSpace.s4) {
                    HStack {
                        Text(user.username).labbText(.md, weight: LabbWeight.semibold)
                        if user.id == app.user?.id { Text("You").labbText(.xs).foregroundStyle(.labb.muted) }
                    }
                    if user.id != app.user?.id {
                        Picker("Role", selection: Binding(get: { user.role }, set: { change(user, ["role": .string($0.rawValue)]) })) {
                            Text("Member").tag(Role.member)
                            Text("Admin").tag(Role.admin)
                        }
                        Toggle("Can sign in", isOn: Binding(get: { user.disabled != true }, set: { change(user, ["disabled": .bool(!$0)]) }))
                    }
                }
            }
        }
        .labbList()
        .navigationTitle("People")
        .errorAlert(errors)
        .task { await load() }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { users = try await api.get("/admin/users") }
    }

    private func change(_ user: User, _ fields: [String: JSONValue]) {
        Task {
            await errors.run { let _: User = try await app.api!.send("PATCH", "/admin/users/\(user.id)", body: .object(fields)) }
            await load()
        }
    }
}

private struct InvitesView: View {
    @Environment(AppModel.self) private var app
    @State private var invites: [Invite] = []
    @State private var created: Invite?
    @State private var errors = ErrorAlert()

    var body: some View {
        List {
            if let created, let link = created.url, let url = URL(string: link) {
                Section {
                    Text(link).labbText(.xs, design: LabbFontDesign.mono).textSelection(.enabled)
                    ShareLink("Send the invite", item: url)
                    Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.string = link }
                } header: {
                    Text("New invite")
                } footer: {
                    Text("It works once, and only until \(shortTime(created.expiresAt)). It isn't shown again.")
                }
            }
            Section("Open invites") {
                if invites.isEmpty { Text("None.").foregroundStyle(.labb.muted) }
                ForEach(invites) { invite in
                    LabeledContent(invite.role == .admin ? "Admin" : "Member", value: "until \(shortTime(invite.expiresAt))")
                        .swipeActions { Button("Cancel", role: .destructive) { cancel(invite) } }
                }
            }
        }
        .labbList()
        .navigationTitle("Invites")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Menu {
                    Button("Invite a member") { create(.member) }
                    Button("Invite an admin") { create(.admin) }
                } label: {
                    Label("Invite", systemImage: "plus")
                }
            }
        }
        .errorAlert(errors)
        .task { await load() }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { invites = try await api.get("/admin/invites") }
    }

    private func create(_ role: Role) {
        Task {
            await errors.run { created = try await app.api!.send("POST", "/admin/invites", body: ["role": .string(role.rawValue)]) }
            await load()
        }
    }

    private func cancel(_ invite: Invite) {
        Task {
            await errors.run { try await app.api?.perform("DELETE", "/admin/invites/\(invite.id)") }
            await load()
        }
    }
}

private struct HealthView: View {
    @Environment(AppModel.self) private var app
    @State private var checks: [HealthCheck] = []
    @State private var errors = ErrorAlert()

    private struct Checks: Decodable {
        let checks: [HealthCheck]
    }

    var body: some View {
        List {
            ForEach(checks) { check in
                HStack(alignment: .top, spacing: LabbSpace.s5) {
                    Image(systemName: check.ok == true ? "checkmark.circle.fill" : check.ok == false ? "xmark.octagon.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(check.ok == true ? Color.labb.series3 : check.ok == false ? Color.labb.error : Color.labb.series4)
                    VStack(alignment: .leading, spacing: LabbSpace.s1) {
                        Text(check.name).labbText(.md, weight: LabbWeight.medium)
                        Text(check.detail).labbText(.sm).foregroundStyle(.labb.muted)
                    }
                }
            }
        }
        .labbList()
        .navigationTitle("Health")
        .refreshable { await load() }
        .errorAlert(errors)
        .task { await load() }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { checks = try await api.get("/admin/health", as: Checks.self).checks }
    }
}

private struct LimitsView: View {
    @Environment(AppModel.self) private var app
    @State private var limits: [LimitInfo] = []
    @State private var edited: [String: Double] = [:]
    @State private var errors = ErrorAlert()

    var body: some View {
        Form {
            ForEach(Dictionary(grouping: limits, by: \.group).sorted { $0.key < $1.key }, id: \.key) { group, items in
                Section(group) {
                    ForEach(items) { limit in
                        VStack(alignment: .leading, spacing: LabbSpace.s2) {
                            HStack {
                                Text(limit.label)
                                Spacer()
                                TextField("", value: binding(limit), format: .number)
                                    .keyboardType(.decimalPad)
                                    .multilineTextAlignment(.trailing)
                                    .frame(width: 90)
                                Text(limit.unit).foregroundStyle(.labb.muted)
                            }
                            Text(limit.description).labbText(.xs).foregroundStyle(.labb.muted)
                        }
                    }
                }
            }
        }
        .navigationTitle("Limits")
        .toolbar {
            ToolbarItem(placement: .confirmationAction) { Button("Save", action: save).disabled(edited.isEmpty) }
        }
        .errorAlert(errors)
        .task { await load() }
    }

    private func binding(_ limit: LimitInfo) -> Binding<Double> {
        Binding(get: { edited[limit.key] ?? limit.value }, set: { edited[limit.key] = min(max($0, limit.min), limit.max) })
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { limits = try await api.get("/admin/limits") }
    }

    private func save() {
        Task {
            await errors.run {
                limits = try await app.api!.send("PUT", "/admin/limits", body: .object(edited.mapValues { .number($0) }))
                edited = [:]
            }
        }
    }
}
