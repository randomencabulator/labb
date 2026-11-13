import LabbDesign
import SwiftUI

@main
struct LabbApp: App {
    @State private var app = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(app)
                .tint(.labb.accent)
        }
    }
}

/// Which part of the app to show: choosing a server, signing in, or the app itself
struct RootView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        switch app.phase {
        case .server: ServerView()
        case .checking: CheckingView()
        case .signIn(let setupNeeded, let invite): SignInView(setupNeeded: setupNeeded, invite: invite)
        case .ready: MainView()
        }
    }
}

/// The app once signed in: a tab bar, which is a sidebar on iPad
struct MainView: View {
    @Environment(AppModel.self) private var app
    @State private var tab: AppTab = .chats

    enum AppTab: Hashable {
        case chats, agents, tasks, library, more
    }

    var body: some View {
        TabView(selection: $tab) {
            Tab("Chats", systemImage: "bubble.left.and.bubble.right", value: AppTab.chats) {
                ChatsView()
            }
            Tab("Agents", systemImage: "person.2", value: AppTab.agents) {
                AgentsView()
            }
            Tab("Tasks", systemImage: "clock", value: AppTab.tasks) {
                TasksView()
            }
            Tab("Library", systemImage: "books.vertical", value: AppTab.library) {
                LibraryView()
            }
            Tab("More", systemImage: "ellipsis.circle", value: AppTab.more) {
                MoreView()
            }
        }
        .tabViewStyle(.sidebarAdaptable)
        // Another screen asked to open a chat: go to the Chats tab, which opens it
        .onChange(of: app.openChatRequest) { _, request in
            if request != nil { tab = .chats }
        }
        .onChange(of: app.pendingNewChat) { _, request in
            if request != nil { tab = .chats }
        }
    }
}
