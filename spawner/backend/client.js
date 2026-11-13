// An app's API, from its pages: sign-in for its own users, and its tables with the signed-in user's token.
// Served by the API itself, so it's always the one that matches it. In a page (a module script):
//
//   const api = await import(`${location.protocol}//${location.hostname}:${Number(location.port) + 100}/client.js`)
//   await api.signIn(email, password)            // or api.signUp(...)
//   const notes = await api.from('notes').select('select=*&order=id')
//   await api.from('notes').insert({ body: 'Hello' })
//   api.onUser(user => render(user))             // signed in or out, here or in another tab
//
// Tokens are kept in localStorage (per API); an expired access token is refreshed on its own.

const base = new URL('.', import.meta.url).href.replace(/\/$/, '')
const key = `labb-auth:${base}`
const listeners = new Set()
let session = load()
let refreshing = null

function load() {
  try {
    const found = JSON.parse(localStorage.getItem(key))
    return found && found.refresh_token ? found : null
  } catch {
    return null
  }
}

function save(next) {
  session = next
  try {
    if (next) localStorage.setItem(key, JSON.stringify(next))
    else localStorage.removeItem(key)
  } catch {}
  for (const listener of listeners) listener(user())
}

addEventListener('storage', (event) => {
  if (event.key !== key) return
  session = load()
  for (const listener of listeners) listener(user())
})

export class ApiError extends Error {
  constructor(status, body) {
    super((body && (body.message || body.hint)) || `The API said ${status}`)
    this.status = status
    this.body = body
  }
}

async function call(path, { method = 'GET', body, headers = {}, token } = {}) {
  const sent = { ...headers }
  if (body !== undefined) sent['Content-Type'] = 'application/json'
  if (token) sent.Authorization = `Bearer ${token}`
  const response = await fetch(base + path, { method, headers: sent, body: body === undefined ? undefined : JSON.stringify(body) })
  const text = await response.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!response.ok) throw new ApiError(response.status, data)
  return data
}

function keep(tokens) {
  save({ access_token: tokens.access_token, refresh_token: tokens.refresh_token, expires_at: tokens.expires_at, user: tokens.user })
  return tokens.user
}

/** The API's address (…:9200) */
export const url = base

/** A new account, signed in: its user ({id, email, …}) */
export async function signUp(email, password) {
  return keep(await call('/auth/signup', { method: 'POST', body: { email, password } }))
}

/** Signed in: the user */
export async function signIn(email, password) {
  return keep(await call('/auth/signin', { method: 'POST', body: { email, password } }))
}

/** Signed out, here and on the server */
export async function signOut() {
  const was = session
  save(null)
  if (was) {
    try {
      await call('/auth/signout', { method: 'POST', body: { refresh_token: was.refresh_token } })
    } catch {}
  }
}

/** Who's signed in (as last known here), or null */
export function user() {
  return session ? session.user : null
}

/** Who's signed in, asked of the server (null if no one, or their sign-in has ended) */
export async function currentUser() {
  const access = await token()
  if (!access) return null
  try {
    return await call('/auth/user', { token: access })
  } catch (e) {
    if (e.status === 401) {
      save(null)
      return null
    }
    throw e
  }
}

/** The password changed (the one now first); their other sign-ins end */
export async function changePassword(password, newPassword) {
  await call('/auth/password', { method: 'POST', body: { password, new_password: newPassword }, token: await token() })
}

/** fn(user or null) when someone signs in or out; returns a function that stops it */
export function onUser(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/** The access token, refreshed if it's about to expire; null when no one's signed in */
export async function token() {
  if (!session) return null
  if (session.expires_at - 30 > Date.now() / 1000) return session.access_token
  if (!refreshing) {
    const current = session
    refreshing = (async () => {
      // Another tab may have refreshed already
      const stored = load()
      if (stored && stored.refresh_token !== current.refresh_token && stored.expires_at - 30 > Date.now() / 1000) {
        session = stored
        return
      }
      try {
        const fresh = await call('/auth/refresh', { method: 'POST', body: { refresh_token: current.refresh_token } })
        if (session === current) keep(fresh)
      } catch (e) {
        if (e.status === 401 && session === current) save(null)
        else if (e.status !== 401) throw e
      }
    })().finally(() => {
      refreshing = null
    })
  }
  await refreshing
  return session ? session.access_token : null
}

/** A request to /rest/<path> (PostgREST), as the signed-in user if there is one */
export async function rest(path, options = {}) {
  const access = await token()
  try {
    return await call('/rest/' + String(path).replace(/^\/+/, ''), { ...options, token: access })
  } catch (e) {
    // Signed out elsewhere, or disabled by the app's owner
    const code = String((e.body && e.body.code) || '')
    if (e.status === 401 && access && (code === 'PT401' || code.startsWith('PGRST30'))) save(null)
    throw e
  }
}

/** A table: select('select=*&done=eq.false&order=id'), insert(row or rows), update('id=eq.3', changes), remove('id=eq.3') */
export function from(table) {
  const at = (query) => encodeURIComponent(table) + (query ? `?${query}` : '')
  const filtered = (filter) => {
    if (!filter || !String(filter).trim()) throw new Error('update and remove need a filter, e.g. "id=eq.3"')
    return at(filter)
  }
  const returning = (options) => ({ Prefer: options && options.returning === false ? 'return=minimal' : 'return=representation' })
  return {
    select: (query = 'select=*') => rest(at(query)),
    insert: (rows, options) => rest(at(''), { method: 'POST', body: rows, headers: returning(options) }),
    update: (filter, changes, options) => rest(filtered(filter), { method: 'PATCH', body: changes, headers: returning(options) }),
    remove: (filter, options) => rest(filtered(filter), { method: 'DELETE', headers: returning(options) }),
  }
}

/** A function of the database's (granted to the API): rpc('add_vote', {item: 3}) */
export function rpc(name, args = {}) {
  return rest(`rpc/${encodeURIComponent(name)}`, { method: 'POST', body: args })
}
