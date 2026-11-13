import XCTest

/// Walks through the app against a real labb server, saving a screenshot at each step. Runs only
/// with a server to use (it signs up there on a fresh one):
///
///     TEST_RUNNER_LABB_SERVER=http://127.0.0.1:8000 xcodebuild test -scheme LabbUITests \
///         -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
@MainActor
final class WalkthroughTests: XCTestCase {
    private var app: XCUIApplication!
    private let username = "admin"
    private let password = "labb-test-password"

    override func setUp() async throws {
        continueAfterFailure = false
        guard let server = ProcessInfo.processInfo.environment["LABB_SERVER"] else {
            throw XCTSkip("Set TEST_RUNNER_LABB_SERVER to a labb server to walk through the app")
        }
        app = XCUIApplication()
        // The saved server, as if it had been typed in (an argument overrides UserDefaults)
        app.launchArguments = ["-server", server]
        app.launch()
    }

    func testWalkthrough() throws {
        signIn()
        shot("1-chats")

        // A chat: send a message and watch the reply come in
        // Right after signing in the list may still be settling: tap again if the chat didn't open
        let newChat = app.navigationBars.buttons["New chat"].firstMatch
        XCTAssertTrue(newChat.waitForExistence(timeout: 10))
        newChat.tap()
        if !field("Message labb").waitForExistence(timeout: 5) { newChat.tap() }
        shot("2-new-chat")
        let message = field("Message labb")
        message.tap()
        message.typeText("Plan three days in Lisbon on foot")
        app.buttons["Send"].tap()
        XCTAssertTrue(shows("Lisbon in three days", timeout: 30), "the reply came in")
        sleep(3)
        shot("3-reply")
        app.staticTexts["Lisbon in three days"].swipeDown()
        shot("4-reply-start")
        app.buttons["Options"].firstMatch.tap()
        shot("5-chat-options")
        // Close the menu by tapping outside it
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.1, dy: 0.9)).tap()
        back()
        shot("6-chat-list")

        // An agent
        app.tabBars.buttons["Agents"].tap()
        app.navigationBars.buttons["New agent"].tap()
        let name = field("Name")
        name.tap()
        name.typeText("Travel guide")
        shot("7-agent-editor")
        app.navigationBars.buttons["Create"].tap()
        XCTAssertTrue(shows("Travel guide", timeout: 10))
        shot("8-agents")
        app.staticTexts["Travel guide"].firstMatch.tap()
        shot("9-agent")

        // A task
        app.tabBars.buttons["Tasks"].tap()
        app.navigationBars.buttons["New task"].tap()
        let taskName = field("Name")
        taskName.tap()
        taskName.typeText("Morning summary")
        let prompt = field("What to ask, each time it runs")
        prompt.tap()
        prompt.typeText("Summarize my day")
        sleep(1)
        shot("10-task-editor")
        app.navigationBars.buttons["Create"].tap()
        XCTAssertTrue(shows("Morning summary", timeout: 10))
        shot("11-tasks")

        // Library: a memory
        app.tabBars.buttons["Library"].tap()
        shot("12-library")
        app.staticTexts["Memories"].tap()
        app.navigationBars.buttons["Add"].firstMatch.tap()
        let memory = field("Something labb should remember")
        memory.tap()
        memory.typeText("I prefer walking to taking the bus")
        app.navigationBars.buttons["Save"].tap()
        XCTAssertTrue(shows("I prefer walking to taking the bus", timeout: 10))
        shot("13-memories")

        // More: usage, settings, the server
        app.tabBars.buttons["More"].tap()
        shot("14-more")
        app.staticTexts["Usage"].tap()
        sleep(2)
        shot("15-usage")
        back()
        app.staticTexts["Server"].tap()
        app.staticTexts["Invites"].tap()
        app.navigationBars.buttons["Invite"].tap()
        app.buttons["Invite a member"].tap()
        XCTAssertTrue(shows("New invite", timeout: 10))
        shot("16-invite")
    }

    // MARK: - Helpers

    private func signIn() {
        let chats = app.tabBars.buttons["Chats"]
        if chats.waitForExistence(timeout: 5) { return }
        let user = field("Username")
        XCTAssertTrue(user.waitForExistence(timeout: 15), "the sign-in screen")
        shot("0-sign-in")
        user.tap()
        user.typeText(username)
        let secret = app.secureTextFields["Password"]
        secret.tap()
        secret.typeText(password)
        let create = app.buttons["Create account"]
        (create.exists ? create : app.buttons["Sign in"]).tap()
        XCTAssertTrue(chats.waitForExistence(timeout: 15), "signed in")
    }

    /// Whether the screen shows this text, as text or as a button's label
    private func shows(_ text: String, timeout: TimeInterval) -> Bool {
        app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", text)).firstMatch.waitForExistence(timeout: timeout)
    }

    private func back() {
        app.navigationBars.buttons["BackButton"].firstMatch.tap()
    }

    /// A text field by its placeholder, one line or several
    private func field(_ label: String) -> XCUIElement {
        let line = app.textFields[label]
        if line.waitForExistence(timeout: 3) { return line }
        return app.textViews[label]
    }

    private func shot(_ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
