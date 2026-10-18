/** Validate every token destination before saving or sending credentials. */
function validateSession(session) {
  const target = new URL(session.origin);
  if (target.protocol !== 'http:' || target.hostname !== '127.0.0.1' || target.username || target.password ||
      target.pathname !== '/' || target.search || target.hash || typeof session.token !== 'string' || !session.token) {
    throw new Error('Ungültige Helfer-Sitzung. Bitte den Windows-Helfer erneut starten.');
  }
  return {origin: target.origin, token: session.token};
}

/** Keep the validated helper session in this project tab only. */
export function saveSession(session, location, storage) {
  session = validateSession(session);
  storage.setItem(`x6-session:${location.pathname}`, JSON.stringify(session));
  return session;
}

/** Recover a helper-launched session or one paired by a direct Pages visit. */
export function sessionFromLocation(location, storage) {
  const fragment = new URLSearchParams(location.hash.slice(1));
  const session = fragment.has('session') ? {token: fragment.get('session'), origin: fragment.get('bridge') || location.origin} :
    JSON.parse(storage.getItem(`x6-session:${location.pathname}`) || 'null');
  return session ? saveSession(session, location, storage) : null;
}

/** Pair on a Connect click; this endpoint does not query or switch any USB device. */
export async function pairHelper(origin, timeoutMs) {
  const target = validateSession({origin, token: 'validate-destination'}).origin;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${target}/api/pair`, {
      method: 'POST', headers: {'X-X6-Pair': '1'}, credentials: 'omit', redirect: 'error', signal: controller.signal,
    });
    if (!response.ok) throw new Error('Bitte den aktuellen Windows-Helfer herunterladen und starten.');
    const session = validateSession(await response.json());
    if (session.origin !== target) throw new Error('Die App hat eine abweichende Sitzungsadresse geliefert.');
    return session;
  } catch (error) {
    throw new Error(`Die Windows-App konnte nicht gekoppelt werden. Bitte den aktuellen Helper starten und Chrome den Zugriff auf die lokale App erlauben. ${error.message}`, {cause: error});
  } finally { clearTimeout(timer); }
}

/** The session token goes only to loopback, never to the public web server. */
export async function bridgeRequest(session, path, method = 'GET', binary = false) {
  if (!session) throw new Error('Bitte zuerst „DDMY X6 Helper.exe“ aus dem Windows-Helfer starten. Er öffnet diese Seite mit einer lokalen Sitzung.');
  const response = await fetch(`${session.origin}/api/${path}`, {
    method, headers: {'X-X6-Session': session.token}, credentials: 'omit', redirect: 'error',
  });
  if (!response.ok) throw new Error((await response.json()).error || 'Der lokale Helfer antwortet nicht.');
  return binary ? new Uint8Array(await response.arrayBuffer()) : response.json();
}
