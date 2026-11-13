import Foundation

/// What went wrong with a request, in words a person can read
struct APIError: LocalizedError, Sendable {
    let status: Int
    let message: String

    var errorDescription: String? { message }

    static let notLoggedIn = 401
}

/// The labb server's API, as the web app uses it: the session cookie signs requests in (kept by
/// URLSession's cookie storage, so it lasts between launches), and requests that change something
/// carry `X-Labb: 1`, which the server wants with the cookie.
final class APIClient: Sendable {
    let base: URL
    private let session: URLSession
    /// Called when the server says the session ended (a 401 outside /auth/)
    private let onLoggedOut: @Sendable () -> Void

    init(base: URL, onLoggedOut: @escaping @Sendable () -> Void = {}) {
        self.base = base
        self.onLoggedOut = onLoggedOut
        let configuration = URLSessionConfiguration.default
        configuration.httpCookieStorage = .shared
        configuration.httpShouldSetCookies = true
        configuration.httpCookieAcceptPolicy = .always
        // Replies can take a while between events; the server pings while it works
        configuration.timeoutIntervalForRequest = 300
        configuration.waitsForConnectivity = false
        session = URLSession(configuration: configuration)
    }

    func url(_ path: String, query: [String: String] = [:]) -> URL {
        var components = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        let basePath = components.path.hasSuffix("/") ? String(components.path.dropLast()) : components.path
        components.path = basePath + path
        if !query.isEmpty {
            components.queryItems = query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        return components.url!
    }

    private func request(_ method: String, _ path: String, query: [String: String], body: JSONValue?) throws -> URLRequest {
        var request = URLRequest(url: url(path, query: query))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if method != "GET" && method != "HEAD" { request.setValue("1", forHTTPHeaderField: "X-Labb") }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONEncoder().encode(body)
        }
        return request
    }

    /// The server's reason for a failed request: FastAPI puts it in `detail`
    private func failure(_ data: Data, _ response: HTTPURLResponse, path: String) -> APIError {
        if response.statusCode == APIError.notLoggedIn && !path.hasPrefix("/auth/") { onLoggedOut() }
        struct Detail: Decodable { let detail: String? }
        let detail = (try? JSONDecoder().decode(Detail.self, from: data))?.detail
        let message = detail ?? HTTPURLResponse.localizedString(forStatusCode: response.statusCode).capitalized
        return APIError(status: response.statusCode, message: message)
    }

    func data(_ method: String, _ path: String, query: [String: String] = [:], body: JSONValue? = nil) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: try request(method, path, query: query, body: body))
        guard let http = response as? HTTPURLResponse else { throw APIError(status: 0, message: "No answer from the server") }
        guard (200 ..< 300).contains(http.statusCode) else { throw failure(data, http, path: path) }
        return (data, http)
    }

    func send<T: Decodable>(_ method: String, _ path: String, query: [String: String] = [:], body: JSONValue? = nil, as type: T.Type = T.self) async throws -> T {
        let (data, _) = try await data(method, path, query: query, body: body)
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError(status: 0, message: "The server's answer wasn't what the app expected (\(path)). Is it a labb server, and up to date?")
        }
    }

    func get<T: Decodable>(_ path: String, query: [String: String] = [:], as type: T.Type = T.self) async throws -> T {
        try await send("GET", path, query: query, as: type)
    }

    /// A request whose answer doesn't matter (204s, deletes)
    func perform(_ method: String, _ path: String, query: [String: String] = [:], body: JSONValue? = nil) async throws {
        _ = try await data(method, path, query: query, body: body)
    }

    /// Newline-delimited JSON events, until the stream ends. Nothing when the server answers 204
    /// (no reply to join). Cancelling the task ends the request.
    func events(_ method: String, _ path: String, query: [String: String] = [:], body: JSONValue? = nil) -> AsyncThrowingStream<ChatEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let request = try self.request(method, path, query: query, body: body)
                    let (bytes, response) = try await self.session.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else { throw APIError(status: 0, message: "No answer from the server") }
                    if !(200 ..< 300).contains(http.statusCode) {
                        var data = Data()
                        for try await byte in bytes { data.append(byte) }
                        throw self.failure(data, http, path: path)
                    }
                    if http.statusCode != 204 {
                        let decoder = JSONDecoder()
                        for try await line in bytes.lines where !line.trimmingCharacters(in: .whitespaces).isEmpty {
                            if let event = try? decoder.decode(ChatEvent.self, from: Data(line.utf8)) {
                                continuation.yield(event)
                            }
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// Forget the session: the server's and the cookie
    func clearCookies() {
        guard let cookies = HTTPCookieStorage.shared.cookies(for: base) else { return }
        for cookie in cookies { HTTPCookieStorage.shared.deleteCookie(cookie) }
    }
}

/// Whether an error is the connection going (worth trying again), not the server refusing
func isConnectionLost(_ error: Error) -> Bool {
    if error is APIError { return false }
    if error is CancellationError { return false }
    if let urlError = error as? URLError { return urlError.code != .cancelled }
    return true
}
